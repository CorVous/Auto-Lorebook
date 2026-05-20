"""Shared builder helpers for reading tests.

Underscore prefix prevents pytest collection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from auto_lorebook.info_yaml import Info, SourceContext
from auto_lorebook.reading_sidecar import Sidecar
from auto_lorebook.stage1b import Bullet, ReadingBullets
from auto_lorebook.structure import Segment, Structure, UncertaintyFlag

if TYPE_CHECKING:
    import sqlite3


_SOURCE_ID = "yt-abc12345678"


def _info(
    *,
    source_url: str | None = "https://youtube.com/watch?v=abc12345678",
    title: str | None = "Session 3",
) -> Info:
    return Info(
        source_id=_SOURCE_ID,
        source_type="youtube",
        fetched_at="2026-04-20T14:35:12Z",
        source_url=source_url,
        title=title,
        duration_seconds=600,
        context=SourceContext(),
    )


def _structure() -> Structure:
    return Structure(
        source_id=_SOURCE_ID,
        generated_at="2026-04-20T14:32:00Z",
        default_speaker="DM",
        segments=[
            Segment(
                id="seg-001", start=0.0, end=120.0, title="Introduction", speaker="DM"
            ),
            Segment(
                id="seg-002",
                start=120.0,
                end=270.0,
                title="Rules discussion: grappling",
                speaker="mixed",
                notes="off-topic",
            ),
            Segment(
                id="seg-003",
                start=270.0,
                end=600.0,
                title="Founding of Aldara",
                speaker="DM",
            ),
        ],
        uncertainty_flags=[
            UncertaintyFlag(
                locator=347.0, span="a place name", kind="name", note="unclear"
            )
        ],
    )


def _bullets() -> ReadingBullets:
    return ReadingBullets(
        source_id=_SOURCE_ID,
        generated_at="2026-04-20T14:34:00Z",
        segments={
            "seg-001": [],
            "seg-002": [],
            "seg-003": [
                Bullet(
                    text="King Theron founded Aldara in the Second Age",
                    anchor=272.0,
                    locator_hint_start=257.0,
                    locator_hint_end=287.0,
                ),
                Bullet(
                    text="The founding displaced an earlier elven presence",
                    anchor=314.0,
                    locator_hint_start=299.0,
                    locator_hint_end=329.0,
                ),
            ],
        },
    )


def _sidecar(
    name_corrections: dict[str, str] | None = None,
    ingest_id: str = _SOURCE_ID,
) -> Sidecar:
    return Sidecar(
        ingest_id=ingest_id,
        source_id=ingest_id,
        state="reading",
        default_speaker="DM",
        name_corrections=name_corrections or {},
        session_date=None,
    )


def _seed_ingest_in_db(
    conn: sqlite3.Connection,
    sid: str = _SOURCE_ID,
    *,
    default_speaker: str = "DM",
    name_corrections: dict[str, str] | None = None,
    session_date: str | None = None,
) -> None:
    """Seed sources + ingests rows and optionally structure + bullets in DB."""
    from auto_lorebook import structure_store  # noqa: PLC0415

    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, source_type, source_url, title, duration_seconds, "
        " caption_type, fetched_at, session_date, context_json) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            sid,
            "youtube",
            "https://youtube.com/watch?v=abc12345678",
            "Session 3",
            600,
            "manual",
            "2026-04-20T14:35:12Z",
            None,
            "{}",
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO ingests "
        "(ingest_id, source_id, started_at, state, default_speaker, "
        " name_corrections_json, session_date) "
        "VALUES (?,?,?,'reading',?,?,?)",
        (
            sid,
            sid,
            "2026-04-20T14:35:12Z",
            default_speaker,
            __import__("json").dumps(name_corrections or {}),
            session_date,
        ),
    )
    # seed structure + bullets
    struct = _structure()
    # patch source_id for the given sid
    struct = Structure(
        source_id=sid,
        generated_at=struct.generated_at,
        default_speaker=struct.default_speaker,
        segments=struct.segments,
        uncertainty_flags=struct.uncertainty_flags,
    )
    structure_store.write_structure(conn, sid, struct)

    bulls = _bullets()
    # patch source_id
    bulls = ReadingBullets(
        source_id=sid,
        generated_at=bulls.generated_at,
        segments=bulls.segments,
    )
    structure_store.write_bullets(conn, sid, bulls)
    conn.commit()
