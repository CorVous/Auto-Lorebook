"""Phase 4 cleanup: undo all of one ingest's contributions.

`reject_ingest(cfg, source_id)` removes all facts created by the ingest
from the DB, deletes entity rows that the ingest created and are now
empty of facts, removes their `.md` summaries, and removes pipeline
artifacts under `pending/<source_id>/` (plan + proposals).

Decisions:

- Stub deletion criterion: empty facts AND `entity.created_by_ingest == source_id`.
  Aliases are left in place for other ingests' entities.
- `<wiki>/sources/<source_id>/` is left alone. Transcript, info.yaml,
  and the approved reading.md stay; re-running `approve-reading`
  overwrites reading.md cleanly.
- `pending/<source_id>/reading/` (Stage 1a/1b artifacts) is left
  alone, so a follow-up `replan` or `regenerate-reading` still works.
- Facts/aliases without an ingest tag (hand-edited) are kept.
- Each operation is DB-transactional; a crash mid-run leaves a partial
  cleanup; re-running is idempotent.

`preview` is a read-only dry-run that returns the same `RejectResult`
counts the actual run would produce, used by the CLI's confirmation
prompt.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook import db as db_mod
from auto_lorebook import entities as entities_mod
from auto_lorebook import facts as facts_mod
from auto_lorebook import reading_pipeline as pipeline_mod
from auto_lorebook import summary_regen as regen_mod
from auto_lorebook import wiki_state as wiki_state_mod

if TYPE_CHECKING:
    from auto_lorebook.config import Config

_logger = logging.getLogger(__name__)


@dataclass
class RejectResult:
    """Counters returned by `preview` and `reject_ingest`."""

    facts_removed: int = 0
    aliases_removed: int = 0
    stubs_deleted: int = 0
    entities_modified: int = 0


def preview(
    cfg: Config,
    source_id: str,
    wiki_override: str | None = None,
) -> RejectResult:
    """Read-only count of what `reject_ingest` would change."""
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    result = RejectResult()
    try:
        # count facts to be removed
        fact_rows = conn.execute(
            "SELECT f.id, ft.entity_category, ft.entity_slug"
            " FROM facts f"
            " JOIN fact_targets ft ON ft.fact_id = f.id"
            " WHERE f.created_by_ingest=?",
            (source_id,),
        ).fetchall()
        result.facts_removed = len(fact_rows)

        # which entities created by this ingest will be empty after removal?
        affected_entities = {
            (r["entity_category"], r["entity_slug"]) for r in fact_rows
        }
        for cat, slug in affected_entities:
            entity = entities_mod.get_entity(conn, cat, slug)
            if entity is None:
                continue
            remaining = conn.execute(
                "SELECT COUNT(*) FROM facts f"
                " JOIN fact_targets ft ON ft.fact_id=f.id"
                " WHERE ft.entity_category=? AND ft.entity_slug=?"
                " AND f.created_by_ingest!=?",
                (cat, slug, source_id),
            ).fetchone()[0]
            if remaining == 0 and entity.created_by_ingest == source_id:
                result.stubs_deleted += 1
            elif remaining < len(facts_mod.list_facts_by_entity(conn, cat, slug)):
                result.entities_modified += 1
    finally:
        conn.close()
    return result


def reject_ingest(
    cfg: Config,
    source_id: str,
    wiki_override: str | None = None,
) -> RejectResult:
    """Remove `source_id`'s contributions from DB; clean pending."""
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    result = RejectResult()
    try:
        # collect entities affected before deletion
        fact_rows = conn.execute(
            "SELECT f.id, ft.entity_category, ft.entity_slug"
            " FROM facts f"
            " JOIN fact_targets ft ON ft.fact_id = f.id"
            " WHERE f.created_by_ingest=?",
            (source_id,),
        ).fetchall()
        result.facts_removed = len(fact_rows)

        affected_entities: dict[tuple[str, str], entities_mod.EntityRow | None] = {}
        for r in fact_rows:
            key = (r["entity_category"], r["entity_slug"])
            if key not in affected_entities:
                affected_entities[key] = entities_mod.get_entity(
                    conn, r["entity_category"], r["entity_slug"]
                )

        # delete facts + cascade (fact_targets, fact_status_history)
        conn.execute(
            "DELETE FROM facts WHERE created_by_ingest=?",
            (source_id,),
        )

        # delete aliases created by this ingest
        cur = conn.execute(
            "DELETE FROM aliases WHERE added_by_ingest=?",
            (source_id,),
        )
        result.aliases_removed = cur.rowcount

        # delete or mark entities
        for (cat, slug), entity in affected_entities.items():
            if entity is None:
                continue
            remaining = conn.execute(
                "SELECT COUNT(*) FROM facts f"
                " JOIN fact_targets ft ON ft.fact_id=f.id"
                " WHERE ft.entity_category=? AND ft.entity_slug=?",
                (cat, slug),
            ).fetchone()[0]
            if remaining == 0 and entity.created_by_ingest == source_id:
                conn.execute(
                    "DELETE FROM entities WHERE category=? AND slug=?",
                    (cat, slug),
                )
                regen_mod.delete_entity_summary(wiki_repo, cat, slug)
                result.stubs_deleted += 1
            elif remaining < (result.facts_removed):
                # partial removal; regenerate .md
                with contextlib.suppress(ValueError):
                    regen_mod.regenerate_entity(conn, wiki_repo, cat, slug)
                result.entities_modified += 1

    finally:
        conn.close()

    # Pipeline artifacts: drop plan.yaml + proposals/. Leave reading-state
    # DB rows (ingests/segments/segment_bullets) alone so replan /
    # regenerate-reading still work after a partial reset.
    plan_path = pipeline_mod.pending_plan_path(source_id)
    plan_path.unlink(missing_ok=True)
    proposals_dir = pipeline_mod.pending_proposals_dir(source_id)
    if proposals_dir.exists():
        shutil.rmtree(proposals_dir)
    return result
