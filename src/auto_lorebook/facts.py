"""DB-backed fact store.

Public API (conn first):
    FactRow, FactTargetRow, FactError
    VALID_STATUSES
    create_fact_with_target, get_fact, list_facts_by_entity, update_status
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    import sqlite3

VALID_STATUSES: frozenset[str] = frozenset({
    "authoritative",
    "trustworthy",
    "hearsay",
    "disproven",
})


class FactError(ValueError):
    """Base error for fact store operations."""


@dataclass(frozen=True)
class FactRow:
    """One row from the facts table."""

    id: str
    text: str
    raw_transcript_span: str
    text_corrects_transcript: bool
    text_source: str | None
    edited_by_human: bool
    edited_at: str | None
    source_id: str
    locator: str
    speaker: str | None
    status: str
    status_reason: str | None
    session_date: str | None
    approved_at: str
    created_by_ingest: str
    claim_group_id: str | None
    corrections_applied: list[dict]
    inputs_json: str | None


@dataclass(frozen=True)
class FactTargetRow:
    """One row from the fact_targets table."""

    fact_id: str
    entity_category: str
    entity_slug: str
    section: str


def _fact_from_row(row: sqlite3.Row) -> FactRow:
    corrections_raw = row["corrections_applied_json"]
    try:
        corrections = json.loads(corrections_raw) if corrections_raw else []
    except (json.JSONDecodeError, TypeError):
        corrections = []
    return FactRow(
        id=row["id"],
        text=row["text"],
        raw_transcript_span=row["raw_transcript_span"],
        text_corrects_transcript=bool(row["text_corrects_transcript"]),
        text_source=row["text_source"],
        edited_by_human=bool(row["edited_by_human"]),
        edited_at=row["edited_at"],
        source_id=row["source_id"],
        locator=row["locator"],
        speaker=row["speaker"],
        status=row["status"],
        status_reason=row["status_reason"],
        session_date=row["session_date"],
        approved_at=row["approved_at"],
        created_by_ingest=row["created_by_ingest"],
        claim_group_id=row["claim_group_id"],
        corrections_applied=corrections,
        inputs_json=row["inputs_json"],
    )


def _facts_from_rows(rows: list[sqlite3.Row]) -> list[FactRow]:
    return [_fact_from_row(r) for r in rows]


def create_fact_with_target(
    conn: sqlite3.Connection,
    *,
    fact_id: str,
    text: str,
    raw_transcript_span: str,
    text_corrects_transcript: bool,
    source_id: str,
    locator: str,
    status: str,
    approved_at: str,
    created_by_ingest: str,
    entity_category: str,
    entity_slug: str,
    section: str,
    by: str,
    text_source: str | None = None,
    edited_by_human: bool = False,
    edited_at: str | None = None,
    speaker: str | None = None,
    status_reason: str | None = None,
    session_date: str | None = None,
    corrections_applied: list[dict] | None = None,
    inputs_json: str | None = None,
) -> FactRow:
    """INSERT facts + fact_targets + fact_status_history. Caller owns the tx."""
    if status not in VALID_STATUSES:
        msg = f"invalid status {status!r}; must be one of {sorted(VALID_STATUSES)}"
        raise FactError(msg)
    corrections_json = json.dumps(corrections_applied or [])
    try:
        conn.execute(
            """
            INSERT INTO facts (
                id, text, raw_transcript_span, text_corrects_transcript,
                text_source, edited_by_human, edited_at,
                source_id, locator, speaker,
                status, status_reason, session_date,
                approved_at, created_by_ingest, claim_group_id,
                corrections_applied_json, inputs_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)
            """,
            (
                fact_id,
                text,
                raw_transcript_span,
                int(text_corrects_transcript),
                text_source,
                int(edited_by_human),
                edited_at,
                source_id,
                locator,
                speaker,
                status,
                status_reason,
                session_date,
                approved_at,
                created_by_ingest,
                corrections_json,
                inputs_json,
            ),
        )
    except Exception as exc:
        msg = f"create_fact_with_target failed for {fact_id}: {exc}"
        raise FactError(msg) from exc

    conn.execute(
        """
        INSERT INTO fact_targets (fact_id, entity_category, entity_slug, section)
        VALUES (?, ?, ?, ?)
        """,
        (fact_id, entity_category, entity_slug, section),
    )
    conn.execute(
        """
        INSERT INTO fact_status_history (fact_id, status, at, by, reason)
        VALUES (?, ?, ?, ?, ?)
        """,
        (fact_id, status, approved_at, by, status_reason),
    )

    row = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
    if row is None:  # pragma: no cover
        msg = (
            f"create_fact_with_target: get after insert returned nothing for {fact_id}"
        )
        raise FactError(msg)
    return _fact_from_row(row)


def get_fact(conn: sqlite3.Connection, fact_id: str) -> FactRow | None:
    """Return FactRow or None."""
    row = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
    return _fact_from_row(row) if row else None


def list_facts_by_entity(
    conn: sqlite3.Connection,
    entity_category: str,
    entity_slug: str,
) -> list[FactRow]:
    """List facts targeting entity; sorted by (approved_at, id)."""
    rows = conn.execute(
        """
        SELECT f.* FROM facts f
        JOIN fact_targets ft ON ft.fact_id = f.id
        WHERE ft.entity_category=? AND ft.entity_slug=?
        ORDER BY f.approved_at, f.id
        """,
        (entity_category, entity_slug),
    ).fetchall()
    return _facts_from_rows(rows)


def update_status(
    conn: sqlite3.Connection,
    fact_id: str,
    status: str,
    *,
    by: str,
    reason: str | None = None,
    when: str | None = None,
) -> None:
    """UPDATE facts.status + INSERT history row."""
    if status not in VALID_STATUSES:
        msg = f"invalid status {status!r}; must be one of {sorted(VALID_STATUSES)}"
        raise FactError(msg)
    now = when or format_iso_now()
    cur = conn.execute(
        "UPDATE facts SET status=? WHERE id=?",
        (status, fact_id),
    )
    if cur.rowcount == 0:
        msg = f"fact not found: {fact_id}"
        raise FactError(msg)
    conn.execute(
        """
        INSERT INTO fact_status_history (fact_id, status, at, by, reason)
        VALUES (?, ?, ?, ?, ?)
        """,
        (fact_id, status, now, by, reason),
    )
