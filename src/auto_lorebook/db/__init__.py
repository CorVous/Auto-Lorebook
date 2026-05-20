"""SQLite wiki database — connection, schema DDL, and migrations."""

from __future__ import annotations

from auto_lorebook.db.connection import CURRENT_SCHEMA_VERSION, open  # noqa: A004
from auto_lorebook.db.errors import SchemaVersionTooNewError

__all__ = ["CURRENT_SCHEMA_VERSION", "SchemaVersionTooNewError", "open"]
