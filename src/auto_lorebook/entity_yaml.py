"""Entity YAML read/write with schema validation.

Mirrors `info_yaml.py` shape; preserves unknown top-level keys on
write-back so Phase 4 facts written by future code survive a Phase 2
read→write round-trip.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text

# re-export so callers importing slugify from entity_yaml keep working
from auto_lorebook.entities import normalize_name as _normalize_name
from auto_lorebook.entities import slugify as slugify  # noqa: PLC0414
from auto_lorebook.schema import SchemaVersionError, read_schema_version

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)

_MAX_SCHEMA = 1

# six entity categories; mirrors DDL CHECK in db/ddl.py
CATEGORIES: tuple[str, ...] = (
    "characters",
    "locations",
    "factions",
    "events",
    "items",
    "concepts",
)

# top-level keys this module knows about; everything else lands in `Entity.extra`
_KNOWN_KEYS = frozenset(
    {
        "schema_version",
        "entity",
        "category",
        "slug",
        "aliases",
        "superseded_by",
        "created_at",
        "created_by_ingest",
        "updated_at",
        "facts",
    },
)


class EntityError(ValueError):
    """Raised when entity YAML is missing or malformed."""


@dataclass
class Alias:
    """Single alias record on an entity.

    See `docs/architecture/entity-model.md` for `source` enum values.
    """

    name: str
    added_by_ingest: str | None = None
    added_at: str | None = None
    source: str | None = None


@dataclass
class Entity:
    """In-memory representation of `<category>/<slug>.yaml`."""

    entity: str
    category: str
    slug: str
    aliases: list[Alias] = field(default_factory=list)
    superseded_by: str | None = None
    created_at: str | None = None
    created_by_ingest: str | None = None
    updated_at: str | None = None
    # facts are kept as raw dicts in Phase 2; Phase 4 models them properly.
    facts: list[dict[str, Any]] = field(default_factory=list)
    # unrecognized top-level keys; preserved on write-back.
    extra: dict[str, Any] = field(default_factory=dict)


def normalize_alias_name(name: str) -> str:
    """Delegate to entities.normalize_name (NFKC + casefold + collapse whitespace)."""
    return _normalize_name(name)


def _alias_from_dict(data: Any) -> Alias | None:  # noqa: ANN401
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    return Alias(
        name=name,
        added_by_ingest=data.get("added_by_ingest") or None,
        added_at=data.get("added_at") or None,
        source=data.get("source") or None,
    )


def _alias_to_dict(alias: Alias) -> dict[str, Any]:
    return {
        "name": alias.name,
        "added_by_ingest": alias.added_by_ingest,
        "added_at": alias.added_at,
        "source": alias.source,
    }


def _dedup_aliases(aliases: list[Alias]) -> list[Alias]:
    """Drop later records sharing a normalized name with an earlier one."""
    seen: set[str] = set()
    out: list[Alias] = []
    for a in aliases:
        key = normalize_alias_name(a.name)
        if key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


def _from_dict(data: dict[str, Any], file_label: str) -> Entity:
    for required in ("entity", "category", "slug"):
        if not data.get(required):
            msg = f"{file_label}: missing required field {required!r}"
            raise EntityError(msg)
    category = str(data["category"])
    if category not in CATEGORIES:
        allowed = ", ".join(CATEGORIES)
        msg = f"{file_label}: category {category!r} not one of: {allowed}"
        raise EntityError(msg)

    raw_aliases = data.get("aliases") or []
    if not isinstance(raw_aliases, list):
        msg = f"{file_label}: aliases must be a list"
        raise EntityError(msg)
    aliases: list[Alias] = []
    for raw in raw_aliases:
        a = _alias_from_dict(raw)
        if a is not None:
            aliases.append(a)
    aliases = _dedup_aliases(aliases)

    facts = data.get("facts") or []
    if not isinstance(facts, list):
        msg = f"{file_label}: facts must be a list"
        raise EntityError(msg)

    extra = {k: v for k, v in data.items() if k not in _KNOWN_KEYS}

    return Entity(
        entity=str(data["entity"]),
        category=category,
        slug=str(data["slug"]),
        aliases=aliases,
        superseded_by=data.get("superseded_by") or None,
        created_at=data.get("created_at") or None,
        created_by_ingest=data.get("created_by_ingest") or None,
        updated_at=data.get("updated_at") or None,
        facts=list(facts),
        extra=extra,
    )


def _to_dict(entity: Entity) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": 1,
        "entity": entity.entity,
        "category": entity.category,
        "slug": entity.slug,
        "aliases": [_alias_to_dict(a) for a in _dedup_aliases(entity.aliases)],
        "superseded_by": entity.superseded_by,
        "created_at": entity.created_at,
        "created_by_ingest": entity.created_by_ingest,
        "updated_at": entity.updated_at,
        "facts": list(entity.facts),
    }
    # preserved keys appended after known ones; ignore any collisions
    for k, v in entity.extra.items():
        if k not in out:
            out[k] = v
    return out


def read(path: Path) -> Entity:
    """Read and validate an entity YAML.

    :raises EntityError: missing file, non-mapping root, or invalid contents
    """
    if not path.exists():
        msg = f"{path}: file not found"
        raise EntityError(msg)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a YAML mapping"
        raise EntityError(msg)
    try:
        read_schema_version(raw, str(path), max_supported=_MAX_SCHEMA)
    except SchemaVersionError as e:
        raise EntityError(str(e)) from e
    return _from_dict(raw, str(path))


def write(entity: Entity, path: Path) -> None:
    """Atomically write entity to path; schema_version always first key."""
    text = yaml.safe_dump(
        _to_dict(entity),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    atomic_write_text(path, text)


def scan(wiki_repo: Path) -> list[Entity]:
    """Read every entity YAML under `wiki_repo`; skip malformed with warning.

    Sorted by category (per `CATEGORIES` order), then slug.
    """
    out: list[Entity] = []
    for cat in CATEGORIES:
        cat_dir = wiki_repo / cat
        if not cat_dir.is_dir():
            continue
        for yaml_path in sorted(cat_dir.glob("*.yaml")):
            try:
                out.append(read(yaml_path))
            except EntityError:
                _logger.warning("entity_yaml: could not parse %s; skipping", yaml_path)
    return out
