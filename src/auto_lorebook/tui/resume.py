"""Stage detection: inspect on-disk artifacts and return the next unfinished stage.

Called at the start of every `process` run and on manual `--source-id` resumes.

Done-checks in order:
  INGEST       wiki/sources/<id>/transcript.*.srt exists
  CONTEXT      pending/<id>/context.set tombstone exists
  READING_GEN  pending_reading_path(id) exists
  READING_GATE reading frontmatter reading_status == "approved"
  PLAN         pending_plan_path(id) exists
  EXTRACT      pending_proposals_dir(id).exists()
               NOTE: proposals dir present but empty is still "done" — extract
               ran and produced 0 proposals; review.run short-circuits cleanly.
               Accepted edge case: if a write exception fires between rmtree and
               the first successful write in reading_pipeline.extract, the dir
               exists with zero files and we route to REVIEW_GATE, where
               review.run short-circuits and writes the tombstone. Window is
               extremely narrow (only proposal_yaml.write exceptions after stage3
               has already returned the list).
  REVIEW_GATE  pending/<id>/review.done tombstone exists
  DONE         same tombstone (DONE is an alias sentinel, not a separate check)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import reading as reading_mod
from auto_lorebook import reading_pipeline
from auto_lorebook.tui.state import Stage

if TYPE_CHECKING:
    from pathlib import Path


def detect_stage(source_id: str, wiki_repo: Path) -> Stage:
    """Return the next unfinished stage for *source_id*.

    :param source_id: e.g. ``yt-abc12345678``
    :param wiki_repo: path to the wiki repository root
    """
    config_dir = cfg_mod.config_dir()
    pending_root = config_dir / "pending" / source_id

    # INGEST: at least one transcript file must exist under sources/<id>/
    sources_dir = wiki_repo / "sources" / source_id
    has_transcript = sources_dir.is_dir() and any(sources_dir.glob("transcript.*"))
    if not has_transcript:
        return Stage.INGEST

    # CONTEXT: tombstone written by the process orchestrator after the context screen
    if not (pending_root / "context.set").exists():
        return Stage.CONTEXT

    # READING_GEN: pending reading.md must exist
    reading_path = reading_pipeline.pending_reading_path(source_id)
    if not reading_path.exists():
        return Stage.READING_GEN

    # READING_GATE: frontmatter reading_status must be "approved"
    try:
        fm = reading_mod.read_frontmatter(reading_path)
    except reading_mod.ReadingError:
        return Stage.READING_GATE
    if fm.get("reading_status") != "approved":
        return Stage.READING_GATE

    # PLAN: plan.yaml must exist
    if not reading_pipeline.pending_plan_path(source_id).exists():
        return Stage.PLAN

    # EXTRACT: proposals dir must exist (empty dir = 0 proposals = still done)
    if not reading_pipeline.pending_proposals_dir(source_id).exists():
        return Stage.EXTRACT

    # REVIEW_GATE / DONE
    if not (pending_root / "review.done").exists():
        return Stage.REVIEW_GATE

    return Stage.DONE
