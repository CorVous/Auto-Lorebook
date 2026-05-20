"""DB-backed entity/alias store.

Public surface:
    EntityRow, AliasRow, EntityError, EntityNotFoundError
    normalize_name, slugify
    create_entity, get_entity, get_by_alias
    list_entities, list_aliases
    rename, add_alias, remove_alias
    supersede, resolve
    render_for_preamble, lookup_by_planner_name
"""

from __future__ import annotations

import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9-]+")
_SLUG_DASH_RE = re.compile(r"-+")

# valid alias source values (mirrors DDL CHECK constraint)
_VALID_SOURCES = frozenset({
    "hand-edited",
    "alias-confirmation",
    "stub-creation",
    "promoted-from-merge",
    "cli-edit",
})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EntityError(ValueError):
    """Base error for entity store operations."""


class EntityNotFoundError(EntityError):
    """Entity not found by (category, slug)."""


# ---------------------------------------------------------------------------
# Row dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityRow:
    """One row from the `entities` table."""

    category: str
    slug: str
    canonical_name: str
    superseded_by_category: str | None
    superseded_by_slug: str | None
    created_at: str
    created_by_ingest: str
    updated_at: str


@dataclass(frozen=True)
class AliasRow:
    """One row from the `aliases` table."""

    entity_category: str
    entity_slug: str
    name: str
    name_normalized: str
    added_by_ingest: str
    added_at: str
    source: str


# ---------------------------------------------------------------------------
# Normalization + slugification
# ---------------------------------------------------------------------------


def normalize_name(name: str) -> str:
    """NFKC → casefold → strip → collapse whitespace."""
    s = unicodedata.normalize("NFKC", name)
    s = s.casefold().strip()
    return re.sub(r"\s+", " ", s)


def slugify(name: str) -> str:
    """Canonical slug for an entity name. Empty input → empty string."""
    s = name.strip().casefold().replace(" ", "-")
    s = _SLUG_STRIP_RE.sub("-", s)
    return _SLUG_DASH_RE.sub("-", s).strip("-")


# ---------------------------------------------------------------------------
# Row constructors from sqlite3.Row
# ---------------------------------------------------------------------------


def _entity_from_row(row: sqlite3.Row) -> EntityRow:
    return EntityRow(
        category=row["category"],
        slug=row["slug"],
        canonical_name=row["canonical_name"],
        superseded_by_category=row["superseded_by_category"],
        superseded_by_slug=row["superseded_by_slug"],
        created_at=row["created_at"],
        created_by_ingest=row["created_by_ingest"],
        updated_at=row["updated_at"],
    )


def _alias_from_row(row: sqlite3.Row) -> AliasRow:
    return AliasRow(
        entity_category=row["entity_category"],
        entity_slug=row["entity_slug"],
        name=row["name"],
        name_normalized=row["name_normalized"],
        added_by_ingest=row["added_by_ingest"],
        added_at=row["added_at"],
        source=row["source"],
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_entity(
    conn: sqlite3.Connection,
    *,
    category: str,
    slug: str,
    canonical_name: str,
    ingest_id: str,
    when: str | None = None,
) -> EntityRow:
    """Insert entity; raises EntityError on duplicate PK or bad category."""
    now = when or format_iso_now()
    try:
        conn.execute(
            """
            INSERT INTO entities
                (category, slug, canonical_name,
                 superseded_by_category, superseded_by_slug,
                 created_at, created_by_ingest, updated_at)
            VALUES (?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (category, slug, canonical_name, now, ingest_id, now),
        )
    except sqlite3.IntegrityError as exc:
        msg = f"create_entity failed for {category}/{slug}: {exc}"
        raise EntityError(msg) from exc
    row = get_entity(conn, category, slug)
    if row is None:  # pragma: no cover
        msg = f"create_entity: get after insert returned nothing for {category}/{slug}"
        raise EntityError(msg)
    return row


def get_entity(
    conn: sqlite3.Connection,
    category: str,
    slug: str,
) -> EntityRow | None:
    """Return EntityRow or None."""
    row = conn.execute(
        "SELECT * FROM entities WHERE category=? AND slug=?",
        (category, slug),
    ).fetchone()
    return _entity_from_row(row) if row else None


def get_by_alias(
    conn: sqlite3.Connection,
    normalized_name: str,
    *,
    category: str | None = None,
) -> EntityRow | None:
    """Lookup entity by normalized alias name.

    Returns None when no match or ambiguous across categories (and
    category=None). Caller disambiguates.
    """
    if category is not None:
        row = conn.execute(
            """
            SELECT e.* FROM entities e
            JOIN aliases a ON a.entity_category=e.category AND a.entity_slug=e.slug
            WHERE a.name_normalized=? AND e.category=?
            """,
            (normalized_name, category),
        ).fetchone()
        return _entity_from_row(row) if row else None

    rows = conn.execute(
        """
        SELECT DISTINCT e.* FROM entities e
        JOIN aliases a ON a.entity_category=e.category AND a.entity_slug=e.slug
        WHERE a.name_normalized=?
        """,
        (normalized_name,),
    ).fetchall()
    if len(rows) == 1:
        return _entity_from_row(rows[0])
    if len(rows) > 1:
        # ambiguous; let caller pass category= to disambiguate
        return None
    return None


def list_entities(
    conn: sqlite3.Connection,
    category: str | None = None,
    *,
    include_superseded: bool = False,
    wiki_repo: Path | None = None,
) -> list[EntityRow]:
    """List entities sorted by (category, canonical_name).

    Lazy backfill: if the entities table is globally empty AND
    wiki_repo is provided, scans YAML once. Filtered-but-empty
    results don't trigger backfill (a category with no entities is
    a legitimate state, not a missing-DB signal).
    """
    if wiki_repo is not None:
        (db_total,) = conn.execute("SELECT COUNT(*) FROM entities").fetchone()
        if db_total == 0:
            _backfill_from_yaml(conn, wiki_repo)
    return _query_entities(conn, category, include_superseded=include_superseded)


def _query_entities(
    conn: sqlite3.Connection,
    category: str | None,
    *,
    include_superseded: bool,
) -> list[EntityRow]:
    if category is not None and not include_superseded:
        cur = conn.execute(
            """
            SELECT * FROM entities
            WHERE category=?
              AND superseded_by_category IS NULL AND superseded_by_slug IS NULL
            ORDER BY category, canonical_name COLLATE NOCASE
            """,
            (category,),
        )
    elif category is not None:
        cur = conn.execute(
            "SELECT * FROM entities WHERE category=?"
            " ORDER BY category, canonical_name COLLATE NOCASE",
            (category,),
        )
    elif not include_superseded:
        cur = conn.execute(
            """
            SELECT * FROM entities
            WHERE superseded_by_category IS NULL AND superseded_by_slug IS NULL
            ORDER BY category, canonical_name COLLATE NOCASE
            """
        )
    else:
        cur = conn.execute(
            "SELECT * FROM entities ORDER BY category, canonical_name COLLATE NOCASE"
        )
    return [_entity_from_row(r) for r in cur.fetchall()]


def list_aliases(
    conn: sqlite3.Connection,
    category: str,
    slug: str,
) -> list[AliasRow]:
    """List aliases for entity, sorted by name_normalized."""
    cur = conn.execute(
        """
        SELECT * FROM aliases
        WHERE entity_category=? AND entity_slug=?
        ORDER BY name_normalized
        """,
        (category, slug),
    )
    return [_alias_from_row(r) for r in cur.fetchall()]


def rename(
    conn: sqlite3.Connection,
    category: str,
    slug: str,
    new_canonical_name: str,
    *,
    when: str | None = None,
) -> EntityRow:
    """Update canonical_name; raises EntityNotFoundError if absent."""
    now = when or format_iso_now()
    cur = conn.execute(
        "UPDATE entities SET canonical_name=?, updated_at=?"
        " WHERE category=? AND slug=?",
        (new_canonical_name, now, category, slug),
    )
    if cur.rowcount == 0:
        msg = f"entity not found: {category}/{slug}"
        raise EntityNotFoundError(msg)
    row = get_entity(conn, category, slug)
    if row is None:  # pragma: no cover
        msg = f"rename: get after update returned nothing for {category}/{slug}"
        raise EntityError(msg)
    return row


def add_alias(
    conn: sqlite3.Connection,
    *,
    category: str,
    slug: str,
    name: str,
    ingest_id: str,
    source: str,
    when: str | None = None,
) -> AliasRow | None:
    """Add alias; idempotent (None if already on same entity).

    Raises EntityError on invalid source. Logs INFO on cross-entity collision.
    """
    if source not in _VALID_SOURCES:
        msg = (
            f"invalid alias source {source!r}; must be one of {sorted(_VALID_SOURCES)}"
        )
        raise EntityError(msg)
    now = when or format_iso_now()
    normalized = normalize_name(name)

    # check for existing alias on same entity (idempotent)
    existing = conn.execute(
        """
        SELECT 1 FROM aliases
        WHERE entity_category=? AND entity_slug=? AND name_normalized=?
        """,
        (category, slug, normalized),
    ).fetchone()
    if existing:
        return None

    # check for cross-entity collision (allowed, but log INFO)
    cross = conn.execute(
        """
        SELECT entity_category, entity_slug FROM aliases
        WHERE name_normalized=? AND NOT (entity_category=? AND entity_slug=?)
        """,
        (normalized, category, slug),
    ).fetchone()
    if cross:
        _logger.info(
            "entities: cross-entity alias collision — %r (normalized %r) "
            "already on %s/%s, now also on %s/%s",
            name,
            normalized,
            cross["entity_category"],
            cross["entity_slug"],
            category,
            slug,
        )

    try:
        conn.execute(
            """
            INSERT INTO aliases
                (entity_category, entity_slug, name, name_normalized,
                 added_by_ingest, added_at, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (category, slug, name, normalized, ingest_id, now, source),
        )
    except sqlite3.IntegrityError as exc:
        msg = f"add_alias failed for {category}/{slug} name={name!r}: {exc}"
        raise EntityError(msg) from exc

    row = conn.execute(
        """
        SELECT * FROM aliases
        WHERE entity_category=? AND entity_slug=? AND name_normalized=?
        """,
        (category, slug, normalized),
    ).fetchone()
    if row is None:  # pragma: no cover
        msg = (
            f"add_alias: get after insert returned nothing for"
            f" {category}/{slug} name={name!r}"
        )
        raise EntityError(msg)
    return _alias_from_row(row)


def remove_alias(
    conn: sqlite3.Connection,
    category: str,
    slug: str,
    normalized_name: str,
) -> bool:
    """Remove alias by normalized name; returns True if removed."""
    cur = conn.execute(
        """
        DELETE FROM aliases
        WHERE entity_category=? AND entity_slug=? AND name_normalized=?
        """,
        (category, slug, normalized_name),
    )
    return cur.rowcount > 0


def supersede(
    conn: sqlite3.Connection,
    *,
    category: str,
    slug: str,
    by_category: str,
    by_slug: str,
    when: str | None = None,
) -> EntityRow:
    """Mark entity as superseded; rejects self-ref and cycles."""
    if category == by_category and slug == by_slug:
        msg = f"entity {category}/{slug} cannot supersede itself"
        raise EntityError(msg)

    # cycle detection: follow the by_* chain to ensure it doesn't reach us
    visited: set[tuple[str, str]] = set()
    cur_cat, cur_slug = by_category, by_slug
    while True:
        if cur_cat == category and cur_slug == slug:
            msg = f"supersession cycle: {category}/{slug} → {by_category}/{by_slug}"
            raise EntityError(msg)
        if (cur_cat, cur_slug) in visited:
            break
        visited.add((cur_cat, cur_slug))
        row = conn.execute(
            "SELECT superseded_by_category, superseded_by_slug"
            " FROM entities WHERE category=? AND slug=?",
            (cur_cat, cur_slug),
        ).fetchone()
        if row is None or row["superseded_by_category"] is None:
            break
        cur_cat = row["superseded_by_category"]
        cur_slug = row["superseded_by_slug"]

    now = when or format_iso_now()
    cur = conn.execute(
        """
        UPDATE entities
        SET superseded_by_category=?, superseded_by_slug=?, updated_at=?
        WHERE category=? AND slug=?
        """,
        (by_category, by_slug, now, category, slug),
    )
    if cur.rowcount == 0:
        msg = f"entity not found: {category}/{slug}"
        raise EntityNotFoundError(msg)
    row2 = get_entity(conn, category, slug)
    if row2 is None:  # pragma: no cover
        msg = f"supersede: get after update returned nothing for {category}/{slug}"
        raise EntityError(msg)
    return row2


def resolve(
    conn: sqlite3.Connection,
    category: str,
    slug: str,
    *,
    max_hops: int = 16,
) -> EntityRow:
    """Follow superseded_by chain; raises EntityError on cycle past max_hops."""
    hops = 0
    cur_cat, cur_slug = category, slug
    while True:
        row = get_entity(conn, cur_cat, cur_slug)
        if row is None:
            msg = f"entity not found: {cur_cat}/{cur_slug}"
            raise EntityNotFoundError(msg)
        if row.superseded_by_category is None:
            return row
        hops += 1
        if hops > max_hops:
            msg = f"supersession chain exceeded {max_hops} hops for {category}/{slug}"
            raise EntityError(msg)
        # superseded_by_category is not None (checked above);
        # both columns always set together (FK constraint)
        cur_cat = row.superseded_by_category
        cur_slug = row.superseded_by_slug or ""


# ---------------------------------------------------------------------------
# Preamble rendering
# ---------------------------------------------------------------------------


def render_for_preamble(
    conn: sqlite3.Connection,
    wiki_repo: Path | None = None,
) -> str:
    """Render grouped entity list for preamble inclusion.

    Groups by category (sorted), entities sorted by canonical_name,
    aliases sorted. Excludes superseded entities.
    """
    rows = list_entities(conn, wiki_repo=wiki_repo)
    if not rows:
        return "(no entities yet)"

    by_cat: dict[str, list[EntityRow]] = {}
    for r in rows:
        by_cat.setdefault(r.category, []).append(r)

    lines: list[str] = []
    for cat in sorted(by_cat):
        cap = cat.capitalize()
        lines.append(f"{cap}:")
        for entity in sorted(by_cat[cat], key=lambda e: e.canonical_name):
            aliases = list_aliases(conn, entity.category, entity.slug)
            if aliases:
                alias_str = ", ".join(sorted(a.name for a in aliases))
                lines.append(f"  - {entity.canonical_name} (aliases: {alias_str})")
            else:
                lines.append(f"  - {entity.canonical_name}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def lookup_by_planner_name(
    conn: sqlite3.Connection,
    name: str,
) -> EntityRow | None:
    """Resolve name to entity: canonical_name first (case-insensitive), then alias."""
    if not name.strip():
        return None
    # canonical name match (case-insensitive)
    row = conn.execute(
        "SELECT * FROM entities WHERE canonical_name=? COLLATE NOCASE",
        (name.strip(),),
    ).fetchone()
    if row is not None:
        return _entity_from_row(row)
    # alias match
    normalized = normalize_name(name)
    return get_by_alias(conn, normalized)


def search_entities(
    conn: sqlite3.Connection,
    query: str,
) -> list[EntityRow]:
    """3-tier `entities show` lookup.

    Order: exact slug → canonical name (case-insensitive) → alias
    (normalized). Returns all matches at the first tier that has any;
    empty list if nothing hits.
    """
    rows = conn.execute("SELECT * FROM entities WHERE slug=?", (query,)).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT * FROM entities WHERE canonical_name=? COLLATE NOCASE",
            (query,),
        ).fetchall()
    if not rows:
        norm = normalize_name(query)
        rows = conn.execute(
            """
            SELECT DISTINCT e.* FROM entities e
            JOIN aliases a
              ON a.entity_category=e.category AND a.entity_slug=e.slug
            WHERE a.name_normalized=?
            """,
            (norm,),
        ).fetchall()
    return [_entity_from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------


def _backfill_from_yaml(conn: sqlite3.Connection, wiki_repo: Path) -> None:
    """One-shot YAML→DB backfill. Idempotent via INSERT OR IGNORE."""
    from auto_lorebook import entity_yaml  # noqa: PLC0415

    entities = entity_yaml.scan(wiki_repo)
    if not entities:
        return
    _logger.info("entities: backfilling DB from %d YAML entities", len(entities))
    now = format_iso_now()
    for e in entities:
        conn.execute(
            """
            INSERT OR IGNORE INTO entities
                (category, slug, canonical_name,
                 superseded_by_category, superseded_by_slug,
                 created_at, created_by_ingest, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                e.category,
                e.slug,
                e.entity,
                _parse_superseded_by_category(e.superseded_by),
                _parse_superseded_by_slug(e.superseded_by),
                e.created_at or now,
                e.created_by_ingest or "yaml-backfill",
                e.updated_at or now,
            ),
        )
        for alias in e.aliases:
            normalized = normalize_name(alias.name)
            conn.execute(
                """
                INSERT OR IGNORE INTO aliases
                    (entity_category, entity_slug, name, name_normalized,
                     added_by_ingest, added_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    e.category,
                    e.slug,
                    alias.name,
                    normalized,
                    alias.added_by_ingest or "yaml-backfill",
                    alias.added_at or now,
                    alias.source or "hand-edited",
                ),
            )


def _parse_superseded_by_category(superseded_by: str | None) -> str | None:
    if not superseded_by:
        return None
    parts = superseded_by.split("/", 1)
    return parts[0] if len(parts) == 2 else None


def _parse_superseded_by_slug(superseded_by: str | None) -> str | None:
    if not superseded_by:
        return None
    parts = superseded_by.split("/", 1)
    return parts[1] if len(parts) == 2 else None
