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

# v1 DDL frozen here so migration 001 never drifts with ddl.py edits.
_V1_SOURCES = """
CREATE TABLE sources (
    source_id       TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL CHECK(source_type IN ('youtube','srt','text')),
    source_url      TEXT,
    title           TEXT,
    duration_seconds INTEGER,
    caption_type    TEXT CHECK(caption_type IN ('manual','auto-generated','n/a')
                              OR caption_type IS NULL),
    fetched_at      TEXT NOT NULL,
    session_date    TEXT,
    context_json    TEXT NOT NULL DEFAULT '{}'
)
"""

_V1_STMTS = (
    """
CREATE TABLE schema_version (
    version INTEGER NOT NULL PRIMARY KEY CHECK(version >= 1)
)
""",
    _V1_SOURCES,
    """
CREATE TABLE entities (
    slug                    TEXT NOT NULL,
    category                TEXT NOT NULL CHECK(category IN (
                                'characters','locations','factions',
                                'events','items','concepts')),
    canonical_name          TEXT NOT NULL,
    superseded_by_category  TEXT,
    superseded_by_slug      TEXT,
    created_at              TEXT NOT NULL,
    created_by_ingest       TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    PRIMARY KEY (category, slug),
    FOREIGN KEY (superseded_by_category, superseded_by_slug)
        REFERENCES entities(category, slug) ON DELETE SET NULL
)
""",
    """
CREATE TABLE aliases (
    entity_category     TEXT NOT NULL,
    entity_slug         TEXT NOT NULL,
    name                TEXT NOT NULL,
    name_normalized     TEXT NOT NULL,
    added_by_ingest     TEXT NOT NULL,
    added_at            TEXT NOT NULL,
    source              TEXT NOT NULL CHECK(source IN (
                            'hand-edited','alias-confirmation','stub-creation',
                            'promoted-from-merge','cli-edit')),
    PRIMARY KEY (entity_category, entity_slug, name_normalized),
    FOREIGN KEY (entity_category, entity_slug)
        REFERENCES entities(category, slug) ON DELETE CASCADE
)
""",
    """
CREATE TABLE facts (
    id                          TEXT PRIMARY KEY,
    text                        TEXT NOT NULL,
    raw_transcript_span         TEXT NOT NULL,
    text_corrects_transcript    INTEGER NOT NULL
                                    CHECK(text_corrects_transcript IN (0,1)),
    text_source                 TEXT,
    edited_by_human             INTEGER NOT NULL DEFAULT 0,
    edited_at                   TEXT,
    source_id                   TEXT NOT NULL,
    locator                     TEXT NOT NULL,
    speaker                     TEXT,
    status                      TEXT NOT NULL CHECK(status IN (
                                    'authoritative','trustworthy','hearsay','disproven')),
    status_reason               TEXT,
    session_date                TEXT,
    approved_at                 TEXT NOT NULL,
    created_by_ingest           TEXT NOT NULL,
    claim_group_id              TEXT,
    corrections_applied_json    TEXT NOT NULL DEFAULT '[]',
    inputs_json                 TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(source_id) ON DELETE RESTRICT
)
""",
    """
CREATE TABLE fact_targets (
    fact_id         TEXT NOT NULL,
    entity_category TEXT NOT NULL,
    entity_slug     TEXT NOT NULL,
    section         TEXT NOT NULL,
    PRIMARY KEY (fact_id, entity_category, entity_slug),
    FOREIGN KEY (fact_id) REFERENCES facts(id) ON DELETE CASCADE,
    FOREIGN KEY (entity_category, entity_slug)
        REFERENCES entities(category, slug) ON DELETE CASCADE
)
""",
    """
CREATE TABLE fact_refs (
    from_fact_id    TEXT NOT NULL,
    to_fact_id      TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK(kind IN (
                        'supersedes','contradicts','corroborates','qualifies')),
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    created_by_ingest TEXT,
    note            TEXT,
    PRIMARY KEY (from_fact_id, to_fact_id, kind),
    FOREIGN KEY (from_fact_id) REFERENCES facts(id) ON DELETE CASCADE,
    FOREIGN KEY (to_fact_id) REFERENCES facts(id) ON DELETE CASCADE,
    CHECK (from_fact_id != to_fact_id)
)
""",
    """
CREATE TABLE fact_status_history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id TEXT NOT NULL,
    status  TEXT NOT NULL CHECK(status IN (
                'authoritative','trustworthy','hearsay','disproven')),
    at      TEXT NOT NULL,
    by      TEXT NOT NULL,
    reason  TEXT,
    FOREIGN KEY (fact_id) REFERENCES facts(id) ON DELETE CASCADE
)
""",
    """
CREATE TABLE wiki_context (
    id                      INTEGER PRIMARY KEY CHECK(id = 1),
    setting_name            TEXT,
    setting_description     TEXT,
    naming_conventions      TEXT,
    interpretation_defaults TEXT,
    recurring_speakers_json TEXT NOT NULL DEFAULT '[]',
    updated_at              TEXT NOT NULL
)
""",
    """
CREATE TABLE transcription_corrections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_text       TEXT NOT NULL,
    to_text         TEXT NOT NULL,
    first_seen_in   TEXT NOT NULL,
    promoted_at     TEXT NOT NULL,
    notes           TEXT,
    UNIQUE (from_text, to_text),
    FOREIGN KEY (first_seen_in) REFERENCES sources(source_id) ON DELETE RESTRICT
)
""",
    """
CREATE TABLE correction_also_seen_in (
    correction_id   INTEGER NOT NULL,
    source_id       TEXT NOT NULL,
    PRIMARY KEY (correction_id, source_id),
    FOREIGN KEY (correction_id)
        REFERENCES transcription_corrections(id) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(source_id) ON DELETE CASCADE
)
""",
    """
CREATE TABLE ingests (
    ingest_id               TEXT PRIMARY KEY,
    source_id               TEXT NOT NULL,
    started_at              TEXT NOT NULL,
    state                   TEXT NOT NULL CHECK(state IN (
                                'reading','planned','extracted',
                                'reviewing','done','rejected')),
    default_speaker         TEXT,
    name_corrections_json   TEXT NOT NULL DEFAULT '{}',
    session_date            TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(source_id) ON DELETE RESTRICT
)
""",
    """
CREATE TABLE segments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ingest_id       TEXT NOT NULL,
    segment_id      TEXT NOT NULL,
    start           TEXT NOT NULL,
    end             TEXT NOT NULL,
    title           TEXT NOT NULL,
    speaker         TEXT,
    notes           TEXT,
    segment_status  TEXT NOT NULL DEFAULT 'draft' CHECK(segment_status IN (
                        'draft','accepted','flagged','regenerating')),
    overrides_json  TEXT NOT NULL DEFAULT '[]',
    UNIQUE (ingest_id, segment_id),
    FOREIGN KEY (ingest_id) REFERENCES ingests(ingest_id) ON DELETE CASCADE
)
""",
    """
CREATE TABLE segment_bullets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_pk      INTEGER NOT NULL,
    bullet_index    INTEGER NOT NULL,
    text            TEXT NOT NULL,
    anchor          TEXT NOT NULL,
    locator_hint    TEXT NOT NULL,
    UNIQUE (segment_pk, bullet_index),
    FOREIGN KEY (segment_pk) REFERENCES segments(id) ON DELETE CASCADE
)
""",
    """
CREATE TABLE plan_routes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    ingest_id               TEXT NOT NULL,
    claim_group_id          TEXT NOT NULL,
    target_entity_category  TEXT,
    target_entity_slug      TEXT,
    target_entity_name      TEXT NOT NULL,
    entity_state            TEXT NOT NULL CHECK(entity_state IN ('existing','new')),
    proposed_section        TEXT NOT NULL,
    proposed_speaker        TEXT,
    proposed_status         TEXT NOT NULL,
    proposed_status_reason  TEXT,
    locator                 TEXT NOT NULL,
    locator_hint            TEXT NOT NULL,
    reading_section         TEXT NOT NULL,
    reading_bullet_index    INTEGER NOT NULL,
    rationale               TEXT,
    matched_via             TEXT,
    UNIQUE (ingest_id, claim_group_id, target_entity_name),
    FOREIGN KEY (ingest_id) REFERENCES ingests(ingest_id) ON DELETE CASCADE
)
""",
    """
CREATE TABLE proposals (
    proposal_id                 TEXT PRIMARY KEY,
    ingest_id                   TEXT NOT NULL,
    plan_route_id               INTEGER NOT NULL,
    proposal_type               TEXT NOT NULL CHECK(proposal_type IN (
                                    'new_fact','new_entity_with_facts')),
    target_entity_name          TEXT NOT NULL,
    proposed_id                 TEXT NOT NULL,
    claim_group_id              TEXT NOT NULL,
    text                        TEXT NOT NULL,
    raw_transcript_span         TEXT NOT NULL,
    text_corrects_transcript    INTEGER NOT NULL,
    corrections_applied_json    TEXT NOT NULL DEFAULT '[]',
    source_id                   TEXT NOT NULL,
    locator                     TEXT NOT NULL,
    speaker                     TEXT,
    status                      TEXT NOT NULL,
    status_reason               TEXT,
    session_date                TEXT,
    section                     TEXT NOT NULL,
    reading_section             TEXT NOT NULL,
    reading_bullet_index        INTEGER NOT NULL,
    context_before              TEXT,
    context_after               TEXT,
    extractor_flagged           INTEGER NOT NULL DEFAULT 0,
    hint_widened                INTEGER NOT NULL DEFAULT 0,
    inputs_json                 TEXT,
    FOREIGN KEY (ingest_id) REFERENCES ingests(ingest_id) ON DELETE CASCADE,
    FOREIGN KEY (plan_route_id) REFERENCES plan_routes(id) ON DELETE CASCADE
)
""",
    # indexes
    "CREATE INDEX idx_entities_canonical_name ON entities(canonical_name)",
    "CREATE INDEX idx_aliases_normalized ON aliases(name_normalized)",
    "CREATE INDEX idx_facts_claim_group ON facts(claim_group_id)",
    "CREATE INDEX idx_facts_source ON facts(source_id)",
    "CREATE INDEX idx_facts_ingest ON facts(created_by_ingest)",
    (
        "CREATE INDEX idx_fact_targets_entity"
        " ON fact_targets(entity_category, entity_slug)"
    ),
    "CREATE INDEX idx_fact_refs_to ON fact_refs(to_fact_id)",
    "CREATE INDEX idx_fact_status_history_fact ON fact_status_history(fact_id)",
    "CREATE INDEX idx_plan_routes_group ON plan_routes(ingest_id, claim_group_id)",
    "CREATE INDEX idx_proposals_group ON proposals(ingest_id, claim_group_id)",
)

# v2 sources DDL (adds 'markdown' to source_type CHECK); used by migration 002.
_V2_SOURCES = """
CREATE TABLE sources (
    source_id       TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL CHECK(source_type IN (
                        'youtube','srt','text','markdown')),
    source_url      TEXT,
    title           TEXT,
    duration_seconds INTEGER,
    caption_type    TEXT CHECK(caption_type IN ('manual','auto-generated','n/a')
                              OR caption_type IS NULL),
    fetched_at      TEXT NOT NULL,
    session_date    TEXT,
    context_json    TEXT NOT NULL DEFAULT '{}'
)
"""


def _migration_001_initial(conn: sqlite3.Connection) -> None:
    """Create all v1 tables, indexes, and seed singleton rows."""
    for stmt in _V1_STMTS:
        conn.execute(stmt)

    now = datetime.now(UTC).isoformat()
    conn.execute("INSERT INTO wiki_context(id, updated_at) VALUES (1, ?)", (now,))
    conn.execute("INSERT INTO schema_version(version) VALUES (1)")


def _migration_002_widen_source_type(conn: sqlite3.Connection) -> None:
    """Widen sources.source_type CHECK to include 'markdown'.

    SQLite cannot ALTER a CHECK constraint; uses create-copy-drop-rename.
    """
    conn.execute(
        _V2_SOURCES.replace("CREATE TABLE sources", "CREATE TABLE sources_new")
    )
    conn.execute("INSERT INTO sources_new SELECT * FROM sources")
    conn.execute("DROP TABLE sources")
    conn.execute("ALTER TABLE sources_new RENAME TO sources")
    conn.execute("UPDATE schema_version SET version = 2")


# v3 segments DDL (fixes segment_status CHECK + adds flags_json); used by migration 003.
_V3_SEGMENTS = """
CREATE TABLE segments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ingest_id       TEXT NOT NULL,
    segment_id      TEXT NOT NULL,
    start           TEXT NOT NULL,
    end             TEXT NOT NULL,
    title           TEXT NOT NULL,
    speaker         TEXT,
    notes           TEXT,
    segment_status  TEXT NOT NULL DEFAULT 'draft' CHECK(segment_status IN (
                        'draft','accepted','skipped','regenerating')),
    overrides_json  TEXT NOT NULL DEFAULT '[]',
    flags_json      TEXT NOT NULL DEFAULT '[]',
    UNIQUE (ingest_id, segment_id),
    FOREIGN KEY (ingest_id) REFERENCES ingests(ingest_id) ON DELETE CASCADE
)
"""


def _migration_003_fix_segment_status_and_add_flags_json(
    conn: sqlite3.Connection,
) -> None:
    """Fix segments.segment_status CHECK ('flagged'→'skipped') + add flags_json.

    SQLite cannot ALTER a CHECK constraint; uses create-copy-drop-rename.
    Existing rows are preserved; flags_json defaults to '[]'.
    """
    conn.execute(
        _V3_SEGMENTS.replace("CREATE TABLE segments", "CREATE TABLE segments_new")
    )
    conn.execute(
        "INSERT INTO segments_new "
        "(id, ingest_id, segment_id, start, end, title, speaker, notes, "
        " segment_status, overrides_json) "
        "SELECT id, ingest_id, segment_id, start, end, title, speaker, notes, "
        "       segment_status, overrides_json FROM segments"
    )
    conn.execute("DROP TABLE segments")
    conn.execute("ALTER TABLE segments_new RENAME TO segments")
    conn.execute("UPDATE schema_version SET version = 3")


MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _migration_001_initial,
    _migration_002_widen_source_type,
    _migration_003_fix_segment_status_and_add_flags_json,
)

CURRENT_SCHEMA_VERSION: int = len(MIGRATIONS)
