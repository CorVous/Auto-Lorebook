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
    """Monkeypatch MIGRATIONS to add a no-op v2; verify order is preserved."""
    db_path = tmp_path / "wiki.db"
    order: list[int] = []

    def _migration_002_noop(conn: sqlite3.Connection) -> None:
        order.append(2)
        conn.execute("UPDATE schema_version SET version = 2")

    extended = (*MIGRATIONS, _migration_002_noop)

    with (
        patch("auto_lorebook.db.connection.MIGRATIONS", extended),
        patch("auto_lorebook.db.connection.CURRENT_SCHEMA_VERSION", 2),
        patch("auto_lorebook.db.migrations.CURRENT_SCHEMA_VERSION", 2),
    ):
        conn = db.open(db_path)
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        conn.close()

    assert version == 2
    assert order == [2]  # migration 1 wrote its own version row; noop ran


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
