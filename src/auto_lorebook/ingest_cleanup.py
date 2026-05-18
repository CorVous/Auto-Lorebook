"""Phase 4 cleanup: undo all of one ingest's contributions.

`reject_ingest(cfg, source_id)` walks every entity YAML in the wiki,
removes facts whose `created_by_ingest` matches and aliases whose
`added_by_ingest` matches, deletes entity stubs that the ingest itself
created and that are now empty of facts, and removes pipeline
artifacts under `pending/<source_id>/` (plan + proposals).

Decisions:

- Stub deletion criterion is empty-facts AND
  `entity.created_by_ingest == source_id`. Aliases are ignored — an
  entity can be referenced via alias from another ingest's fact, and
  we shouldn't delete a still-referenced stub.
- `<wiki>/sources/<source_id>/` is left alone. Transcript, info.yaml,
  and the approved reading.md stay; re-running `approve-reading`
  overwrites reading.md cleanly.
- `pending/<source_id>/reading/` (Stage 1a/1b artifacts) is left
  alone, so a follow-up `replan` or `regenerate-reading` still works.
- Facts/aliases without an ingest tag (hand-edited) are kept.
- Each `entity_yaml.write` is atomic; the walk is not. A crash
  mid-walk leaves a partial cleanup; re-running is idempotent.

`preview` is a read-only dry-run that returns the same `RejectResult`
counts the actual run would produce, used by the CLI's confirmation
prompt.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook import entity_yaml
from auto_lorebook import reading_pipeline as pipeline_mod
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    from pathlib import Path

    from auto_lorebook.config import Config
    from auto_lorebook.entity_yaml import Entity

_logger = logging.getLogger(__name__)


@dataclass
class RejectResult:
    """Counters returned by `preview` and `reject_ingest`."""

    facts_removed: int = 0
    aliases_removed: int = 0
    stubs_deleted: int = 0
    entities_modified: int = 0


@dataclass(frozen=True)
class _Plan:
    """What `reject_ingest` would do for one entity. Used by both paths."""

    facts_dropped: int
    aliases_dropped: int
    delete_stub: bool
    kept_facts: list[dict]
    kept_aliases: list[entity_yaml.Alias]


def _plan_for(entity: Entity, source_id: str) -> _Plan:
    kept_facts = [f for f in entity.facts if f.get("created_by_ingest") != source_id]
    kept_aliases = [a for a in entity.aliases if a.added_by_ingest != source_id]
    facts_dropped = len(entity.facts) - len(kept_facts)
    aliases_dropped = len(entity.aliases) - len(kept_aliases)
    delete_stub = not kept_facts and entity.created_by_ingest == source_id
    return _Plan(
        facts_dropped=facts_dropped,
        aliases_dropped=aliases_dropped,
        delete_stub=delete_stub,
        kept_facts=kept_facts,
        kept_aliases=kept_aliases,
    )


def _walk_entity_paths(wiki_repo: Path) -> list[Path]:
    paths: list[Path] = []
    for cat in entity_yaml.CATEGORIES:
        cat_dir = wiki_repo / cat
        if not cat_dir.is_dir():
            continue
        paths.extend(sorted(cat_dir.glob("*.yaml")))
    return paths


def preview(
    cfg: Config,
    source_id: str,
    wiki_override: str | None = None,
) -> RejectResult:
    """Read-only count of what `reject_ingest` would change."""
    result = RejectResult()
    for path in _walk_entity_paths(cfg.resolve_active_wiki(wiki_override)):
        try:
            entity = entity_yaml.read(path)
        except entity_yaml.EntityError:
            continue
        plan = _plan_for(entity, source_id)
        result.facts_removed += plan.facts_dropped
        result.aliases_removed += plan.aliases_dropped
        if plan.delete_stub:
            result.stubs_deleted += 1
        elif plan.facts_dropped or plan.aliases_dropped:
            result.entities_modified += 1
    return result


def reject_ingest(
    cfg: Config,
    source_id: str,
    wiki_override: str | None = None,
) -> RejectResult:
    """Remove `source_id`'s contributions from every entity; clean pending."""
    result = RejectResult()
    for path in _walk_entity_paths(cfg.resolve_active_wiki(wiki_override)):
        try:
            entity = entity_yaml.read(path)
        except entity_yaml.EntityError:
            _logger.warning("reject_ingest: could not parse %s; skipping", path)
            continue
        plan = _plan_for(entity, source_id)
        if plan.delete_stub:
            path.unlink()
            result.stubs_deleted += 1
            result.facts_removed += plan.facts_dropped
            result.aliases_removed += plan.aliases_dropped
            continue
        if not plan.facts_dropped and not plan.aliases_dropped:
            continue
        entity.facts = plan.kept_facts
        entity.aliases = plan.kept_aliases
        entity.updated_at = format_iso_now()
        entity_yaml.write(entity, path)
        result.entities_modified += 1
        result.facts_removed += plan.facts_dropped
        result.aliases_removed += plan.aliases_dropped

    # Pipeline artifacts: drop plan.yaml + proposals/. Leave reading/
    # alone so replan / regenerate-reading still work after a partial
    # reset.
    plan_path = pipeline_mod.pending_plan_path(source_id)
    plan_path.unlink(missing_ok=True)
    proposals_dir = pipeline_mod.pending_proposals_dir(source_id)
    if proposals_dir.exists():
        shutil.rmtree(proposals_dir)
    return result
