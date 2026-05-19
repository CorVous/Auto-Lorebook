"""SQLite connection factory with pragma setup and lazy migration.

Usage::

    conn = db.open(wiki_root / ".wiki-state" / "wiki.db")

The connection uses autocommit mode (``isolation_level=None``); callers
manage transactions explicitly.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from auto_lorebook.db.errors import SchemaVersionTooNewError
from auto_lorebook.db.migrations import CURRENT_SCHEMA_VERSION, MIGRATIONS

_MEMORY = ":memory:"


def open(path: str | Path) -> sqlite3.Connection:  # noqa: A001
    """Open (and migrate) wiki.db at *path*.

    - ``:memory:`` → in-memory DB, no parent dir created.
    - Filesystem path → parent dir created if absent.
    - Applies WAL, foreign_keys, synchronous=NORMAL, busy_timeout pragmas.
    - Migrates lazily from current db_version to CURRENT_SCHEMA_VERSION.
    - Raises :exc:`SchemaVersionTooNewError` if db_version > CURRENT.
    """
    path_str = str(path)
    if path_str != _MEMORY:
        parent = Path(path_str).parent
        parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path_str, isolation_level=None)
    conn.row_factory = sqlite3.Row

    _apply_pragmas(conn, path_str)

    db_version = _detect_version(conn)

    if db_version > CURRENT_SCHEMA_VERSION:
        conn.close()
        raise SchemaVersionTooNewError(db_version, CURRENT_SCHEMA_VERSION)

    if db_version < CURRENT_SCHEMA_VERSION:
        _migrate(conn, db_version)

    return conn


def _apply_pragmas(conn: sqlite3.Connection, path_str: str) -> None:
    """Apply recommended pragmas; WAL is best-effort for :memory:."""
    if path_str != _MEMORY:
        conn.execute("PRAGMA journal_mode=WAL")
    else:
        with contextlib.suppress(sqlite3.OperationalError):
            conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")


def _detect_version(conn: sqlite3.Connection) -> int:
    """Return current schema version; 0 if schema_version table absent."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    row2 = conn.execute("SELECT version FROM schema_version").fetchone()
    return int(row2[0]) if row2 else 0


def _migrate(conn: sqlite3.Connection, from_version: int) -> None:
    """Run migrations from_version+1 … CURRENT inside a single transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        for i in range(from_version, CURRENT_SCHEMA_VERSION):
            MIGRATIONS[i](conn)
        if from_version > 0:
            # update existing version row
            conn.execute(
                "UPDATE schema_version SET version = ?", (CURRENT_SCHEMA_VERSION,)
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
