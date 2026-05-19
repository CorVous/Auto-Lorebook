"""High-level orchestration of the Stage 1 reading pipeline.

Exposes `generate`, `approve`, `regenerate`, and `assemble_draft` entry
points used by the three reading subcommands. The command modules handle
argparse; this module handles the wiring between stages.

State is now stored in wiki.db (ingests/segments/segment_bullets tables).
Pending YAML files (structure.yaml, bullets.yaml, reading.yaml, seg-NNN.md)
are no longer written by this module.

Hard-cutover: if a pre-existing YAML reading state is detected alongside a
missing ingests row, a ReadingPipelineError is raised with a remediation hint.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import (
    corrections as corrections_mod,
)
from auto_lorebook import db as db_mod
from auto_lorebook import entities as entities_mod
from auto_lorebook import entity_yaml as entity_yaml_mod
from auto_lorebook import gap_check as gap_check_mod
from auto_lorebook import info_yaml as info_yaml_mod
from auto_lorebook import plan_yaml as plan_yaml_mod
from auto_lorebook import preamble as preamble_mod
from auto_lorebook import proposal_yaml as proposal_yaml_mod
from auto_lorebook import reading as reading_mod
from auto_lorebook import reading_assembly as reading_assembly_mod
from auto_lorebook import reading_sidecar as sidecar_mod
from auto_lorebook import source_store as source_store_mod
from auto_lorebook import stage1a as stage1a_mod
from auto_lorebook import stage1b as stage1b_mod
from auto_lorebook import stage2 as stage2_mod
from auto_lorebook import stage3 as stage3_mod
from auto_lorebook import structure as structure_mod
from auto_lorebook import structure_store as structure_store_mod
from auto_lorebook import transcript as transcript_mod
from auto_lorebook import wiki_context as wiki_context_mod
from auto_lorebook import wiki_state as wiki_state_mod
from auto_lorebook.openrouter import OpenRouterClient, OpenRouterError

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from auto_lorebook.gap_check import GapWarning
    from auto_lorebook.info_yaml import Info
    from auto_lorebook.plan_yaml import Plan
    from auto_lorebook.proposal_yaml import Proposal
    from auto_lorebook.reading_review import RegenBatch
    from auto_lorebook.reading_sidecar import IngestState

_logger = logging.getLogger(__name__)


class ReadingPipelineError(RuntimeError):
    """Raised for any user-facing failure in the reading pipeline."""


@dataclass
class GenerateResult:
    """Result of a generate/regenerate run."""

    ingest_id: str
    segments_count: int
    bullets_count: int
    gap_warnings: list[GapWarning]


@dataclass
class PlanResult:
    """Plan written by a Stage 2 run."""

    plan_path: Path
    plan: Plan


@dataclass
class ExtractResult:
    """Proposals written by a Stage 3 run."""

    proposals_dir: Path
    proposals: list[Proposal]
    flagged_count: int


def generate(
    cfg: cfg_mod.Config,
    source_id: str,
    wiki_override: str | None = None,
) -> GenerateResult:
    """Run Stage 1a + 1b from scratch and store in DB."""
    return _run_full(cfg, source_id, wiki_override=wiki_override)


def regenerate(
    cfg: cfg_mod.Config,
    source_id: str,
    *,
    from_stage: str,
    segment_ids: list[str] | None = None,
    wiki_override: str | None = None,
) -> GenerateResult:
    """Re-run from the given stage, preserving name_corrections + selective bullets."""
    if from_stage == "structure":
        if segment_ids is not None:
            msg = "--segments is only valid with --from=summarize"
            raise ReadingPipelineError(msg)
        return _run_full(cfg, source_id, wiki_override=wiki_override)
    if from_stage == "summarize":
        return _run_summarize_only(
            cfg, source_id, segment_ids=segment_ids, wiki_override=wiki_override
        )
    msg = f"unknown --from value: {from_stage!r} (expected structure|summarize)"
    raise ReadingPipelineError(msg)


def regenerate_after_review(
    cfg: cfg_mod.Config,
    batch: RegenBatch,
    wiki_override: str | None = None,
) -> GenerateResult:
    """Re-run Stage 1b on regenerating segments after a quit-commit.

    Injects accepted-segments context; rewrites bullets and resets statuses to draft.
    """
    source_id = batch.source_id
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        _check_no_stale_yaml(wiki_repo, source_id, conn)
        if not sidecar_mod.exists(conn, source_id):
            msg = (
                f"No prior reading state for {source_id!r}. "
                "Run `regenerate-reading --from=structure` or `generate-reading` first."
            )
            raise ReadingPipelineError(msg)
        try:
            structure = structure_store_mod.read_structure(conn, source_id)
        except structure_store_mod.StructureStoreError as e:
            raise ReadingPipelineError(str(e)) from e

        _info, ctx = _load_context_with_conn(cfg, source_id, conn, wiki_repo)
        client = _build_client(cfg)
        model = cfg.models.primary

        existing_bullets = structure_store_mod.read_bullets(conn, source_id)
        new_bullets = stage1b_mod.run(
            transcript=ctx.transcript,
            structure=structure,
            preamble_text=ctx.preamble_text,
            client=client,
            model=model,
            segment_ids=list(batch.regen_segment_ids),
            accepted_context=list(batch.accepted_context),
        )
        # rebuilt segments overwrite; untouched segments keep existing bullets
        merged = existing_bullets.segments.copy()
        for sid, bullets_list in new_bullets.segments.items():
            merged[sid] = bullets_list
        for seg in structure.segments:
            merged.setdefault(seg.id, [])
        new_bullets.segments = merged

        structure_store_mod.write_bullets(conn, source_id, new_bullets)

        # reset status of regen segments to draft
        for seg_id in batch.regen_segment_ids:
            structure_store_mod.set_segment_status(conn, source_id, seg_id, "draft")

        warnings = gap_check_mod.check(structure)
        existing_sc = _load_existing_state(conn, source_id)
        name_corrections = existing_sc.name_corrections if existing_sc else {}
        session_date = existing_sc.session_date if existing_sc else None
        sidecar_mod.write_state(
            conn,
            source_id,
            default_speaker=structure.default_speaker,
            name_corrections=name_corrections,
            session_date=session_date,
        )
        conn.commit()
        return GenerateResult(
            ingest_id=source_id,
            segments_count=len(structure.segments),
            bullets_count=sum(len(bl) for bl in new_bullets.segments.values()),
            gap_warnings=warnings,
        )
    finally:
        conn.close()


def approve(
    cfg: cfg_mod.Config,
    source_id: str,
    wiki_override: str | None = None,
) -> Path:
    """Auto-accept all draft segments via the reading-review engine.

    :raises ReadingPipelineError: sidecar missing, engine error, or gate did
        not fire (all segments must be decidable).
    """
    from auto_lorebook import reading_review as reading_review_mod  # noqa: PLC0415
    from auto_lorebook.commands.approve_reading import (  # noqa: PLC0415
        AutoAcceptReviewer,
    )

    try:
        result = reading_review_mod.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=AutoAcceptReviewer(),
            wiki_override=wiki_override,
        )
    except reading_review_mod.ReadingReviewError as e:
        raise ReadingPipelineError(str(e)) from e

    if not result.gate_fired or result.wiki_reading_path is None:
        msg = (
            f"Reading review for {source_id!r} did not fire the gate; "
            "not all segments could be decided."
        )
        raise ReadingPipelineError(msg)

    return result.wiki_reading_path


def assemble_draft(
    cfg: cfg_mod.Config,
    source_id: str,
    wiki_override: str | None = None,
) -> str:
    """Assemble current segment state into a draft preview string."""
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        _check_no_stale_yaml(wiki_repo, source_id, conn)
        if not sidecar_mod.exists(conn, source_id):
            msg = f"No draft reading for {source_id!r}. Run `generate-reading` first."
            raise ReadingPipelineError(msg)
        try:
            info = info_yaml_mod.read(conn, source_id, wiki_repo=wiki_repo)
        except info_yaml_mod.InfoError as e:
            raise ReadingPipelineError(str(e)) from e
        try:
            sc = sidecar_mod.read_state(conn, source_id)
        except sidecar_mod.ReadingSidecarError as e:
            raise ReadingPipelineError(str(e)) from e
        return reading_assembly_mod.assemble(
            conn=conn, ingest_id=source_id, info=info, sidecar=sc
        )
    finally:
        conn.close()


def _active_wiki_root() -> Path:
    """Resolve active wiki from loaded config."""
    return cfg_mod.load_config().resolve_active_wiki(None)


def pending_dir(source_id: str) -> Path:
    """Return the reading pending dir for a source under the active wiki."""
    return wiki_state_mod.pending_reading_dir(_active_wiki_root(), source_id)


def pending_sidecar_path(source_id: str) -> Path:
    return pending_dir(source_id) / "reading.yaml"


def pending_segments_dir(source_id: str) -> Path:
    return pending_dir(source_id) / "segments"


def pending_segment_path(source_id: str, segment_id: str) -> Path:
    return pending_segments_dir(source_id) / f"{segment_id}.md"


def pending_structure_path(source_id: str) -> Path:
    return pending_dir(source_id) / "structure.yaml"


def pending_bullets_path(source_id: str) -> Path:
    return pending_dir(source_id) / "bullets.yaml"


def pending_plan_path(source_id: str) -> Path:
    """Plan artifact path under the active wiki's .wiki-state/."""
    return wiki_state_mod.pending_plan_path(_active_wiki_root(), source_id)


def pending_proposals_dir(source_id: str) -> Path:
    """Stage 3 proposal directory under the active wiki's .wiki-state/."""
    return wiki_state_mod.pending_proposals_dir(_active_wiki_root(), source_id)


def pending_proposal_path(source_id: str, proposal_id: str) -> Path:
    return pending_proposals_dir(source_id) / f"{proposal_id}.yaml"


def plan(
    cfg: cfg_mod.Config,
    source_id: str,
    wiki_override: str | None = None,
) -> PlanResult:
    """Run Stage 2 on an approved reading and write `plan.yaml`.

    :raises ReadingPipelineError: reading not approved, prior pipeline
        artifacts missing, or planner / preamble failure.
    """
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    approved_path = wiki_repo / "sources" / source_id / "reading.md"
    if not approved_path.exists():
        msg = (
            f"No approved reading at {approved_path}. "
            f"Run `approve-reading {source_id}` first."
        )
        raise ReadingPipelineError(msg)

    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        try:
            info = info_yaml_mod.read(conn, source_id, wiki_repo=wiki_repo)
        except info_yaml_mod.InfoError as e:
            raise ReadingPipelineError(str(e)) from e
        wc = wiki_context_mod.read(conn, wiki_repo=wiki_repo)
        cors = corrections_mod.read(conn, wiki_repo=wiki_repo)
        entity_snippet = entities_mod.render_for_preamble(conn, wiki_repo)

        try:
            structure = structure_store_mod.read_structure(conn, source_id)
        except structure_store_mod.StructureStoreError as e:
            msg = (
                f"Missing prior structure for {source_id!r}. "
                "Re-run `regenerate-reading` to repopulate."
            )
            raise ReadingPipelineError(msg) from e
        try:
            bullets = structure_store_mod.read_bullets(conn, source_id)
        except Exception as e:
            msg = (
                f"Missing prior bullets for {source_id!r}. "
                "Re-run `regenerate-reading` to repopulate."
            )
            raise ReadingPipelineError(msg) from e
    finally:
        conn.close()

    p = preamble_mod.assemble(info, wc, cors, entity_snippet, reduced=False)
    try:
        p.check_budget(
            context_window=cfg.models.primary_context_window,
            budget_fraction=cfg.preamble.budget_fraction,
        )
    except preamble_mod.PreambleTooLargeError as e:
        raise ReadingPipelineError(str(e)) from e

    client = _build_client(cfg)
    model = cfg.models.planner or cfg.models.primary

    reading_text = approved_path.read_text(encoding="utf-8")
    try:
        result_plan = stage2_mod.run(
            reading_text=reading_text,
            structure=structure,
            bullets=bullets,
            preamble_text=p.text,
            source_id=source_id,
            client=client,
            model=model,
        )
    except stage2_mod.Stage2Error as e:
        raise ReadingPipelineError(str(e)) from e

    # write to DB (primary) and YAML (legacy compat)
    conn2 = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        plan_yaml_mod.write_plan_routes(conn2, source_id, result_plan)
        conn2.commit()
    finally:
        conn2.close()
    plan_path = pending_plan_path(source_id)
    plan_yaml_mod.write(result_plan, plan_path)
    return PlanResult(plan_path=plan_path, plan=result_plan)


def extract(
    cfg: cfg_mod.Config,
    source_id: str,
    wiki_override: str | None = None,
) -> ExtractResult:
    """Run Stage 3 against an existing plan and write proposal yamls.

    :raises ReadingPipelineError: missing plan, plain-text source, or
        Stage 3 failure (bad LLM output, schema violation).
    """
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    plan_path = pending_plan_path(source_id)
    if not plan_path.exists():
        msg = f"No plan at {plan_path}. Run `plan {source_id}` first."
        raise ReadingPipelineError(msg)
    try:
        plan_obj = plan_yaml_mod.read(plan_path)
    except plan_yaml_mod.PlanError as e:
        raise ReadingPipelineError(str(e)) from e

    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        try:
            info = info_yaml_mod.read(conn, source_id, wiki_repo=wiki_repo)
        except info_yaml_mod.InfoError as e:
            raise ReadingPipelineError(str(e)) from e
        cors = corrections_mod.read(conn, wiki_repo=wiki_repo)
        wc = wiki_context_mod.read(conn, wiki_repo=wiki_repo)
        entity_snippet = entities_mod.render_for_preamble(conn, wiki_repo)
        try:
            loaded = transcript_mod.load(wiki_repo, info, cors)
        except transcript_mod.TranscriptError as e:
            raise ReadingPipelineError(str(e)) from e

        try:
            structure = structure_store_mod.read_structure(conn, source_id)
        except structure_store_mod.StructureStoreError as e:
            msg = (
                f"No structure for {source_id!r}; "
                "run `regenerate-reading` to repopulate."
            )
            raise ReadingPipelineError(msg) from e

        p = preamble_mod.assemble(info, wc, cors, entity_snippet, reduced=True)
        try:
            p.check_budget(
                context_window=cfg.models.primary_context_window,
                budget_fraction=cfg.preamble.budget_fraction,
            )
        except preamble_mod.PreambleTooLargeError as e:
            raise ReadingPipelineError(str(e)) from e

        existing_fact_counts, existing_slugs = _collect_existing_target_metadata(
            wiki_repo, plan_obj, conn
        )
    finally:
        conn.close()

    client = _build_client(cfg)
    model = cfg.models.extractor or cfg.models.primary

    try:
        proposals = stage3_mod.run(
            plan=plan_obj,
            transcript=loaded,
            structure=structure,
            info=info,
            preamble_text=p.text,
            source_id=source_id,
            client=client,
            model=model,
            existing_fact_counts=existing_fact_counts,
            existing_slugs=existing_slugs,
        )
    except stage3_mod.Stage3Error as e:
        raise ReadingPipelineError(str(e)) from e

    # write proposals to DB (primary) and YAML files (legacy compat)
    conn3 = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        proposal_yaml_mod.delete_all_for_ingest(conn3, source_id)
        for proposal in proposals:
            proposal_yaml_mod.write_proposal(conn3, source_id, proposal)
        conn3.commit()
    finally:
        conn3.close()

    proposals_dir = pending_proposals_dir(source_id)
    if proposals_dir.exists():
        shutil.rmtree(proposals_dir)
    proposals_dir.mkdir(parents=True, exist_ok=True)
    for proposal in proposals:
        proposal_yaml_mod.write(
            proposal, pending_proposal_path(source_id, proposal.proposed_id)
        )
    flagged = sum(1 for p in proposals if p.extractor_flagged)
    return ExtractResult(
        proposals_dir=proposals_dir,
        proposals=proposals,
        flagged_count=flagged,
    )


def _collect_existing_target_metadata(
    wiki_repo: Path,
    plan_obj: Plan,
    conn: sqlite3.Connection,
) -> tuple[dict[str, int], dict[str, str]]:
    """Per-target fact counts and slugs for entities resolving to disk."""
    fact_counts: dict[str, int] = {}
    slugs: dict[str, str] = {}
    for claim in plan_obj.planned_claims:
        for target in claim.targets:
            if target.entity in fact_counts:
                continue
            if target.entity_state == "new":
                continue
            entry = entities_mod.lookup_by_planner_name(conn, target.entity)
            if entry is None:
                continue
            entity_path = wiki_repo / entry.category / f"{entry.slug}.yaml"
            try:
                entity = entity_yaml_mod.read(entity_path)
            except entity_yaml_mod.EntityError:
                continue
            fact_counts[target.entity] = len(entity.facts)
            slugs[target.entity] = entity.slug
    return fact_counts, slugs


def _check_no_stale_yaml(
    wiki_repo: Path,
    source_id: str,
    conn: sqlite3.Connection,
) -> None:
    """Fail loudly if pre-existing YAML reading state exists without a DB row.

    Hard-cutover: no lazy backfill. If a human has old YAML state and no DB
    row, they must run regenerate-reading --from=structure to rebuild.
    """
    pending_yaml = (
        wiki_state_mod.pending_reading_dir(wiki_repo, source_id) / "reading.yaml"
    )
    if pending_yaml.exists() and not sidecar_mod.exists(conn, source_id):
        msg = (
            f"Pre-existing YAML reading state detected at {pending_yaml}. "
            f"Run `auto-lorebook regenerate-reading {source_id} --from=structure` "
            "to rebuild from transcript into the DB."
        )
        raise ReadingPipelineError(msg)


def _run_full(
    cfg: cfg_mod.Config,
    source_id: str,
    wiki_override: str | None = None,
) -> GenerateResult:
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        _check_no_stale_yaml(wiki_repo, source_id, conn)
        loaded_info, ctx = _load_context_with_conn(cfg, source_id, conn, wiki_repo)
        # ensure sources + ingests rows exist (idempotent if already seeded)
        source_store_mod.record_in_db(
            conn, loaded_info, source_id, loaded_info.source_type
        )
        client = _build_client(cfg)
        model = cfg.models.primary

        structure = stage1a_mod.run(
            transcript=ctx.transcript,
            preamble_text=ctx.preamble_text,
            source_id=source_id,
            client=client,
            model=model,
        )
        structure_store_mod.write_structure(conn, source_id, structure)

        warnings = gap_check_mod.check(structure)

        bullets = stage1b_mod.run(
            transcript=ctx.transcript,
            structure=structure,
            preamble_text=ctx.preamble_text,
            client=client,
            model=model,
        )
        for seg in structure.segments:
            bullets.segments.setdefault(seg.id, [])
        structure_store_mod.write_bullets(conn, source_id, bullets)

        existing_sc = _load_existing_state(conn, source_id)
        name_corrections = existing_sc.name_corrections if existing_sc else {}
        session_date = existing_sc.session_date if existing_sc else None
        sidecar_mod.write_state(
            conn,
            source_id,
            default_speaker=structure.default_speaker,
            name_corrections=name_corrections,
            session_date=session_date,
        )
        conn.commit()
        return GenerateResult(
            ingest_id=source_id,
            segments_count=len(structure.segments),
            bullets_count=sum(len(bl) for bl in bullets.segments.values()),
            gap_warnings=warnings,
        )
    finally:
        conn.close()


def _run_summarize_only(
    cfg: cfg_mod.Config,
    source_id: str,
    *,
    segment_ids: list[str] | None,
    wiki_override: str | None = None,
) -> GenerateResult:
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        _check_no_stale_yaml(wiki_repo, source_id, conn)
        if not sidecar_mod.exists(conn, source_id):
            msg = (
                f"No prior reading state for {source_id!r}. "
                "Run `regenerate-reading --from=structure` or `generate-reading` first."
            )
            raise ReadingPipelineError(msg)

        try:
            structure = structure_store_mod.read_structure(conn, source_id)
        except structure_store_mod.StructureStoreError as e:
            raise ReadingPipelineError(str(e)) from e

        _info, ctx = _load_context_with_conn(cfg, source_id, conn, wiki_repo)
        client = _build_client(cfg)
        model = cfg.models.primary

        existing_bullets = structure_store_mod.read_bullets(conn, source_id)
        new_bullets = stage1b_mod.run(
            transcript=ctx.transcript,
            structure=structure,
            preamble_text=ctx.preamble_text,
            client=client,
            model=model,
            segment_ids=segment_ids,
        )
        # rebuilt segments overwrite; untouched segments keep existing bullets
        merged = existing_bullets.segments.copy()
        for sid, bullets_list in new_bullets.segments.items():
            merged[sid] = bullets_list
        for seg in structure.segments:
            merged.setdefault(seg.id, [])
        new_bullets.segments = merged
        structure_store_mod.write_bullets(conn, source_id, new_bullets)

        warnings = gap_check_mod.check(structure)
        existing_sc = _load_existing_state(conn, source_id)
        name_corrections = existing_sc.name_corrections if existing_sc else {}
        session_date = existing_sc.session_date if existing_sc else None
        sidecar_mod.write_state(
            conn,
            source_id,
            default_speaker=structure.default_speaker,
            name_corrections=name_corrections,
            session_date=session_date,
        )
        conn.commit()
        return GenerateResult(
            ingest_id=source_id,
            segments_count=len(structure.segments),
            bullets_count=sum(len(bl) for bl in new_bullets.segments.values()),
            gap_warnings=warnings,
        )
    finally:
        conn.close()


@dataclass
class _Context:
    transcript: transcript_mod.LoadedTranscript
    preamble_text: str


def _load_context_with_conn(
    cfg: cfg_mod.Config,
    source_id: str,
    conn: sqlite3.Connection,
    wiki_repo: Path,
) -> tuple[Info, _Context]:
    """Load context using an already-open connection."""
    try:
        info = info_yaml_mod.read(conn, source_id, wiki_repo=wiki_repo)
    except info_yaml_mod.InfoError as e:
        raise ReadingPipelineError(str(e)) from e
    wc = wiki_context_mod.read(conn, wiki_repo=wiki_repo)
    cors = corrections_mod.read(conn, wiki_repo=wiki_repo)
    entity_snippet = entities_mod.render_for_preamble(conn, wiki_repo)

    try:
        loaded = transcript_mod.load(wiki_repo, info, cors)
    except transcript_mod.TranscriptError as e:
        raise ReadingPipelineError(str(e)) from e

    p = preamble_mod.assemble(info, wc, cors, entity_snippet, reduced=False)
    try:
        p.check_budget(
            context_window=cfg.models.primary_context_window,
            budget_fraction=cfg.preamble.budget_fraction,
        )
    except preamble_mod.PreambleTooLargeError as e:
        raise ReadingPipelineError(str(e)) from e
    return info, _Context(transcript=loaded, preamble_text=p.text)


def _load_context(
    cfg: cfg_mod.Config,
    source_id: str,
    wiki_override: str | None = None,
) -> tuple[Info, _Context]:
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        return _load_context_with_conn(cfg, source_id, conn, wiki_repo)
    finally:
        conn.close()


def _build_client(cfg: cfg_mod.Config) -> OpenRouterClient:
    api_key = cfg.get_api_key()
    if not api_key:
        msg = (
            "OpenRouter API key not found. Either export "
            f"${cfg.openrouter.api_key_env} or store the key in "
            "~/.auto-lorebook/credentials (the interactive ingest setup "
            "writes this for you)."
        )
        raise ReadingPipelineError(msg)
    try:
        return OpenRouterClient(
            api_key=api_key,
            default_model=cfg.models.primary,
            app_title="auto-lorebook",
        )
    except OpenRouterError as e:
        raise ReadingPipelineError(str(e)) from e


def _load_existing_state(
    conn: sqlite3.Connection, source_id: str
) -> IngestState | None:
    """Load existing IngestState if present, else None."""
    if not sidecar_mod.exists(conn, source_id):
        return None
    try:
        return sidecar_mod.read_state(conn, source_id)
    except sidecar_mod.ReadingSidecarError:
        return None


def _flags_by_segment(
    structure: structure_mod.Structure,
) -> dict[str, list[structure_mod.UncertaintyFlag]]:
    out: dict[str, list[structure_mod.UncertaintyFlag]] = {}
    for flag in structure.uncertainty_flags:
        for seg in structure.segments:
            if seg.start <= flag.locator <= seg.end:
                out.setdefault(seg.id, []).append(flag)
                break
    return out


def build_segment_body(
    _seg: structure_mod.Segment,
    bullets: list[stage1b_mod.Bullet],
    flags: list[structure_mod.UncertaintyFlag],
    source_url: str | None,
    name_corrections: dict[str, str],
) -> str:
    """Render segment body: uncertainty flags + bullets (or empty marker)."""
    from auto_lorebook.timestamps import format_timestamp  # noqa: PLC0415

    parts: list[str] = []
    for flag in flags:
        ts = format_timestamp(flag.locator)
        note = f"; {flag.note}" if flag.note else ""
        parts.append(f"- [{ts}] uncertain {flag.kind}: {flag.span}{note}")
    if not bullets:
        parts.append("_No claims extracted from this segment._")
    else:
        for b in bullets:
            text = reading_mod.apply_name_corrections(b.text, name_corrections)
            anchor_ts = format_timestamp(b.anchor)
            link = reading_mod.linkify_timestamp(source_url, b.anchor)
            if link:
                parts.append(f"- {text} [[{anchor_ts}]]({link})")
            else:
                parts.append(f"- {text} [{anchor_ts}]")
    return "\n\n".join(parts) + "\n"
