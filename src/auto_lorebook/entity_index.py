"""In-memory entity index built by scanning wiki entity YAMLs."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import yaml

_logger = logging.getLogger(__name__)

_CATEGORIES = ("characters", "locations", "factions", "events", "items", "concepts")


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
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        _logger.warning("entity_index: could not parse %s; skipping", path)
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("superseded_by") is not None:
        return None
    entity = raw.get("entity")
    category = raw.get("category")
    slug = raw.get("slug")
    if not entity or not category or not slug:
        _logger.warning("entity_index: missing required fields in %s; skipping", path)
        return None
    aliases_raw: list[Any] = raw.get("aliases") or []
    aliases = [a["name"] for a in aliases_raw if isinstance(a, dict) and a.get("name")]
    return EntityEntry(
        entity=str(entity), category=str(category), slug=str(slug), aliases=aliases
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
