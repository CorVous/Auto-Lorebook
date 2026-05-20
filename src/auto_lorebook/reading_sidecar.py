"""DB-backed reading session state (ingests row).

Replaces the old pending/<id>/reading/reading.yaml YAML sidecar.
gap_warnings are derived at read time from segments — not persisted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from auto_lorebook.gap_check import GapWarning  # noqa: TC001 -- used in dataclass field

if TYPE_CHECKING:
    import sqlite3


class ReadingSidecarError(ValueError):
    """Raised when ingests row missing or malformed."""


@dataclass
class IngestState:
    """In-memory representation of reading session state (from ingests row)."""

    ingest_id: str
    source_id: str
    state: str
    default_speaker: str
    name_corrections: dict[str, str] = field(default_factory=dict)
    session_date: str | None = None
    gap_warnings: list[GapWarning] = field(default_factory=list)


# Back-compat alias used in tests and other modules.
Sidecar = IngestState

# Convenience aliases
read = None  # defined below; reassigned after function definition
write = None


def read_state(conn: sqlite3.Connection, ingest_id: str) -> IngestState:
    """Read IngestState from DB.

    Derives gap_warnings by running gap_check over the stored structure.
    :raises ReadingSidecarError: no ingests row for ingest_id.
    """
    from auto_lorebook import gap_check as gap_check_mod  # noqa: PLC0415
    from auto_lorebook import structure_store  # noqa: PLC0415

    row = conn.execute(
        "SELECT source_id, state, default_speaker, name_corrections_json, session_date "
        "FROM ingests WHERE ingest_id=?",
        (ingest_id,),
    ).fetchone()
    if row is None:
        msg = f"{ingest_id}: no ingests row found"
        raise ReadingSidecarError(msg)

    source_id, state, default_speaker, nc_json, session_date = row
    name_corrections: dict[str, str] = {}
    if nc_json:
        try:
            raw = json.loads(nc_json)
            if isinstance(raw, dict):
                name_corrections = {str(k): str(v) for k, v in raw.items()}
        except (json.JSONDecodeError, TypeError):
            pass

    try:
        structure = structure_store.read_structure(conn, ingest_id)
        gap_warnings = gap_check_mod.check(structure)
    except structure_store.StructureStoreError:
        gap_warnings = []

    return IngestState(
        ingest_id=ingest_id,
        source_id=source_id or "",
        state=state or "reading",
        default_speaker=default_speaker or "",
        name_corrections=name_corrections,
        session_date=session_date,
        gap_warnings=gap_warnings,
    )


def write_state(
    conn: sqlite3.Connection,
    ingest_id: str,
    *,
    default_speaker: str,
    name_corrections: dict[str, str],
    session_date: str | None,
    state: str = "reading",
) -> None:
    """UPSERT reading state onto an existing ingests row.

    The ingests row must already exist (created by source_store.record_in_db).
    """
    nc_json = json.dumps(name_corrections)
    conn.execute(
        "UPDATE ingests SET state=?, default_speaker=?, name_corrections_json=?, "
        "session_date=? WHERE ingest_id=?",
        (state, default_speaker, nc_json, session_date, ingest_id),
    )


def update_default_speaker(
    conn: sqlite3.Connection, ingest_id: str, speaker: str
) -> None:
    """Update default_speaker on ingests row."""
    conn.execute(
        "UPDATE ingests SET default_speaker=? WHERE ingest_id=?",
        (speaker, ingest_id),
    )


def update_name_corrections(
    conn: sqlite3.Connection, ingest_id: str, mapping: dict[str, str]
) -> None:
    """Replace name_corrections_json on ingests row."""
    conn.execute(
        "UPDATE ingests SET name_corrections_json=? WHERE ingest_id=?",
        (json.dumps(mapping), ingest_id),
    )


def update_session_date(
    conn: sqlite3.Connection, ingest_id: str, session_date: str | None
) -> None:
    """Update session_date on ingests row."""
    conn.execute(
        "UPDATE ingests SET session_date=? WHERE ingest_id=?",
        (session_date, ingest_id),
    )


def update_state(conn: sqlite3.Connection, ingest_id: str, new_state: str) -> None:
    """Update state field on ingests row."""
    conn.execute(
        "UPDATE ingests SET state=? WHERE ingest_id=?",
        (new_state, ingest_id),
    )


def exists(conn: sqlite3.Connection, ingest_id: str) -> bool:
    """Return True if an ingests row exists for ingest_id."""
    row = conn.execute(
        "SELECT 1 FROM ingests WHERE ingest_id=?", (ingest_id,)
    ).fetchone()
    return row is not None


# Convenience aliases matching old read/write names used in tests.
read = read_state
write = write_state
