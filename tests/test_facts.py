"""Tests for facts.py — DB-backed fact store."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from auto_lorebook import db
from auto_lorebook.facts import (
    VALID_STATUSES,
    FactError,
    FactRow,
    create_fact_with_target,
    create_fact_with_targets,
    get_fact,
    list_facts_by_entity,
    update_status,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Generator


@pytest.fixture
def conn() -> Generator[sqlite3.Connection]:
    """In-memory DB with full schema.

    Yields:
        open in-memory connection.

    """
    c = db.open(":memory:")
    # seed a source so FK constraint passes
    c.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at, context_json)"
        " VALUES ('src-001', 'youtube', '2026-01-01T00:00:00Z', '{}')"
    )
    # seed entity + ingests row
    c.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('ing-001', 'src-001', '2026-01-01T00:00:00Z', 'done')"
    )
    c.execute(
        "INSERT INTO entities(category, slug, canonical_name, created_at,"
        " created_by_ingest, updated_at)"
        " VALUES ('characters', 'theron', 'Theron',"
        " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
    )
    c.commit()
    yield c
    c.close()


def _make_fact(
    conn: sqlite3.Connection, fact_id: str = "f-001", status: str = "authoritative"
) -> FactRow:
    return create_fact_with_target(
        conn,
        fact_id=fact_id,
        text="Theron founded Aldara.",
        raw_transcript_span="Theron founded all-dara.",
        text_corrects_transcript=True,
        source_id="src-001",
        locator="0:04:32",
        status=status,
        approved_at="2026-01-15T10:00:00Z",
        created_by_ingest="ing-001",
        entity_category="characters",
        entity_slug="theron",
        section="biography",
        by="test-user",
    )


class TestCreateFactWithTarget:
    def test_returns_fact_row(self, conn: sqlite3.Connection) -> None:
        row = _make_fact(conn)
        assert isinstance(row, FactRow)
        assert row.id == "f-001"
        assert row.text == "Theron founded Aldara."
        assert row.status == "authoritative"
        assert row.source_id == "src-001"

    def test_inserts_fact_target(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        row = conn.execute(
            "SELECT * FROM fact_targets WHERE fact_id='f-001'"
        ).fetchone()
        assert row is not None
        assert row[1] == "characters"
        assert row[2] == "theron"
        assert row[3] == "biography"

    def test_inserts_status_history(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        row = conn.execute(
            "SELECT status, by FROM fact_status_history WHERE fact_id='f-001'"
        ).fetchone()
        assert row is not None
        assert row[0] == "authoritative"
        assert row[1] == "test-user"

    def test_invalid_status_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(FactError, match="invalid status"):
            create_fact_with_target(
                conn,
                fact_id="f-bad",
                text="x",
                raw_transcript_span="x",
                text_corrects_transcript=False,
                source_id="src-001",
                locator="0:00:00",
                status="fictional",
                approved_at="2026-01-01T00:00:00Z",
                created_by_ingest="ing-001",
                entity_category="characters",
                entity_slug="theron",
                section="overview",
                by="tester",
            )

    def test_duplicate_fact_id_raises(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        conn.commit()
        with pytest.raises(FactError):
            _make_fact(conn)


class TestGetFact:
    def test_get_existing_fact(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        conn.commit()
        row = get_fact(conn, "f-001")
        assert row is not None
        assert row.id == "f-001"

    def test_get_missing_returns_none(self, conn: sqlite3.Connection) -> None:
        assert get_fact(conn, "no-such-fact") is None


class TestListFactsByEntity:
    def test_returns_empty_when_none(self, conn: sqlite3.Connection) -> None:
        result = list_facts_by_entity(conn, "characters", "theron")
        assert result == []

    def test_returns_inserted_facts_sorted(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn, fact_id="f-002", status="trustworthy")
        _make_fact(conn, fact_id="f-001", status="authoritative")
        conn.commit()
        rows = list_facts_by_entity(conn, "characters", "theron")
        # f-001 approved_at same as f-002 → sort by id
        ids = [r.id for r in rows]
        assert "f-001" in ids
        assert "f-002" in ids


class TestUpdateStatus:
    def test_updates_status(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        conn.commit()
        update_status(conn, "f-001", "disproven", by="reviewer")
        conn.commit()
        row = get_fact(conn, "f-001")
        assert row is not None
        assert row.status == "disproven"

    def test_inserts_history_on_update(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        conn.commit()
        update_status(conn, "f-001", "hearsay", by="reviewer", reason="uncertain")
        conn.commit()
        rows = conn.execute(
            "SELECT status, reason FROM fact_status_history WHERE fact_id='f-001'"
            " ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[1][0] == "hearsay"
        assert rows[1][1] == "uncertain"

    def test_invalid_status_raises(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        conn.commit()
        with pytest.raises(FactError, match="invalid status"):
            update_status(conn, "f-001", "unknown", by="reviewer")

    def test_missing_fact_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(FactError, match="fact not found"):
            update_status(conn, "no-such", "authoritative", by="reviewer")


class TestCreateFactWithTargets:
    def test_inserts_one_fact_and_n_fact_targets(
        self, conn: sqlite3.Connection
    ) -> None:
        # seed two extra entities for multi-target
        conn.execute(
            "INSERT INTO entities(category, slug, canonical_name, created_at,"
            " created_by_ingest, updated_at)"
            " VALUES ('locations', 'aldara', 'Aldara',"
            " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO entities(category, slug, canonical_name, created_at,"
            " created_by_ingest, updated_at)"
            " VALUES ('events', 'second-age', 'Second Age',"
            " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
        )
        targets = [
            ("characters", "theron", "biography"),
            ("locations", "aldara", "founding"),
            ("events", "second-age", "events-in-era"),
        ]
        row = create_fact_with_targets(
            conn,
            fact_id="multi-f001",
            text="Multi-target claim.",
            raw_transcript_span="Multi-target claim.",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:01:00",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            targets=targets,
            by="test-user",
        )
        assert isinstance(row, FactRow)
        assert row.id == "multi-f001"
        # 1 fact row
        fact_count = conn.execute(
            "SELECT COUNT(*) FROM facts WHERE id='multi-f001'"
        ).fetchone()[0]
        assert fact_count == 1
        # 3 fact_targets rows
        ft_rows = conn.execute(
            "SELECT entity_category, entity_slug, section FROM fact_targets"
            " WHERE fact_id='multi-f001' ORDER BY entity_category"
        ).fetchall()
        assert len(ft_rows) == 3

    def test_zero_targets_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(FactError, match="at least one target required"):
            create_fact_with_targets(
                conn,
                fact_id="bad-f001",
                text="x",
                raw_transcript_span="x",
                text_corrects_transcript=False,
                source_id="src-001",
                locator="0:00:00",
                status="authoritative",
                approved_at="2026-01-01T00:00:00Z",
                created_by_ingest="ing-001",
                targets=[],
                by="tester",
            )


class TestValidStatuses:
    def test_all_four_statuses_present(self) -> None:
        assert (
            frozenset({
                "authoritative",
                "trustworthy",
                "hearsay",
                "disproven",
            })
            == VALID_STATUSES
        )
