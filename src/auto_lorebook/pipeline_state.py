"""Pipeline state: detect next incomplete stage for a source."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from auto_lorebook import wiki_state

if TYPE_CHECKING:
    from pathlib import Path

    from auto_lorebook.config import Config


class Stage(Enum):
    """Pipeline stages in execution order."""

    INGEST = "ingest"
    GENERATE_READING = "generate_reading"
    APPROVE_READING = "approve_reading"
    PLAN = "plan"
    EXTRACT = "extract"
    REVIEW = "review"


def first_missing_stage(
    cfg: Config,
    source_id: str,
    *,
    wiki_override: str | None,
) -> Stage | None:
    """Return first incomplete stage, or None when all stages done.

    Detector table:
      INGEST        : sources row exists in DB (lazy-backfills from info.yaml)
      GENERATE_READING : pending/<sid>/reading/reading.yaml exists
      APPROVE_READING  : <wiki>/sources/<sid>/reading.md exists
      PLAN          : pending/<sid>/plan.yaml exists
      EXTRACT       : pending/<sid>/proposals/ absent
      REVIEW        : pending/<sid>/proposals/ exists and non-empty
      done          : proposals dir exists and empty
    """
    from auto_lorebook import db as db_mod  # noqa: PLC0415
    from auto_lorebook import info_yaml as info_yaml_mod  # noqa: PLC0415

    wiki_root: Path = cfg.resolve_active_wiki(wiki_override)

    conn = db_mod.open(wiki_state.wiki_db_path(wiki_root))
    try:
        ingested = info_yaml_mod.exists(conn, source_id, wiki_repo=wiki_root)
    finally:
        conn.close()

    if not ingested:
        return Stage.INGEST

    sidecar = wiki_state.pending_reading_dir(wiki_root, source_id) / "reading.yaml"
    wiki_reading = wiki_root / "sources" / source_id / "reading.md"

    # wiki-side reading.md implies GENERATE_READING + APPROVE_READING both done
    if not wiki_reading.exists():
        if not sidecar.exists():
            return Stage.GENERATE_READING
        return Stage.APPROVE_READING

    plan_yaml = wiki_state.pending_plan_path(wiki_root, source_id)
    if not plan_yaml.exists():
        return Stage.PLAN

    proposals_dir = wiki_state.pending_proposals_dir(wiki_root, source_id)
    if not proposals_dir.exists():
        return Stage.EXTRACT
    if any(proposals_dir.iterdir()):
        return Stage.REVIEW
    return None
