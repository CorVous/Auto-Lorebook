"""In-memory entity index built by scanning wiki entity YAMLs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from auto_lorebook import entity_yaml

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)

_CATEGORIES = entity_yaml.CATEGORIES


@dataclass
class EntityEntry:
    """Minimal entity data extracted for the preamble."""

    entity: str
    category: str
    slug: str
    aliases: list[str] = field(default_factory=list)


class EntityIndex:
    """In-memory index of all non-superseded entities."""

    def __init__(self, entries: list[EntityEntry]) -> None:
        self._entries = entries

    def render_for_preamble(self) -> str:
        """Render grouped, sorted entity list for preamble inclusion."""
        by_cat: dict[str, list[EntityEntry]] = {}
        for e in self._entries:
            by_cat.setdefault(e.category, []).append(e)

        if not by_cat:
            return "(no entities yet)"

        lines: list[str] = []
        for cat in sorted(by_cat):
            cap = cat.capitalize()
            lines.append(f"{cap}:")
            for entry in sorted(by_cat[cat], key=lambda e: e.entity):
                if entry.aliases:
                    alias_str = ", ".join(sorted(entry.aliases))
                    lines.append(f"  - {entry.entity} (aliases: {alias_str})")
                else:
                    lines.append(f"  - {entry.entity}")
        return "\n".join(lines)


def _load_entry(path: Path) -> EntityEntry | None:
    """Parse a single entity YAML; return None on error or if superseded."""
    try:
        e = entity_yaml.read(path)
    except entity_yaml.EntityError:
        _logger.warning("entity_index: could not parse %s; skipping", path)
        return None
    if e.superseded_by is not None:
        return None
    return EntityEntry(
        entity=e.entity,
        category=e.category,
        slug=e.slug,
        aliases=[a.name for a in e.aliases],
    )


def build(wiki_repo: Path) -> EntityIndex:
    """Scan all entity YAMLs in wiki_repo and return an EntityIndex."""
    entries: list[EntityEntry] = []
    for cat in _CATEGORIES:
        cat_dir = wiki_repo / cat
        if not cat_dir.is_dir():
            continue
        for yaml_path in sorted(cat_dir.glob("*.yaml")):
            entry = _load_entry(yaml_path)
            if entry is not None:
                entries.append(entry)
    return EntityIndex(entries)
