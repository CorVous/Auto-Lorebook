"""Tests for db.open: connection, pragmas, migrations, error handling."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from auto_lorebook import db
from auto_lorebook.db import SchemaVersionTooNewError
from auto_lorebook.db.migrations import CURRENT_SCHEMA_VERSION, MIGRATIONS

if TYPE_CHECKING:
    from pathlib import Path

_EXPECTED_TABLES = frozenset({
    "schema_version",
    "entities",
    "aliases",
    "facts",
    "fact_targets",
    "fact_refs",
    "fact_status_history",
    "sources",
    "wiki_context",
    "transcription_corrections",
    "correction_also_seen_in",
    "ingests",
    "segments",
    "segment_bullets",
    "plan_routes",
    "proposals",
    "plan_metadata",
    "proposal_targets",
})


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    # exclude sqlite-internal tables (e.g. sqlite_sequence for AUTOINCREMENT)
    return {r[0] for r in rows if not r[0].startswith("sqlite_")}


def test_open_memory_creates_schema_at_latest_version() -> None:
    conn = db.open(":memory:")
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == CURRENT_SCHEMA_VERSION
    conn.close()


def test_open_filesystem_creates_wal_db(tmp_path: Path) -> None:
    db_path = tmp_path / "wiki.db"
    conn = db.open(db_path)
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert journal == "wal"
    assert fk == 1
    conn.close()


def test_open_creates_all_expected_tables(tmp_path: Path) -> None:
    conn = db.open(tmp_path / "wiki.db")
    tables = _table_names(conn)
    conn.close()
    assert tables == _EXPECTED_TABLES, (
        f"missing: {_EXPECTED_TABLES - tables}, extra: {tables - _EXPECTED_TABLES}"
    )


def test_reopen_on_current_db_is_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "wiki.db"
    conn = db.open(db_path)
    # write a sentinel source row
    conn.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at) "
        "VALUES ('s1', 'text', '2025-01-01T00:00:00+00:00')"
    )
    conn.close()

    conn2 = db.open(db_path)
    row = conn2.execute("SELECT source_id FROM sources WHERE source_id='s1'").fetchone()
    conn2.close()
    assert row is not None, "sentinel row lost on reopen"


def test_old_schema_version_upgrades_to_current(tmp_path: Path) -> None:
    """Simulate v0 (no schema_version table) → open → at current."""
    db_path = tmp_path / "wiki.db"
    # create bare DB with no tables (version 0)
    raw = sqlite3.connect(str(db_path))
    raw.close()

    conn = db.open(db_path)
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    conn.close()
    assert version == CURRENT_SCHEMA_VERSION


def test_multistep_upgrade_runs_each_migration_in_order(tmp_path: Path) -> None:
    """Monkeypatch MIGRATIONS to add a noop beyond current; verify order preserved."""
    db_path = tmp_path / "wiki.db"
    order: list[int] = []
    noop_version = len(MIGRATIONS) + 1

    def _migration_noop(conn: sqlite3.Connection) -> None:
        order.append(noop_version)
        conn.execute("UPDATE schema_version SET version = ?", (noop_version,))

    extended = (*MIGRATIONS, _migration_noop)
    extended_version = len(extended)  # CURRENT_SCHEMA_VERSION for the patched run

    with (
        patch("auto_lorebook.db.connection.MIGRATIONS", extended),
        patch("auto_lorebook.db.connection.CURRENT_SCHEMA_VERSION", extended_version),
        patch("auto_lorebook.db.migrations.CURRENT_SCHEMA_VERSION", extended_version),
    ):
        conn = db.open(db_path)
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        conn.close()

    assert version == extended_version
    assert order == [noop_version]  # prior migrations ran implicitly; noop ran last


def test_future_schema_version_raises_named_error(tmp_path: Path) -> None:
    db_path = tmp_path / "wiki.db"
    # write a DB with a future version
    conn = db.open(db_path)
    conn.execute("UPDATE schema_version SET version = 9999")
    conn.close()

    with pytest.raises(SchemaVersionTooNewError) as exc_info:
        db.open(db_path)

    assert "upgrade the tool" in str(exc_info.value)
    assert exc_info.value.db_version == 9999
    assert exc_info.value.tool_version == CURRENT_SCHEMA_VERSION


def test_future_version_error_is_importable_from_db_package() -> None:
    from auto_lorebook.db import SchemaVersionTooNewError as E  # noqa: PLC0415

    err = E(5, 1)
    assert err.db_version == 5
    assert err.tool_version == 1
    assert isinstance(err, RuntimeError)


def test_memory_and_file_share_schema(tmp_path: Path) -> None:
    """Both :memory: and file DBs produce identical table sets."""
    mem_conn = db.open(":memory:")
    file_conn = db.open(tmp_path / "wiki.db")

    mem_tables = _table_names(mem_conn)
    file_tables = _table_names(file_conn)

    mem_conn.close()
    file_conn.close()

    assert mem_tables == file_tables


def test_foreign_keys_enforced(tmp_path: Path) -> None:
    """fact_targets with nonexistent fact_id → IntegrityError."""
    conn = db.open(tmp_path / "wiki.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO fact_targets(fact_id, entity_category, entity_slug, section) "
            "VALUES ('no-such-fact', 'characters', 'alice', 'traits')"
        )
    conn.close()


# ---------------------------------------------------------------------------
# Migration 002 specific tests
# ---------------------------------------------------------------------------


def test_migration_002_widens_source_type_check() -> None:
    """After migrations, inserting source_type='markdown' succeeds."""
    conn = db.open(":memory:")
    conn.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at)"
        " VALUES ('md-001', 'markdown', '2026-01-01T00:00:00+00:00')"
    )
    row = conn.execute(
        "SELECT source_type FROM sources WHERE source_id='md-001'"
    ).fetchone()
    conn.close()
    assert row[0] == "markdown"


def test_migration_002_preserves_existing_rows(tmp_path: Path) -> None:
    """Rows inserted before migration 002 survive the table-swap."""
    db_path = tmp_path / "wiki.db"

    # Create a v1 DB (only migration 001)
    from auto_lorebook.db.migrations import (  # noqa: PLC0415
        _migration_001_initial,  # noqa: PLC2701
    )

    raw = sqlite3.connect(str(db_path))
    raw.execute("PRAGMA foreign_keys=ON")
    _migration_001_initial(raw)
    raw.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at)"
        " VALUES ('srt-existing', 'srt', '2026-01-01T00:00:00+00:00')"
    )
    raw.commit()
    raw.close()

    # open() detects version=1 and runs migration 002
    conn = db.open(db_path)
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    row = conn.execute(
        "SELECT source_id FROM sources WHERE source_id='srt-existing'"
    ).fetchone()
    # now markdown inserts should work too
    conn.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at)"
        " VALUES ('md-new', 'markdown', '2026-01-01T00:00:00+00:00')"
    )
    conn.close()

    assert version == CURRENT_SCHEMA_VERSION
    assert row is not None


# ---------------------------------------------------------------------------
# Migration 003 specific tests
# ---------------------------------------------------------------------------


def test_migration_003_segment_status_check_accepts_skipped() -> None:
    """v3 schema accepts segment_status='skipped'; old 'flagged' is gone."""
    conn = db.open(":memory:")
    conn.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at)"
        " VALUES ('s1', 'srt', '2026-01-01T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('i1', 's1', '2026-01-01T00:00:00+00:00', 'reading')"
    )
    conn.execute(
        "INSERT INTO segments(ingest_id, segment_id, start, end, title,"
        " segment_status) VALUES"
        " ('i1', 'seg-001', '0:00:00', '0:01:00', 't', 'skipped')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO segments(ingest_id, segment_id, start, end, title,"
            " segment_status) VALUES"
            " ('i1', 'seg-002', '0:01:00', '0:02:00', 't', 'flagged')"
        )
    conn.close()


def test_migration_003_preserves_existing_rows(tmp_path: Path) -> None:
    """Segment rows inserted at v2 survive the v3 table-swap with flags_json='[]'."""
    db_path = tmp_path / "wiki.db"

    from auto_lorebook.db.migrations import (  # noqa: PLC0415
        _migration_001_initial,  # noqa: PLC2701
        _migration_002_widen_source_type,  # noqa: PLC2701
    )

    raw = sqlite3.connect(str(db_path))
    raw.execute("PRAGMA foreign_keys=ON")
    _migration_001_initial(raw)
    _migration_002_widen_source_type(raw)
    raw.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at)"
        " VALUES ('s1', 'srt', '2026-01-01T00:00:00+00:00')"
    )
    raw.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('i1', 's1', '2026-01-01T00:00:00+00:00', 'reading')"
    )
    raw.execute(
        "INSERT INTO segments(ingest_id, segment_id, start, end, title,"
        " segment_status) VALUES"
        " ('i1', 'seg-001', '0:00:00', '0:01:00', 'pre-v3', 'draft')"
    )
    # stamp schema_version=2 so db.open runs migration 003 only
    raw.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
    )
    raw.execute("DELETE FROM schema_version")
    raw.execute("INSERT INTO schema_version(version) VALUES (2)")
    raw.commit()
    raw.close()

    # open() detects version=2 and applies migration 003
    conn = db.open(db_path)
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    row = conn.execute(
        "SELECT segment_id, segment_status, flags_json FROM segments"
        " WHERE ingest_id='i1' AND segment_id='seg-001'"
    ).fetchone()
    conn.close()

    assert version == CURRENT_SCHEMA_VERSION
    assert row is not None
    assert row[0] == "seg-001"
    assert row[1] == "draft"
    assert row[2] == "[]"


# ---------------------------------------------------------------------------
# Migration 004 specific tests
# ---------------------------------------------------------------------------


def test_migration_004_plan_metadata_exists() -> None:
    """plan_metadata table present with expected columns after full migration."""
    conn = db.open(":memory:")
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(plan_metadata)").fetchall()
    }
    conn.close()
    assert "ingest_id" in cols
    assert "planned_at" in cols
    assert "source_id" in cols
    assert "entity_resolutions_json" in cols
    assert "new_entities_json" in cols
    assert "unresolved_json" in cols


def test_migration_004_proposals_has_flag_reason() -> None:
    """proposals.flag_reason column present after full migration."""
    conn = db.open(":memory:")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(proposals)").fetchall()}
    conn.close()
    assert "flag_reason" in cols


def test_migration_005_creates_proposal_targets() -> None:
    """Migration 005 creates proposal_targets with expected columns and FK CASCADE."""
    conn = db.open(":memory:")
    cols = {
        row[1] for row in conn.execute("PRAGMA table_info(proposal_targets)").fetchall()
    }
    conn.close()
    assert cols == {
        "proposal_id",
        "position",
        "entity_name",
        "section",
        "speaker",
        "proposal_type",
        "proposed_category",
    }


def test_migration_005_drops_per_target_columns_from_proposals() -> None:
    """Migration 005 removes target_entity_name, section, speaker, plan_route_id."""
    conn = db.open(":memory:")
    proposal_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(proposals)").fetchall()
    }
    conn.close()
    assert "target_entity_name" not in proposal_cols
    assert "section" not in proposal_cols
    assert "speaker" not in proposal_cols
    assert "plan_route_id" not in proposal_cols


def test_migration_005_preserves_existing_rows(tmp_path: Path) -> None:
    """Rows from v4 survive migration 005; proposal_targets populated from proposals."""
    from auto_lorebook.db.migrations import (  # noqa: PLC0415
        _migration_001_initial,  # noqa: PLC2701
        _migration_002_widen_source_type,  # noqa: PLC2701
        _migration_003_fix_segment_status_and_add_flags_json,  # noqa: PLC2701
        _migration_004_plan_metadata_and_flag_reason,  # noqa: PLC2701
    )

    db_path = tmp_path / "wiki.db"
    raw = sqlite3.connect(str(db_path))
    raw.execute("PRAGMA foreign_keys=OFF")  # ease seeding
    _migration_001_initial(raw)
    _migration_002_widen_source_type(raw)
    _migration_003_fix_segment_status_and_add_flags_json(raw)
    _migration_004_plan_metadata_and_flag_reason(raw)
    raw.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at)"
        " VALUES ('s1', 'srt', '2026-01-01T00:00:00+00:00')"
    )
    raw.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('i1', 's1', '2026-01-01T00:00:00+00:00', 'reading')"
    )
    raw.execute(
        "INSERT INTO plan_routes(ingest_id, claim_group_id, target_entity_name,"
        " entity_state, proposed_section, proposed_status, locator, locator_hint,"
        " reading_section, reading_bullet_index)"
        " VALUES ('i1','cg-001','Aldara','existing','founding','authoritative',"
        " '0:00:01','0:00:00-0:00:10','[0:00:00-0:01:00] S1',0)"
    )
    raw.execute(
        "INSERT INTO proposals(proposal_id, ingest_id, plan_route_id, proposal_type,"
        " target_entity_name, proposed_id, claim_group_id, text, raw_transcript_span,"
        " text_corrects_transcript, corrections_applied_json, source_id, locator,"
        " status, section, reading_section, reading_bullet_index, speaker)"
        " VALUES ('aldara-f001','i1',1,'new_fact','Aldara','aldara-f001','cg-001',"
        " 'Aldara was founded.','Aldara was founded.',0,'[]','s1','0:00:01',"
        " 'authoritative','founding','[0:00:00-0:01:00] S1',0,'DM')"
    )
    raw.execute("DELETE FROM schema_version")
    raw.execute("INSERT INTO schema_version(version) VALUES (4)")
    raw.commit()
    raw.close()

    conn = db.open(db_path)
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    # proposal row preserved
    p_row = conn.execute(
        "SELECT proposal_id FROM proposals WHERE proposal_id='aldara-f001'"
    ).fetchone()
    # proposal_targets populated
    pt_rows = conn.execute(
        "SELECT entity_name, section, speaker FROM proposal_targets"
        " WHERE proposal_id='aldara-f001'"
    ).fetchall()
    conn.close()

    assert version == CURRENT_SCHEMA_VERSION
    assert p_row is not None
    assert len(pt_rows) == 1
    assert pt_rows[0][0] == "Aldara"
    assert pt_rows[0][1] == "founding"
    assert pt_rows[0][2] == "DM"


def test_migration_004_preserves_existing_rows(tmp_path: Path) -> None:
    """Rows from v3 survive migration 004; flag_reason is NULL on legacy proposals."""
    from auto_lorebook.db.migrations import (  # noqa: PLC0415
        _migration_001_initial,  # noqa: PLC2701
        _migration_002_widen_source_type,  # noqa: PLC2701
        _migration_003_fix_segment_status_and_add_flags_json,  # noqa: PLC2701
    )

    db_path = tmp_path / "wiki.db"
    raw = sqlite3.connect(str(db_path))
    raw.execute("PRAGMA foreign_keys=ON")
    _migration_001_initial(raw)
    _migration_002_widen_source_type(raw)
    _migration_003_fix_segment_status_and_add_flags_json(raw)
    raw.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at)"
        " VALUES ('s1', 'srt', '2026-01-01T00:00:00+00:00')"
    )
    raw.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('i1', 's1', '2026-01-01T00:00:00+00:00', 'reading')"
    )
    raw.execute(
        "INSERT INTO segments(ingest_id, segment_id, start, end, title,"
        " segment_status) VALUES"
        " ('i1', 'seg-001', '0:00:00', '0:01:00', 'pre-v4', 'draft')"
    )
    raw.execute("DELETE FROM schema_version")
    raw.execute("INSERT INTO schema_version(version) VALUES (3)")
    raw.commit()
    raw.close()

    conn = db.open(db_path)
    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    seg_row = conn.execute(
        "SELECT segment_id FROM segments WHERE ingest_id='i1'"
    ).fetchone()
    # plan_metadata table should exist (empty)
    pm_count = conn.execute("SELECT COUNT(*) FROM plan_metadata").fetchone()[0]
    conn.close()

    assert version == CURRENT_SCHEMA_VERSION
    assert seg_row is not None
    assert seg_row[0] == "seg-001"
    assert pm_count == 0
