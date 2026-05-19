"""Tests for structure_store — DB CRUD for segments and segment_bullets."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from auto_lorebook import structure_store as ss
from auto_lorebook.stage1b import ReadingBullets
from auto_lorebook.structure import Segment, Structure
from tests._reading_fixtures import _bullets, _structure

if TYPE_CHECKING:
    import sqlite3

_INGEST_ID = "yt-test-store"


def _seed_ingest(conn: sqlite3.Connection, ingest_id: str = _INGEST_ID) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, source_type, fetched_at, context_json) VALUES (?,?,?,?)",
        (ingest_id, "youtube", "2026-01-01T00:00:00Z", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO ingests "
        "(ingest_id, source_id, started_at, state, default_speaker, "
        " name_corrections_json, session_date) "
        "VALUES (?,?,?,'reading','DM','{}',NULL)",
        (ingest_id, ingest_id, "2026-01-01T00:00:00Z"),
    )
    conn.commit()


class TestWriteAndReadStructure:
    def test_round_trip(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        struct = _structure()
        ss.write_structure(db_conn, _INGEST_ID, struct)
        db_conn.commit()

        result = ss.read_structure(db_conn, _INGEST_ID)
        assert len(result.segments) == len(struct.segments)
        assert result.segments[0].id == "seg-001"
        assert result.segments[0].title == "Introduction"

    def test_replaces_on_second_write(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        ss.write_structure(db_conn, _INGEST_ID, _structure())
        db_conn.commit()
        # overwrite with single-segment structure
        new = Structure(
            source_id=_INGEST_ID,
            generated_at="",
            default_speaker="DM",
            segments=[
                Segment(id="seg-001", start=0.0, end=60.0, title="Only", speaker="DM")
            ],
        )
        ss.write_structure(db_conn, _INGEST_ID, new)
        db_conn.commit()
        result = ss.read_structure(db_conn, _INGEST_ID)
        assert len(result.segments) == 1

    def test_raises_when_no_segments(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        with pytest.raises(ss.StructureStoreError):
            ss.read_structure(db_conn, _INGEST_ID)

    def test_uncertainty_flags_bucketed_to_seg(
        self, db_conn: sqlite3.Connection
    ) -> None:
        _seed_ingest(db_conn)
        struct = _structure()
        ss.write_structure(db_conn, _INGEST_ID, struct)
        db_conn.commit()
        rows = ss.list_segments(db_conn, _INGEST_ID)
        # flag at 347s should land in seg-003 (270-600)
        seg003 = next(r for r in rows if r.segment_id == "seg-003")
        assert seg003.flags  # at least one flag
        assert seg003.flags[0]["kind"] == "name"


class TestWriteAndReadBullets:
    def test_round_trip(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        ss.write_structure(db_conn, _INGEST_ID, _structure())
        bulls = _bullets()
        bulls_patched = ReadingBullets(
            source_id=_INGEST_ID,
            generated_at="",
            segments=bulls.segments,
        )
        ss.write_bullets(db_conn, _INGEST_ID, bulls_patched)
        db_conn.commit()

        result = ss.read_bullets(db_conn, _INGEST_ID)
        assert len(result.segments["seg-003"]) == 2
        assert "King Theron" in result.segments["seg-003"][0].text

    def test_empty_segments_have_no_bullets(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        ss.write_structure(db_conn, _INGEST_ID, _structure())
        db_conn.commit()
        result = ss.read_bullets(db_conn, _INGEST_ID)
        # no bullets written yet
        assert result.segments == {}


class TestSegmentStatusMutation:
    def test_set_segment_status_accepted(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        ss.write_structure(db_conn, _INGEST_ID, _structure())
        db_conn.commit()
        ss.set_segment_status(db_conn, _INGEST_ID, "seg-001", "accepted")
        db_conn.commit()
        seg = ss.get_segment(db_conn, _INGEST_ID, "seg-001")
        assert seg is not None
        assert seg.segment_status == "accepted"

    def test_delete_ingest_segments(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        ss.write_structure(db_conn, _INGEST_ID, _structure())
        db_conn.commit()
        ss.delete_ingest_segments(db_conn, _INGEST_ID)
        db_conn.commit()
        assert ss.list_segments(db_conn, _INGEST_ID) == []

    def test_get_segment_returns_none_when_missing(
        self, db_conn: sqlite3.Connection
    ) -> None:
        _seed_ingest(db_conn)
        result = ss.get_segment(db_conn, _INGEST_ID, "seg-999")
        assert result is None

    def test_list_segments_sorted_by_start(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest(db_conn)
        ss.write_structure(db_conn, _INGEST_ID, _structure())
        db_conn.commit()
        rows = ss.list_segments(db_conn, _INGEST_ID)
        starts = [r.start for r in rows]
        assert starts == sorted(starts)
