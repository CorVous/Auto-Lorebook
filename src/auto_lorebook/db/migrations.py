"""Numbered migration functions for wiki.db.

Adding a migration:
1. Define ``_migration_NNN_<description>(conn)`` at the bottom.
2. Append it to ``MIGRATIONS``.
``CURRENT_SCHEMA_VERSION`` updates automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

from auto_lorebook.db import ddl


def _migration_001_initial(conn: sqlite3.Connection) -> None:
    """Create all v1 tables, indexes, and seed singleton rows."""
    stmts = [
        ddl.SCHEMA_VERSION,
        ddl.SOURCES,
        ddl.ENTITIES,
        ddl.ALIASES,
        ddl.FACTS,
        ddl.FACT_TARGETS,
        ddl.FACT_REFS,
        ddl.FACT_STATUS_HISTORY,
        ddl.WIKI_CONTEXT,
        ddl.TRANSCRIPTION_CORRECTIONS,
        ddl.CORRECTION_ALSO_SEEN_IN,
        ddl.INGESTS,
        ddl.SEGMENTS,
        ddl.SEGMENT_BULLETS,
        ddl.PLAN_ROUTES,
        ddl.PROPOSALS,
        # indexes
        ddl.IDX_ENTITIES_CANONICAL_NAME,
        ddl.IDX_ALIASES_NORMALIZED,
        ddl.IDX_FACTS_CLAIM_GROUP,
        ddl.IDX_FACTS_SOURCE,
        ddl.IDX_FACTS_INGEST,
        ddl.IDX_FACT_TARGETS_ENTITY,
        ddl.IDX_FACT_REFS_TO,
        ddl.IDX_FACT_STATUS_HISTORY_FACT,
        ddl.IDX_PLAN_ROUTES_GROUP,
        ddl.IDX_PROPOSALS_GROUP,
    ]
    for stmt in stmts:
        conn.execute(stmt)

    now = datetime.now(UTC).isoformat()
    conn.execute("INSERT INTO wiki_context(id, updated_at) VALUES (1, ?)", (now,))
    conn.execute("INSERT INTO schema_version(version) VALUES (1)")


MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_migration_001_initial,)

CURRENT_SCHEMA_VERSION: int = len(MIGRATIONS)
