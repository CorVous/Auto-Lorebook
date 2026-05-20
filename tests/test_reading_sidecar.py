"""Tests for reading_sidecar.py — DB-backed reading session state."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from auto_lorebook.reading_sidecar import (
    IngestState,
    ReadingSidecarError,
    Sidecar,
    exists,
    read_state,
    write_state,
)

if TYPE_CHECKING:
    import sqlite3


def _seed_ingest(conn: sqlite3.Connection, ingest_id: str = "src-001") -> None:
    """Insert minimal sources + ingests rows."""
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, source_type, fetched_at, context_json) "
        "VALUES (?,?,?,?)",
        (ingest_id, "youtube", "2026-01-01T00:00:00Z", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO ingests "
        "(ingest_id, source_id, started_at, state, default_speaker, "
        " name_corrections_json, session_date) "
        "VALUES (?,?,?,'reading',NULL,'{}',NULL)",
        (ingest_id, ingest_id, "2026-01-01T00:00:00Z"),
    )
    conn.commit()


class TestWriteAndReadRoundTrip:
    def test_round_trip_full(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        write_state(
            db_conn,
            "src-001",
            default_speaker="DM",
            name_corrections={"Fair-on": "Theron", "Aldera": "Aldara"},
            session_date="2026-01-15",
        )
        db_conn.commit()
        sc = read_state(db_conn, "src-001")
        assert sc.default_speaker == "DM"
        assert sc.name_corrections == {"Fair-on": "Theron", "Aldera": "Aldara"}
        assert sc.session_date == "2026-01-15"
        assert sc.ingest_id == "src-001"
        assert sc.source_id == "src-001"

    def test_empty_corrections(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        write_state(
            db_conn,
            "src-001",
            default_speaker="GM",
            name_corrections={},
            session_date=None,
        )
        db_conn.commit()
        sc = read_state(db_conn, "src-001")
        assert sc.name_corrections == {}
        assert sc.session_date is None

    def test_null_session_date(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        write_state(
            db_conn,
            "src-001",
            default_speaker="GM",
            name_corrections={},
            session_date=None,
        )
        db_conn.commit()
        sc = read_state(db_conn, "src-001")
        assert sc.session_date is None

    def test_gap_warnings_empty_when_no_structure(
        self, db_conn: sqlite3.Connection
    ) -> None:
        _seed_ingest(db_conn)
        write_state(
            db_conn,
            "src-001",
            default_speaker="DM",
            name_corrections={},
            session_date=None,
        )
        db_conn.commit()
        sc = read_state(db_conn, "src-001")
        # no segments written → no gap warnings
        assert sc.gap_warnings == []


class TestMissingRowRaises:
    def test_missing_ingest_row(self, db_conn: sqlite3.Connection) -> None:
        with pytest.raises(ReadingSidecarError, match="no ingests row"):
            read_state(db_conn, "nonexistent")


class TestExists:
    def test_false_before_seed(self, db_conn: sqlite3.Connection) -> None:
        assert not exists(db_conn, "src-001")

    def test_true_after_seed(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        assert exists(db_conn, "src-001")


class TestSidecarAlias:
    """Sidecar is an alias for IngestState for back-compat."""

    def test_alias(self) -> None:
        assert Sidecar is IngestState

    def test_ingest_state_fields(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        write_state(
            db_conn,
            "src-001",
            default_speaker="DM",
            name_corrections={"A": "B"},
            session_date="2026-03-01",
        )
        db_conn.commit()
        sc = read_state(db_conn, "src-001")
        assert isinstance(sc, IngestState)
        assert sc.state == "reading"
