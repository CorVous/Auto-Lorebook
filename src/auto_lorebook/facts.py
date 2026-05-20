"""DB-backed fact store.

Public API (conn first):
    FactRow, FactTargetRow, FactRef, FactError
    VALID_STATUSES, VALID_REF_KINDS
    create_fact_with_target, create_fact_with_targets,
    get_fact, list_facts_by_entity, update_status,
    create_ref, delete_ref, list_refs_by_fact
"""

from __future__ import annotations

import json
import sqlite3 as _sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    import sqlite3

VALID_STATUSES: frozenset[str] = frozenset({
    "authoritative",
    "trustworthy",
    "hearsay",
    "disproven",
})

VALID_REF_KINDS: frozenset[str] = frozenset({
    "supersedes",
    "contradicts",
    "corroborates",
    "qualifies",
})

# system actor constants for fact_status_history rows written by ref operations
_SYSTEM_REF_CREATION = "system-ref-creation"
_SYSTEM_REF_DELETION = "system-ref-deletion"


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


@dataclass(frozen=True)
class FactRef:
    """One row from the fact_refs table."""

    from_fact_id: str
    to_fact_id: str
    kind: str
    created_at: str
    created_by: str
    created_by_ingest: str | None
    note: str | None


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


def create_fact_with_targets(
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
    targets: list[tuple[str, str, str]],
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
    """INSERT facts + N fact_targets + fact_status_history. Caller owns the tx.

    `targets`: list of (entity_category, entity_slug, section) tuples.
    Requires at least one target.
    """
    if not targets:
        msg = "at least one target required"
        raise FactError(msg)
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
        msg = f"create_fact_with_targets failed for {fact_id}: {exc}"
        raise FactError(msg) from exc

    for entity_category, entity_slug, section in targets:
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
            f"create_fact_with_targets: get after insert returned nothing for {fact_id}"
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


def create_ref(
    conn: sqlite3.Connection,
    *,
    from_fact_id: str,
    to_fact_id: str,
    kind: str,
    by: str,
    ingest_id: str | None = None,
    note: str | None = None,
    when: str | None = None,
) -> FactRef:
    """INSERT into fact_refs; for supersedes, also flip target status.

    CALLER OWNS THE TX — issues conn.execute only, no BEGIN/COMMIT.
    Raises FactError on invalid kind, duplicate edge, FK violations, self-loop.
    """
    if kind not in VALID_REF_KINDS:
        msg = f"invalid ref kind {kind!r}; must be one of {sorted(VALID_REF_KINDS)}"
        raise FactError(msg)
    now = when or format_iso_now()
    try:
        conn.execute(
            """
            INSERT INTO fact_refs
                (from_fact_id, to_fact_id, kind, created_at, created_by,
                 created_by_ingest, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (from_fact_id, to_fact_id, kind, now, by, ingest_id, note),
        )
    except _sqlite3.IntegrityError as exc:
        msg = f"create_ref failed ({from_fact_id!r} -{kind}-> {to_fact_id!r}): {exc}"
        raise FactError(msg) from exc

    if kind == "supersedes":
        # flip target to disproven + record system history row (always)
        conn.execute(
            "UPDATE facts SET status='disproven' WHERE id=?",
            (to_fact_id,),
        )
        conn.execute(
            """
            INSERT INTO fact_status_history (fact_id, status, at, by, reason)
            VALUES (?, 'disproven', ?, ?, ?)
            """,
            (to_fact_id, now, _SYSTEM_REF_CREATION, f"superseded by {from_fact_id}"),
        )

    return FactRef(
        from_fact_id=from_fact_id,
        to_fact_id=to_fact_id,
        kind=kind,
        created_at=now,
        created_by=by,
        created_by_ingest=ingest_id,
        note=note,
    )


def delete_ref(
    conn: sqlite3.Connection,
    *,
    from_fact_id: str,
    to_fact_id: str,
    kind: str,
    by: str,  # noqa: ARG001 — symmetric API; unused on non-supersedes paths
    ingest_id: str | None = None,  # noqa: ARG001 — symmetric API; reserved
    when: str | None = None,
) -> None:
    """DELETE from fact_refs; for supersedes, restore target status when last edge gone.

    CALLER OWNS THE TX — issues conn.execute only, no BEGIN/COMMIT.
    Raises FactError if edge not found or if supersedes restore has no prior history.
    `by` and `ingest_id` kept for API symmetry; unused on non-supersedes paths.
    """
    if kind not in VALID_REF_KINDS:
        msg = f"invalid ref kind {kind!r}; must be one of {sorted(VALID_REF_KINDS)}"
        raise FactError(msg)
    now = when or format_iso_now()

    cur = conn.execute(
        "DELETE FROM fact_refs WHERE from_fact_id=? AND to_fact_id=? AND kind=?",
        (from_fact_id, to_fact_id, kind),
    )
    if cur.rowcount == 0:
        msg = f"fact_ref not found: ({from_fact_id!r} -{kind}-> {to_fact_id!r})"
        raise FactError(msg)

    if kind != "supersedes":
        return

    # check remaining supersedes edges pointing at the target
    other_count: int = conn.execute(
        "SELECT COUNT(*) FROM fact_refs WHERE to_fact_id=? AND kind='supersedes'",
        (to_fact_id,),
    ).fetchone()[0]
    if other_count > 0:
        # still superseded by other facts — leave as disproven
        return

    # restore most recent non-system status
    row = conn.execute(
        """
        SELECT status FROM fact_status_history
        WHERE fact_id=? AND by NOT IN (?, ?)
        ORDER BY id DESC LIMIT 1
        """,
        (to_fact_id, _SYSTEM_REF_CREATION, _SYSTEM_REF_DELETION),
    ).fetchone()
    if row is None:
        msg = "cannot restore: no prior non-system status in history"
        raise FactError(msg)
    prior_status: str = row[0]
    conn.execute(
        "UPDATE facts SET status=? WHERE id=?",
        (prior_status, to_fact_id),
    )
    conn.execute(
        """
        INSERT INTO fact_status_history (fact_id, status, at, by, reason)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            to_fact_id,
            prior_status,
            now,
            _SYSTEM_REF_DELETION,
            f"supersedes by {from_fact_id} removed",
        ),
    )


_SELECT_REFS = (
    "SELECT from_fact_id, to_fact_id, kind, created_at, created_by,"
    " created_by_ingest, note FROM fact_refs"
    " ORDER BY from_fact_id, to_fact_id, kind"
)
_SELECT_REFS_OUT = (
    "SELECT from_fact_id, to_fact_id, kind, created_at, created_by,"
    " created_by_ingest, note FROM fact_refs"
    " WHERE from_fact_id=?"
    " ORDER BY from_fact_id, to_fact_id, kind"
)
_SELECT_REFS_IN = (
    "SELECT from_fact_id, to_fact_id, kind, created_at, created_by,"
    " created_by_ingest, note FROM fact_refs"
    " WHERE to_fact_id=?"
    " ORDER BY from_fact_id, to_fact_id, kind"
)
_SELECT_REFS_BOTH = (
    "SELECT from_fact_id, to_fact_id, kind, created_at, created_by,"
    " created_by_ingest, note FROM fact_refs"
    " WHERE from_fact_id=? OR to_fact_id=?"
    " ORDER BY from_fact_id, to_fact_id, kind"
)


def list_refs_by_fact(
    conn: sqlite3.Connection,
    fact_id: str,
    direction: Literal["out", "in", "both"] = "both",
) -> list[FactRef]:
    """Return FactRef list for `fact_id`; sorted by (from_fact_id, to_fact_id, kind).

    direction: 'out' — edges where fact_id is source; 'in' — where it's target;
               'both' — union.
    """
    if direction == "out":
        rows = conn.execute(_SELECT_REFS_OUT, (fact_id,)).fetchall()
    elif direction == "in":
        rows = conn.execute(_SELECT_REFS_IN, (fact_id,)).fetchall()
    elif direction == "both":
        rows = conn.execute(_SELECT_REFS_BOTH, (fact_id, fact_id)).fetchall()
    else:
        msg = f"direction must be 'out', 'in', or 'both'; got {direction!r}"
        raise ValueError(msg)

    return [
        FactRef(
            from_fact_id=r[0],
            to_fact_id=r[1],
            kind=r[2],
            created_at=r[3],
            created_by=r[4],
            created_by_ingest=r[5],
            note=r[6],
        )
        for r in rows
    ]
