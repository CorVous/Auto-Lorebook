"""High-level orchestration of the Stage 1 reading pipeline.

Exposes `generate`, `approve`, and `regenerate` entry points used by
the three reading subcommands. The command modules handle argparse;
this module handles the wiring between stages.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from auto_lorebook import (
    corrections as corrections_mod,
)
from auto_lorebook import (
    entity_index as entity_index_mod,
)
from auto_lorebook import gap_check as gap_check_mod
from auto_lorebook import info_yaml as info_yaml_mod
from auto_lorebook import preamble as preamble_mod
from auto_lorebook import reading as reading_mod
from auto_lorebook import stage1a as stage1a_mod
from auto_lorebook import stage1b as stage1b_mod
from auto_lorebook import structure as structure_mod
from auto_lorebook import transcript as transcript_mod
from auto_lorebook import wiki_context as wiki_context_mod
from auto_lorebook.openrouter import OpenRouterClient, OpenRouterError

if TYPE_CHECKING:
    from auto_lorebook import config as cfg_mod
    from auto_lorebook.gap_check import GapWarning
    from auto_lorebook.info_yaml import Info

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


def generate(cfg: cfg_mod.Config, source_id: str) -> GenerateResult:
    """Run Stage 1a + 1b from scratch and write the draft reading.md."""
    return _run_full(cfg, source_id, preserve_bullets_for=None)


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
        return _run_full(cfg, source_id, preserve_bullets_for=None)
    if from_stage == "summarize":
        return _run_summarize_only(cfg, source_id, segment_ids=segment_ids)
    msg = f"unknown --from value: {from_stage!r} (expected structure|summarize)"
    raise ReadingPipelineError(msg)


def approve(cfg: cfg_mod.Config, source_id: str) -> Path:
    """Flip the draft to approved and copy it into the wiki."""
    pending_path = pending_reading_path(cfg, source_id)
    if not pending_path.exists():
        msg = f"No draft reading for {source_id!r}. Run `generate-reading` first."
        raise ReadingPipelineError(msg)

    reading_mod.set_status(pending_path, "approved")
    dest = cfg.wiki_repo_path / "sources" / source_id / "reading.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(pending_path.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def pending_dir(cfg: cfg_mod.Config, source_id: str) -> Path:  # noqa: ARG001
    """Return the pending directory for a source."""
    home = os.environ.get("AUTO_LOREBOOK_HOME")
    base = Path(home) if home else Path.home() / ".auto-lorebook"
    return base / "pending" / source_id / "reading"


def pending_reading_path(cfg: cfg_mod.Config, source_id: str) -> Path:
    return pending_dir(cfg, source_id) / "reading.md"


def pending_structure_path(cfg: cfg_mod.Config, source_id: str) -> Path:
    return pending_dir(cfg, source_id) / "structure.yaml"


def pending_bullets_path(cfg: cfg_mod.Config, source_id: str) -> Path:
    return pending_dir(cfg, source_id) / "bullets.yaml"


def _run_full(
    cfg: cfg_mod.Config,
    source_id: str,
    *,
    preserve_bullets_for: list[str] | None,
) -> GenerateResult:
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
    pdir = pending_dir(cfg, source_id)
    pdir.mkdir(parents=True, exist_ok=True)
    structure_mod.write(structure, pending_structure_path(cfg, source_id))

    warnings = gap_check_mod.check(structure)

    preserved = (
        {
            sid: bullets
            for sid, bullets in _load_existing_bullets(cfg, source_id).segments.items()
            if sid in (preserve_bullets_for or [])
        }
        if preserve_bullets_for is not None
        else {}
    )
    targets = [s.id for s in structure.segments if s.id not in preserved]
    bullets = stage1b_mod.run(
        transcript=ctx.transcript,
        structure=structure,
        preamble_text=ctx.preamble_text,
        client=client,
        model=model,
        segment_ids=targets or None,
    )
    for sid, existing in preserved.items():
        bullets.segments[sid] = existing
    # fill any segment missing from 1b output with an empty list
    for seg in structure.segments:
        bullets.segments.setdefault(seg.id, [])
    stage1b_mod.write_bullets(bullets, pending_bullets_path(cfg, source_id))

    name_corrections = _load_existing_name_corrections(cfg, source_id)
    text = reading_mod.assemble(
        info=info,
        structure=structure,
        bullets=bullets,
        name_corrections=name_corrections,
    )
    reading_mod.write(pending_reading_path(cfg, source_id), text)

    return GenerateResult(
        pending_reading_path=pending_reading_path(cfg, source_id),
        structure_path=pending_structure_path(cfg, source_id),
        bullets_path=pending_bullets_path(cfg, source_id),
        gap_warnings=warnings,
    )


def _run_summarize_only(
    cfg: cfg_mod.Config,
    source_id: str,
    *,
    segment_ids: list[str] | None,
) -> GenerateResult:
    structure_path = pending_structure_path(cfg, source_id)
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

    existing = _load_existing_bullets(cfg, source_id)
    new_bullets = stage1b_mod.run(
        transcript=ctx.transcript,
        structure=structure,
        preamble_text=ctx.preamble_text,
        client=client,
        model=model,
        segment_ids=segment_ids,
    )
    # merge: rebuilt segments overwrite; untouched segments keep existing bullets
    merged = existing.segments.copy()
    for sid, bullets_list in new_bullets.segments.items():
        merged[sid] = bullets_list
    for seg in structure.segments:
        merged.setdefault(seg.id, [])
    new_bullets.segments = merged
    stage1b_mod.write_bullets(new_bullets, pending_bullets_path(cfg, source_id))

    name_corrections = _load_existing_name_corrections(cfg, source_id)
    text = reading_mod.assemble(
        info=info,
        structure=structure,
        bullets=new_bullets,
        name_corrections=name_corrections,
    )
    reading_mod.write(pending_reading_path(cfg, source_id), text)

    return GenerateResult(
        pending_reading_path=pending_reading_path(cfg, source_id),
        structure_path=pending_structure_path(cfg, source_id),
        bullets_path=pending_bullets_path(cfg, source_id),
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
            f"OpenRouter API key not found (expected env var "
            f"{cfg.openrouter.api_key_env}). Export it and retry."
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


def _load_existing_bullets(
    cfg: cfg_mod.Config, source_id: str
) -> stage1b_mod.ReadingBullets:
    path = pending_bullets_path(cfg, source_id)
    if not path.exists():
        return stage1b_mod.ReadingBullets(
            source_id=source_id, generated_at="", segments={}
        )
    return stage1b_mod.read_bullets(path)


def _load_existing_name_corrections(
    cfg: cfg_mod.Config, source_id: str
) -> dict[str, str]:
    path = pending_reading_path(cfg, source_id)
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
