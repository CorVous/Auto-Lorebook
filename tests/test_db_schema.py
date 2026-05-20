"""CHECK-constraint smoke tests for schema v1."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from auto_lorebook import db

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def conn(tmp_path: Path) -> Generator[sqlite3.Connection]:
    c = db.open(tmp_path / "wiki.db")
    yield c
    c.close()


def _src(conn: sqlite3.Connection, source_id: str = "s1") -> None:
    """Insert a minimal sources row."""
    conn.execute(
        "INSERT OR IGNORE INTO sources(source_id, source_type, fetched_at) "
        "VALUES (?, 'text', '2025-01-01T00:00:00+00:00')",
        (source_id,),
    )


def _entity(
    conn: sqlite3.Connection,
    category: str = "characters",
    slug: str = "alice",
) -> None:
    """Insert a minimal entities row."""
    conn.execute(
        "INSERT OR IGNORE INTO entities"
        "(category, slug, canonical_name,"
        " created_at, created_by_ingest, updated_at) "
        "VALUES (?, ?, 'Alice',"
        " '2025-01-01T00:00:00', 'ing-1', '2025-01-01T00:00:00')",
        (category, slug),
    )


# ---------------------------------------------------------------------------
# entities.category
# ---------------------------------------------------------------------------


def test_entities_valid_categories(conn: sqlite3.Connection) -> None:
    for cat in ("characters", "locations", "factions", "events", "items", "concepts"):
        conn.execute(
            "INSERT INTO entities"
            "(category, slug, canonical_name,"
            " created_at, created_by_ingest, updated_at) "
            "VALUES (?, ?, 'X', '2025-01-01', 'i', '2025-01-01')",
            (cat, f"slug-{cat}"),
        )


def test_entities_invalid_category_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO entities"
            "(category, slug, canonical_name,"
            " created_at, created_by_ingest, updated_at) "
            "VALUES ('badcat', 'x', 'X', '2025-01-01', 'i', '2025-01-01')"
        )


# ---------------------------------------------------------------------------
# facts.status
# ---------------------------------------------------------------------------


def test_facts_valid_statuses(conn: sqlite3.Connection) -> None:
    _src(conn)
    for status in ("authoritative", "trustworthy", "hearsay", "disproven"):
        conn.execute(
            "INSERT INTO facts"
            "(id, text, raw_transcript_span, text_corrects_transcript,"
            " source_id, locator, status, approved_at, created_by_ingest) "
            "VALUES (?, 'txt', 'raw', 0, 's1', 'loc', ?, '2025-01-01', 'i')",
            (f"f-{status}", status),
        )


def test_facts_invalid_status_rejected(conn: sqlite3.Connection) -> None:
    _src(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO facts"
            "(id, text, raw_transcript_span, text_corrects_transcript,"
            " source_id, locator, status, approved_at, created_by_ingest) "
            "VALUES ('f1', 'txt', 'raw', 0, 's1', 'loc', 'rumour', '2025-01-01', 'i')"
        )


# ---------------------------------------------------------------------------
# fact_refs.kind
# ---------------------------------------------------------------------------

_VALID_KINDS = ("supersedes", "contradicts", "corroborates", "qualifies")

_INSERT_FACT = (
    "INSERT INTO facts"
    "(id, text, raw_transcript_span, text_corrects_transcript,"
    " source_id, locator, status, approved_at, created_by_ingest) "
    "VALUES (?, 'txt', 'raw', 0, 's1', 'loc', 'trustworthy', '2025-01-01', 'i')"
)
_INSERT_REF = (
    "INSERT INTO fact_refs"
    "(from_fact_id, to_fact_id, kind, created_at, created_by) "
    "VALUES (?, ?, ?, '2025-01-01', 'test')"
)


def test_fact_refs_valid_kinds(conn: sqlite3.Connection) -> None:
    _src(conn)
    for i, kind in enumerate(_VALID_KINDS):
        conn.execute(_INSERT_FACT, (f"f{i}a",))
        conn.execute(_INSERT_FACT, (f"f{i}b",))
        conn.execute(_INSERT_REF, (f"f{i}a", f"f{i}b", kind))


def test_fact_refs_invalid_kind_rejected(conn: sqlite3.Connection) -> None:
    _src(conn)
    conn.execute(_INSERT_FACT, ("fa",))
    conn.execute(_INSERT_FACT, ("fb",))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(_INSERT_REF, ("fa", "fb", "supports"))


def test_fact_refs_self_reference_rejected(conn: sqlite3.Connection) -> None:
    _src(conn)
    conn.execute(_INSERT_FACT, ("fx",))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(_INSERT_REF, ("fx", "fx", "supersedes"))


# ---------------------------------------------------------------------------
# aliases.source
# ---------------------------------------------------------------------------


def test_aliases_invalid_source_rejected(conn: sqlite3.Connection) -> None:
    _entity(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO aliases"
            "(entity_category, entity_slug, name, name_normalized,"
            " added_by_ingest, added_at, source) "
            "VALUES ('characters', 'alice', 'Al', 'al', 'i',"
            " '2025-01-01', 'unknown-source')"
        )


# ---------------------------------------------------------------------------
# segments.segment_status
# ---------------------------------------------------------------------------


def test_segments_invalid_status_rejected(conn: sqlite3.Connection) -> None:
    _src(conn)
    conn.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state) "
        "VALUES ('ing-1', 's1', '2025-01-01', 'reading')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO segments"
            "(ingest_id, segment_id, start, end, title, segment_status) "
            "VALUES ('ing-1', 'seg-1', '0:00', '1:00', 'T', 'pending')"
        )


# ---------------------------------------------------------------------------
# wiki_context singleton constraint
# ---------------------------------------------------------------------------


def test_wiki_context_singleton_enforced(conn: sqlite3.Connection) -> None:
    """Only one row allowed in wiki_context (CHECK id = 1)."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO wiki_context(id, updated_at) VALUES (2, '2025-01-01')"
        )


# ---------------------------------------------------------------------------
# ingests.state
# ---------------------------------------------------------------------------


def test_ingests_invalid_state_rejected(conn: sqlite3.Connection) -> None:
    _src(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO ingests(ingest_id, source_id, started_at, state) "
            "VALUES ('i1', 's1', '2025-01-01', 'pending')"
        )
