"""DB-backed transcription corrections; YAML is lazy-backfill source."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from auto_lorebook.schema import read_tolerant_yaml
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

_logger = logging.getLogger("auto_lorebook.corrections")
_MAX_SCHEMA = 1
_FILE_LABEL = ".transcription-corrections.yaml"


@dataclass
class Correction:
    """Single phonetic correction entry."""

    wrong: str
    right: str
    first_seen_in: str | None = None
    also_seen_in: list[str] = field(default_factory=list)


@dataclass
class Corrections:
    """All transcription corrections."""

    corrections: list[Correction] = field(default_factory=list)


# ---------------------------------------------------------------------------
# DB API
# ---------------------------------------------------------------------------


def read(
    conn: sqlite3.Connection,
    *,
    wiki_repo: Path | None = None,
) -> Corrections:
    """Return all corrections; lazy-backfills from YAML when DB is empty."""
    rows = conn.execute(
        "SELECT id, from_text, to_text, first_seen_in FROM transcription_corrections"
        " ORDER BY from_text, to_text"
    ).fetchall()
    if not rows and wiki_repo is not None:
        _backfill_from_yaml(conn, wiki_repo)
        rows = conn.execute(
            "SELECT id, from_text, to_text, first_seen_in FROM"
            " transcription_corrections ORDER BY from_text, to_text"
        ).fetchall()

    result: list[Correction] = []
    for row in rows:
        also = conn.execute(
            "SELECT source_id FROM correction_also_seen_in WHERE correction_id = ?",
            (row["id"],),
        ).fetchall()
        also_ids = [r["source_id"] for r in also]
        result.append(
            Correction(
                wrong=row["from_text"],
                right=row["to_text"],
                first_seen_in=row["first_seen_in"],
                also_seen_in=also_ids,
            )
        )
    return Corrections(corrections=result)


def add(
    conn: sqlite3.Connection,
    *,
    wrong: str,
    right: str,
    first_seen_in: str,
    notes: str | None = None,
) -> Correction:
    """Insert correction; return existing if UNIQUE constraint fires."""
    now = format_iso_now()
    conn.execute(
        """
        INSERT OR IGNORE INTO transcription_corrections(
            from_text, to_text, first_seen_in, promoted_at, notes
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (wrong, right, first_seen_in, now, notes),
    )
    row = conn.execute(
        "SELECT id, from_text, to_text, first_seen_in"
        " FROM transcription_corrections WHERE from_text=? AND to_text=?",
        (wrong, right),
    ).fetchone()
    also = conn.execute(
        "SELECT source_id FROM correction_also_seen_in WHERE correction_id = ?",
        (row["id"],),
    ).fetchall()
    return Correction(
        wrong=row["from_text"],
        right=row["to_text"],
        first_seen_in=row["first_seen_in"],
        also_seen_in=[r["source_id"] for r in also],
    )


def add_also_seen_in(
    conn: sqlite3.Connection,
    *,
    wrong: str,
    right: str,
    source_id: str,
) -> None:
    """Insert also_seen_in row; idempotent."""
    row = conn.execute(
        "SELECT id FROM transcription_corrections WHERE from_text=? AND to_text=?",
        (wrong, right),
    ).fetchone()
    if row is None:
        return
    conn.execute(
        "INSERT OR IGNORE INTO correction_also_seen_in(correction_id, source_id)"
        " VALUES (?, ?)",
        (row["id"], source_id),
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _source_exists(conn: sqlite3.Connection, source_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        is not None
    )


def _backfill_from_yaml(conn: sqlite3.Connection, wiki_repo: Path) -> None:
    """Read YAML corrections and insert into DB; idempotent."""
    path = wiki_repo / _FILE_LABEL
    raw = read_tolerant_yaml(path, _FILE_LABEL, max_supported=_MAX_SCHEMA)
    if raw is None:
        return

    items = raw.get("corrections") or []
    for item in items:
        if not isinstance(item, dict):
            continue
        wrong = item.get("wrong") or ""
        right = item.get("right") or ""
        if not wrong or not right:
            continue
        first_seen_in = item.get("first_seen_in") or None
        if first_seen_in is None or not _source_exists(conn, first_seen_in):
            _logger.warning(
                "corrections backfill: skipping %r→%r because first_seen_in=%r"
                " not in sources",
                wrong,
                right,
                first_seen_in,
            )
            continue
        cor = add(conn, wrong=wrong, right=right, first_seen_in=first_seen_in)
        for also_id in item.get("also_seen_in") or []:
            if _source_exists(conn, also_id):
                add_also_seen_in(
                    conn, wrong=cor.wrong, right=cor.right, source_id=also_id
                )
