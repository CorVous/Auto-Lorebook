"""High-level orchestration of the Stage 1 reading pipeline.

Exposes `generate`, `approve`, and `regenerate` entry points used by
the three reading subcommands. The command modules handle argparse;
this module handles the wiring between stages.
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
from auto_lorebook import (
    entity_index as entity_index_mod,
)
from auto_lorebook import entity_yaml as entity_yaml_mod
from auto_lorebook import gap_check as gap_check_mod
from auto_lorebook import info_yaml as info_yaml_mod
from auto_lorebook import plan_yaml as plan_yaml_mod
from auto_lorebook import preamble as preamble_mod
from auto_lorebook import proposal_yaml as proposal_yaml_mod
from auto_lorebook import reading as reading_mod
from auto_lorebook import stage1a as stage1a_mod
from auto_lorebook import stage1b as stage1b_mod
from auto_lorebook import stage2 as stage2_mod
from auto_lorebook import stage3 as stage3_mod
from auto_lorebook import structure as structure_mod
from auto_lorebook import transcript as transcript_mod
from auto_lorebook import wiki_context as wiki_context_mod
from auto_lorebook.openrouter import OpenRouterClient, OpenRouterError

if TYPE_CHECKING:
    from pathlib import Path

    from auto_lorebook.gap_check import GapWarning
    from auto_lorebook.info_yaml import Info
    from auto_lorebook.plan_yaml import Plan
    from auto_lorebook.proposal_yaml import Proposal

_logger = logging.getLogger(__name__)


class ReadingPipelineError(RuntimeError):
    """Raised for any user-facing failure in the reading pipeline."""


@dataclass
class GenerateResult:
    """Paths and warnings produced by a generate/regenerate run."""

    pending_reading_path: Path
    structure_path: Path
    bullets_path: Path
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


@dataclass
class ApproveAndExtractResult:
    """Combined result of approve → plan → extract."""

    approved_path: Path
    plan_result: PlanResult
    extract_result: ExtractResult


def generate(cfg: cfg_mod.Config, source_id: str) -> GenerateResult:
    """Run Stage 1a + 1b from scratch and write the draft reading.md."""
    return _run_full(cfg, source_id)


def regenerate(
    cfg: cfg_mod.Config,
    source_id: str,
    *,
    from_stage: str,
    segment_ids: list[str] | None = None,
) -> GenerateResult:
    """Re-run from the given stage, preserving name_corrections + selective bullets."""
    if from_stage == "structure":
        if segment_ids is not None:
            msg = "--segments is only valid with --from=summarize"
            raise ReadingPipelineError(msg)
        return _run_full(cfg, source_id)
    if from_stage == "summarize":
        return _run_summarize_only(cfg, source_id, segment_ids=segment_ids)
    msg = f"unknown --from value: {from_stage!r} (expected structure|summarize)"
    raise ReadingPipelineError(msg)


def approve(cfg: cfg_mod.Config, source_id: str) -> Path:
    """Flip the draft to approved and copy it into the wiki."""
    pending_path = pending_reading_path(source_id)
    try:
        approved_text = reading_mod.with_status(pending_path, "approved")
    except FileNotFoundError as e:
        msg = f"No draft reading for {source_id!r}. Run `generate-reading` first."
        raise ReadingPipelineError(msg) from e
    reading_mod.write(pending_path, approved_text)
    dest = cfg.wiki_repo_path / "sources" / source_id / "reading.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    reading_mod.write(dest, approved_text)
    return dest


def approve_and_extract(cfg: cfg_mod.Config, source_id: str) -> ApproveAndExtractResult:
    """Approve reading, run Stage 2 plan, run Stage 3 extract.

    :raises ReadingPipelineError: any stage failure.
    """
    approved_path = approve(cfg, source_id)
    plan_result = plan(cfg, source_id)
    extract_result = extract(cfg, source_id)
    return ApproveAndExtractResult(
        approved_path=approved_path,
        plan_result=plan_result,
        extract_result=extract_result,
    )


def pending_root(source_id: str) -> Path:
    """Parent dir for all pending artifacts (plan.yaml, proposals/, tombstones)."""
    return cfg_mod.config_dir() / "pending" / source_id


def pending_dir(source_id: str) -> Path:
    """Return the pending directory for a source."""
    return cfg_mod.config_dir() / "pending" / source_id / "reading"


def pending_reading_path(source_id: str) -> Path:
    return pending_dir(source_id) / "reading.md"


def pending_structure_path(source_id: str) -> Path:
    return pending_dir(source_id) / "structure.yaml"


def pending_bullets_path(source_id: str) -> Path:
    return pending_dir(source_id) / "bullets.yaml"


def pending_plan_path(source_id: str) -> Path:
    """Plan artifact path. Sibling to the `reading/` subdir."""
    return cfg_mod.config_dir() / "pending" / source_id / "plan.yaml"


def pending_proposals_dir(source_id: str) -> Path:
    """Stage 3 proposal directory. Sibling to `plan.yaml`."""
    return cfg_mod.config_dir() / "pending" / source_id / "proposals"


def pending_proposal_path(source_id: str, proposal_id: str) -> Path:
    return pending_proposals_dir(source_id) / f"{proposal_id}.yaml"


def plan(cfg: cfg_mod.Config, source_id: str) -> PlanResult:
    """Run Stage 2 on an approved reading and write `plan.yaml`.

    :raises ReadingPipelineError: reading not approved, prior pipeline
        artifacts missing, or planner / preamble failure.
    """
    wiki_repo = cfg.wiki_repo_path
    approved_path = wiki_repo / "sources" / source_id / "reading.md"
    if not approved_path.exists():
        msg = (
            f"No approved reading at {approved_path}. "
            f"Run `approve-reading {source_id}` first."
        )
        raise ReadingPipelineError(msg)
    try:
        fm = reading_mod.read_frontmatter(approved_path)
    except reading_mod.ReadingError as e:
        raise ReadingPipelineError(str(e)) from e
    if fm.get("reading_status") != "approved":
        msg = (
            f"Reading at {approved_path} is not approved "
            f"(reading_status={fm.get('reading_status')!r})."
        )
        raise ReadingPipelineError(msg)

    structure_path = pending_structure_path(source_id)
    bullets_path = pending_bullets_path(source_id)
    if not structure_path.exists() or not bullets_path.exists():
        msg = (
            f"Missing prior pipeline artifacts for {source_id!r}. "
            "Re-run `regenerate-reading` to repopulate "
            f"{pending_dir(source_id)}."
        )
        raise ReadingPipelineError(msg)
    try:
        structure = structure_mod.read(structure_path)
    except structure_mod.StructureError as e:
        raise ReadingPipelineError(str(e)) from e
    try:
        bullets = stage1b_mod.read_bullets(bullets_path)
    except stage1b_mod.Stage1bError as e:
        raise ReadingPipelineError(str(e)) from e

    info_path = wiki_repo / "sources" / source_id / "info.yaml"
    try:
        info = info_yaml_mod.read(info_path)
    except info_yaml_mod.InfoError as e:
        raise ReadingPipelineError(str(e)) from e
    wc = wiki_context_mod.read(wiki_repo / ".wiki-context.yaml")
    cors = corrections_mod.read(wiki_repo / ".transcription-corrections.yaml")
    idx = entity_index_mod.build(wiki_repo)
    p = preamble_mod.assemble(info, wc, cors, idx, reduced=False)
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

    plan_path = pending_plan_path(source_id)
    plan_yaml_mod.write(result_plan, plan_path)
    return PlanResult(plan_path=plan_path, plan=result_plan)


def extract(cfg: cfg_mod.Config, source_id: str) -> ExtractResult:
    """Run Stage 3 against an existing plan and write proposal yamls.

    :raises ReadingPipelineError: missing plan, plain-text source, or
        Stage 3 failure (bad LLM output, schema violation).
    """
    wiki_repo = cfg.wiki_repo_path
    plan_path = pending_plan_path(source_id)
    if not plan_path.exists():
        msg = f"No plan at {plan_path}. Run `approve-reading {source_id}` first."
        raise ReadingPipelineError(msg)
    try:
        plan_obj = plan_yaml_mod.read(plan_path)
    except plan_yaml_mod.PlanError as e:
        raise ReadingPipelineError(str(e)) from e

    structure_path = pending_structure_path(source_id)
    if not structure_path.exists():
        msg = (
            f"No structure.yaml for {source_id!r}; run `regenerate-reading` "
            "to repopulate."
        )
        raise ReadingPipelineError(msg)
    try:
        structure = structure_mod.read(structure_path)
    except structure_mod.StructureError as e:
        raise ReadingPipelineError(str(e)) from e

    info_path = wiki_repo / "sources" / source_id / "info.yaml"
    try:
        info = info_yaml_mod.read(info_path)
    except info_yaml_mod.InfoError as e:
        raise ReadingPipelineError(str(e)) from e
    cors = corrections_mod.read(wiki_repo / ".transcription-corrections.yaml")
    wc = wiki_context_mod.read(wiki_repo / ".wiki-context.yaml")
    idx = entity_index_mod.build(wiki_repo)
    try:
        loaded = transcript_mod.load(wiki_repo, info, cors)
    except transcript_mod.TranscriptError as e:
        raise ReadingPipelineError(str(e)) from e

    p = preamble_mod.assemble(info, wc, cors, idx, reduced=True)
    try:
        p.check_budget(
            context_window=cfg.models.primary_context_window,
            budget_fraction=cfg.preamble.budget_fraction,
        )
    except preamble_mod.PreambleTooLargeError as e:
        raise ReadingPipelineError(str(e)) from e

    existing_fact_counts, existing_slugs = _collect_existing_target_metadata(
        wiki_repo, plan_obj, idx
    )

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

    proposals_dir = pending_proposals_dir(source_id)
    # Wipe + recreate: approved facts already live in entity YAMLs, so
    # the proposals dir holds only un-reviewed work — wiping loses
    # nothing authoritative. Review-loop PR will own per-proposal lifecycle.
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
    idx: entity_index_mod.EntityIndex,
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
            entry = idx.lookup(target.entity)
            if entry is None:
                # planner said "existing" but we can't resolve — treat as new
                # so allocation still works deterministically
                continue
            entity_path = wiki_repo / entry.category / f"{entry.slug}.yaml"
            try:
                entity = entity_yaml_mod.read(entity_path)
            except entity_yaml_mod.EntityError:
                continue
            fact_counts[target.entity] = len(entity.facts)
            slugs[target.entity] = entity.slug
    return fact_counts, slugs


def _run_full(cfg: cfg_mod.Config, source_id: str) -> GenerateResult:
    info, ctx = _load_context(cfg, source_id)
    client = _build_client(cfg)
    model = cfg.models.primary

    structure = stage1a_mod.run(
        transcript=ctx.transcript,
        preamble_text=ctx.preamble_text,
        source_id=source_id,
        client=client,
        model=model,
    )
    pdir = pending_dir(source_id)
    pdir.mkdir(parents=True, exist_ok=True)
    structure_mod.write(structure, pending_structure_path(source_id))

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
    stage1b_mod.write_bullets(bullets, pending_bullets_path(source_id))

    name_corrections = _load_existing_name_corrections(source_id)
    text = reading_mod.assemble(
        info=info,
        structure=structure,
        bullets=bullets,
        name_corrections=name_corrections,
    )
    reading_mod.write(pending_reading_path(source_id), text)

    return GenerateResult(
        pending_reading_path=pending_reading_path(source_id),
        structure_path=pending_structure_path(source_id),
        bullets_path=pending_bullets_path(source_id),
        gap_warnings=warnings,
    )


def _run_summarize_only(
    cfg: cfg_mod.Config,
    source_id: str,
    *,
    segment_ids: list[str] | None,
) -> GenerateResult:
    structure_path = pending_structure_path(source_id)
    if not structure_path.exists():
        msg = (
            f"No prior structure.yaml for {source_id!r}. "
            "Run `regenerate-reading --from=structure` or `generate-reading` first."
        )
        raise ReadingPipelineError(msg)
    structure = structure_mod.read(structure_path)

    info, ctx = _load_context(cfg, source_id)
    client = _build_client(cfg)
    model = cfg.models.primary

    existing = _load_existing_bullets(source_id)
    new_bullets = stage1b_mod.run(
        transcript=ctx.transcript,
        structure=structure,
        preamble_text=ctx.preamble_text,
        client=client,
        model=model,
        segment_ids=segment_ids,
    )
    # rebuilt segments overwrite; untouched segments keep existing bullets
    merged = existing.segments.copy()
    for sid, bullets_list in new_bullets.segments.items():
        merged[sid] = bullets_list
    for seg in structure.segments:
        merged.setdefault(seg.id, [])
    new_bullets.segments = merged
    stage1b_mod.write_bullets(new_bullets, pending_bullets_path(source_id))

    name_corrections = _load_existing_name_corrections(source_id)
    text = reading_mod.assemble(
        info=info,
        structure=structure,
        bullets=new_bullets,
        name_corrections=name_corrections,
    )
    reading_mod.write(pending_reading_path(source_id), text)

    return GenerateResult(
        pending_reading_path=pending_reading_path(source_id),
        structure_path=pending_structure_path(source_id),
        bullets_path=pending_bullets_path(source_id),
        gap_warnings=gap_check_mod.check(structure),
    )


@dataclass
class _Context:
    transcript: transcript_mod.LoadedTranscript
    preamble_text: str


def _load_context(cfg: cfg_mod.Config, source_id: str) -> tuple[Info, _Context]:
    wiki_repo = cfg.wiki_repo_path
    info_path = wiki_repo / "sources" / source_id / "info.yaml"
    if not info_path.exists():
        msg = f"info.yaml not found for {source_id!r}: run `ingest` first"
        raise ReadingPipelineError(msg)
    try:
        info = info_yaml_mod.read(info_path)
    except info_yaml_mod.InfoError as e:
        raise ReadingPipelineError(str(e)) from e

    wc = wiki_context_mod.read(wiki_repo / ".wiki-context.yaml")
    cors = corrections_mod.read(wiki_repo / ".transcription-corrections.yaml")
    idx = entity_index_mod.build(wiki_repo)

    try:
        loaded = transcript_mod.load(wiki_repo, info, cors)
    except transcript_mod.TranscriptError as e:
        raise ReadingPipelineError(str(e)) from e

    p = preamble_mod.assemble(info, wc, cors, idx, reduced=False)
    try:
        p.check_budget(
            context_window=cfg.models.primary_context_window,
            budget_fraction=cfg.preamble.budget_fraction,
        )
    except preamble_mod.PreambleTooLargeError as e:
        raise ReadingPipelineError(str(e)) from e
    return info, _Context(transcript=loaded, preamble_text=p.text)


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


def _load_existing_bullets(source_id: str) -> stage1b_mod.ReadingBullets:
    path = pending_bullets_path(source_id)
    if not path.exists():
        return stage1b_mod.ReadingBullets(
            source_id=source_id, generated_at="", segments={}
        )
    return stage1b_mod.read_bullets(path)


def _load_existing_name_corrections(source_id: str) -> dict[str, str]:
    path = pending_reading_path(source_id)
    if not path.exists():
        return {}
    try:
        fm = reading_mod.read_frontmatter(path)
    except reading_mod.ReadingError:
        return {}
    raw = fm.get("name_corrections") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}
