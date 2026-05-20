"""Tests for reading_assembly.py — wiki-side reading.md assembly."""

from __future__ import annotations

from typing import TYPE_CHECKING

from auto_lorebook.reading_assembly import assemble, build_segment_body
from tests._reading_fixtures import (
    _bullets,
    _info,
    _seed_ingest_in_db,
    _sidecar,
    _structure,
)

if TYPE_CHECKING:
    import sqlite3

# Golden bytes: assembled output that assemble() must produce.
# No reading_status key — file presence is the approval gate.
_GOLDEN = (
    "---\n"
    "schema_version: 1\n"
    "source_id: yt-abc12345678\n"
    "source_name: Session 3\n"
    "source_url: https://youtube.com/watch?v=abc12345678\n"
    "source_type: youtube\n"
    "session_date: null\n"
    "ingested_at: '2026-04-20T14:35:12Z'\n"
    "default_speaker: DM\n"
    "name_corrections: {}\n"
    "---\n"
    "\n"
    "# Reading: Session 3\n"
    "\n"
    "## [[0:00:00-0:02:00]](https://youtube.com/watch?v=abc12345678&t=0) Introduction\n"
    "\n"
    "Speaker: DM\n"
    "\n"
    "_No claims extracted from this segment._\n"
    "\n"
    "## [[0:02:00-0:04:30]]"
    "(https://youtube.com/watch?v=abc12345678&t=120)"
    " Rules discussion: grappling\n"
    "\n"
    "Speaker: mixed\n"
    "\n"
    "_No claims extracted from this segment._\n"
    "\n"
    "## [[0:04:30-0:10:00]]"
    "(https://youtube.com/watch?v=abc12345678&t=270)"
    " Founding of Aldara\n"
    "\n"
    "Speaker: DM\n"
    "\n"
    "- [0:05:47] uncertain name: a place name; unclear\n"
    "\n"
    "- King Theron founded Aldara in the Second Age"
    " [[0:04:32]](https://youtube.com/watch?v=abc12345678&t=272)\n"
    "\n"
    "- The founding displaced an earlier elven presence"
    " [[0:05:14]](https://youtube.com/watch?v=abc12345678&t=314)\n"
)


class TestGoldenByteMatch:
    def test_matches_expected_output(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest_in_db(db_conn)
        result = assemble(
            conn=db_conn,
            ingest_id="yt-abc12345678",
            info=_info(),
            sidecar=_sidecar(),
        )
        assert result == _GOLDEN

    def test_no_reading_status_key(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest_in_db(db_conn)
        result = assemble(
            conn=db_conn,
            ingest_id="yt-abc12345678",
            info=_info(),
            sidecar=_sidecar(),
        )
        assert "reading_status" not in result


class TestNoSourceUrl:
    def test_no_url_segment_headers_plain(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest_in_db(db_conn)
        result = assemble(
            conn=db_conn,
            ingest_id="yt-abc12345678",
            info=_info(source_url=None),
            sidecar=_sidecar(),
        )
        assert "## [0:00:00-0:02:00] Introduction" in result
        assert "[[0:00:00" not in result


class TestNameCorrections:
    def test_corrections_applied_to_segment_headers(
        self, db_conn: sqlite3.Connection
    ) -> None:
        _seed_ingest_in_db(db_conn)
        result = assemble(
            conn=db_conn,
            ingest_id="yt-abc12345678",
            info=_info(),
            sidecar=_sidecar(name_corrections={"Aldara": "Aldaria"}),
        )
        assert "Aldaria" in result

    def test_corrections_in_frontmatter(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest_in_db(db_conn)
        result = assemble(
            conn=db_conn,
            ingest_id="yt-abc12345678",
            info=_info(),
            sidecar=_sidecar(name_corrections={"Aldara": "Aldaria"}),
        )
        assert "Aldara: Aldaria" in result


class TestEmptySegmentMarker:
    def test_empty_body_gets_marker(self, db_conn: sqlite3.Connection) -> None:
        from auto_lorebook import structure_store  # noqa: PLC0415
        from auto_lorebook.structure import Segment, Structure  # noqa: PLC0415

        sid = "yt-abc12345678"
        db_conn.execute(
            "INSERT OR IGNORE INTO sources "
            "(source_id, source_type, fetched_at, context_json) VALUES (?,?,?,?)",
            (sid, "youtube", "2026-01-01T00:00:00Z", "{}"),
        )
        db_conn.execute(
            "INSERT OR IGNORE INTO ingests "
            "(ingest_id, source_id, started_at, state, default_speaker, "
            " name_corrections_json, session_date) "
            "VALUES (?,?,?,'reading','DM','{}',NULL)",
            (sid, sid, "2026-01-01T00:00:00Z"),
        )
        struct = Structure(
            source_id=sid,
            generated_at="2026-01-01T00:00:00Z",
            default_speaker="DM",
            segments=[
                Segment(id="seg-001", start=0.0, end=60.0, title="Intro", speaker="DM")
            ],
        )
        structure_store.write_structure(db_conn, sid, struct)
        # no bullets written — segment body will be the empty marker
        db_conn.commit()

        result = assemble(
            conn=db_conn,
            ingest_id=sid,
            info=_info(),
            sidecar=_sidecar(),
        )
        assert "_No claims extracted from this segment._" in result


class TestPureNoFilesystem:
    def test_returns_string_not_path(self, db_conn: sqlite3.Connection) -> None:
        _seed_ingest_in_db(db_conn)
        result = assemble(
            conn=db_conn,
            ingest_id="yt-abc12345678",
            info=_info(),
            sidecar=_sidecar(),
        )
        assert isinstance(result, str)


class TestFixtureMatchesPipeline:
    """Anchors _bullets() bodies to actual build_segment_body output."""

    def test_bodies_match_build_segment_body(self) -> None:
        from auto_lorebook.timestamps import format_timestamp  # noqa: PLC0415

        structure = _structure()
        bullets = _bullets()
        info = _info()
        for seg in structure.segments:
            flags_raw = [
                {
                    "locator": format_timestamp(f.locator),
                    "span": f.span,
                    "kind": f.kind,
                    "note": f.note,
                }
                for f in structure.uncertainty_flags
                if seg.start <= f.locator < seg.end
            ]
            body = build_segment_body(
                seg_bullets=bullets.segments[seg.id],
                flags=flags_raw,
                source_url=info.source_url,
                name_corrections={},
            )
            # seg-003: bullets + flags; others: empty marker
            if seg.id == "seg-003":
                assert "King Theron" in body
            else:
                assert "_No claims" in body
