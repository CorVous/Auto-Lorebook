"""DB CRUD over `segments` + `segment_bullets`.

Public read/write API for Stage 1a/1b pipeline output stored in wiki.db.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook.timestamps import format_timestamp, parse_timestamp

if TYPE_CHECKING:
    import sqlite3

    from auto_lorebook.stage1b import Bullet, ReadingBullets
    from auto_lorebook.structure import Structure

# valid segment_status values (mirrors DB CHECK constraint)
VALID_STATUSES = frozenset({"draft", "accepted", "skipped", "regenerating"})


class StructureStoreError(ValueError):
    """Raised when no segments exist for a given ingest_id."""


@dataclass(frozen=True)
class SegmentRow:
    """One row from the segments table."""

    pk: int
    ingest_id: str
    segment_id: str
    start: str  # "h:mm:ss"
    end: str
    title: str
    speaker: str | None
    notes: str | None
    segment_status: str
    overrides: list[dict]  # decoded from overrides_json
    flags: list[dict]  # decoded from flags_json


@dataclass(frozen=True)
class BulletRow:
    """One row from the segment_bullets table."""

    pk: int
    segment_pk: int
    bullet_index: int
    text: str
    anchor: str  # ISO "h:mm:ss"
    locator_hint: str  # "h:mm:ss-h:mm:ss"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode_overrides(overrides: list) -> str:
    """Serialize Override objects (or dicts) to JSON."""
    out = []
    for o in overrides:
        if hasattr(o, "start"):
            d: dict = {
                "start": format_timestamp(o.start),
                "end": format_timestamp(o.end),
                "speaker": o.speaker,
            }
            if o.voiced_by is not None:
                d["voiced_by"] = o.voiced_by
            if o.note is not None:
                d["note"] = o.note
            out.append(d)
        else:
            out.append(dict(o))
    return json.dumps(out)


def _decode_overrides(raw: str) -> list[dict]:
    return json.loads(raw)


def _decode_flags(raw: str) -> list[dict]:
    return json.loads(raw)


def _encode_flags(flags: list) -> str:
    out = []
    for f in flags:
        if hasattr(f, "locator"):
            d: dict = {
                "locator": format_timestamp(f.locator),
                "span": f.span,
                "kind": f.kind,
            }
            if f.note is not None:
                d["note"] = f.note
            out.append(d)
        else:
            out.append(dict(f))
    return json.dumps(out)


def _flags_by_segment(structure: Structure) -> dict[str, list]:
    """Bucket uncertainty_flags onto matching segments via locator."""
    out: dict[str, list] = {}
    for flag in structure.uncertainty_flags:
        for seg in structure.segments:
            if seg.start <= flag.locator <= seg.end:
                out.setdefault(seg.id, []).append(flag)
                break
    return out


def _bullet_locator_hint(b: Bullet) -> str:
    """Encode locator_hint as 'h:mm:ss-h:mm:ss' string."""
    start = format_timestamp(b.locator_hint_start)
    end = format_timestamp(b.locator_hint_end)
    return f"{start}-{end}"


def _decode_locator_hint(hint: str) -> tuple[float, float]:
    """Decode 'h:mm:ss-h:mm:ss'; split on last '-'."""
    idx = hint.rfind("-")
    if idx == -1:
        return parse_timestamp(hint), parse_timestamp(hint)
    return parse_timestamp(hint[:idx]), parse_timestamp(hint[idx + 1 :])


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_structure(
    conn: sqlite3.Connection, ingest_id: str, structure: Structure
) -> None:
    """Replace all segments for ingest_id (deletes existing, then inserts).

    Buckets Structure.uncertainty_flags onto matching segments via locator.
    segment_status set to 'draft' for all new rows.
    """
    delete_ingest_segments(conn, ingest_id)
    flags_by_seg = _flags_by_segment(structure)
    for seg in structure.segments:
        flags = flags_by_seg.get(seg.id, [])
        conn.execute(
            "INSERT INTO segments "
            "(ingest_id, segment_id, start, end, title, speaker, notes, "
            " segment_status, overrides_json, flags_json) "
            "VALUES (?,?,?,?,?,?,?,'draft',?,?)",
            (
                ingest_id,
                seg.id,
                format_timestamp(seg.start),
                format_timestamp(seg.end),
                seg.title,
                seg.speaker,
                seg.notes,
                _encode_overrides(seg.overrides),
                _encode_flags(flags),
            ),
        )


def write_segment_bullets(
    conn: sqlite3.Connection,
    ingest_id: str,
    segment_id: str,
    bullets: list[Bullet],
) -> None:
    """Replace bullets for one segment (delete existing, then insert)."""
    row = conn.execute(
        "SELECT id FROM segments WHERE ingest_id=? AND segment_id=?",
        (ingest_id, segment_id),
    ).fetchone()
    if row is None:
        return
    seg_pk = row[0]
    conn.execute("DELETE FROM segment_bullets WHERE segment_pk=?", (seg_pk,))
    for idx, b in enumerate(bullets):
        conn.execute(
            "INSERT INTO segment_bullets "
            "(segment_pk, bullet_index, text, anchor, locator_hint) "
            "VALUES (?,?,?,?,?)",
            (seg_pk, idx, b.text, format_timestamp(b.anchor), _bullet_locator_hint(b)),
        )


def write_bullets(
    conn: sqlite3.Connection,
    ingest_id: str,
    reading_bullets: ReadingBullets,
) -> None:
    """Write all segments' bullets from a ReadingBullets snapshot."""
    for segment_id, bullet_list in reading_bullets.segments.items():
        write_segment_bullets(conn, ingest_id, segment_id, bullet_list)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_structure(conn: sqlite3.Connection, ingest_id: str) -> Structure:
    """Reassemble Structure from DB rows.

    :raises StructureStoreError: no segments for ingest_id.
    """
    from auto_lorebook.structure import (  # noqa: PLC0415
        Override,
        Segment,
        Structure,
        UncertaintyFlag,
    )

    rows = conn.execute(
        "SELECT id, segment_id, start, end, title, speaker, notes, "
        "       overrides_json, flags_json "
        "FROM segments WHERE ingest_id=? "
        "ORDER BY start",
        (ingest_id,),
    ).fetchall()
    if not rows:
        msg = f"no segments for ingest_id {ingest_id!r}"
        raise StructureStoreError(msg)

    ingest_row = conn.execute(
        "SELECT default_speaker FROM ingests WHERE ingest_id=?",
        (ingest_id,),
    ).fetchone()
    default_speaker = (ingest_row[0] or "") if ingest_row else ""

    segments = []
    all_flags: list[UncertaintyFlag] = []
    for row in rows:
        _, seg_id, start, end, title, speaker, notes, overrides_json, flags_json = row
        overrides_data = _decode_overrides(overrides_json)
        overrides = [
            Override(
                start=parse_timestamp(str(o["start"])),
                end=parse_timestamp(str(o["end"])),
                speaker=str(o["speaker"]),
                voiced_by=o.get("voiced_by"),
                note=o.get("note"),
            )
            for o in overrides_data
        ]
        segments.append(
            Segment(
                id=seg_id,
                start=parse_timestamp(start),
                end=parse_timestamp(end),
                title=title,
                speaker=speaker or "",
                notes=notes,
                overrides=overrides,
            )
        )
        flags_data = _decode_flags(flags_json)
        all_flags.extend(
            UncertaintyFlag(
                locator=parse_timestamp(str(fd["locator"])),
                span=str(fd.get("span") or ""),
                kind=str(fd.get("kind") or "other"),
                note=fd.get("note"),
            )
            for fd in flags_data
        )

    return Structure(
        source_id=ingest_id,
        generated_at="",
        default_speaker=default_speaker,
        segments=segments,
        uncertainty_flags=all_flags,
    )


def read_bullets(conn: sqlite3.Connection, ingest_id: str) -> ReadingBullets:
    """Reassemble ReadingBullets from DB rows."""
    from auto_lorebook.stage1b import Bullet, ReadingBullets  # noqa: PLC0415

    rows = conn.execute(
        "SELECT s.segment_id, sb.text, sb.anchor, sb.locator_hint "
        "FROM segment_bullets sb "
        "JOIN segments s ON sb.segment_pk = s.id "
        "WHERE s.ingest_id=? "
        "ORDER BY s.start, sb.bullet_index",
        (ingest_id,),
    ).fetchall()

    segments: dict[str, list[Bullet]] = {}
    for seg_id, text, anchor, locator_hint in rows:
        hint_start, hint_end = _decode_locator_hint(locator_hint)
        b = Bullet(
            text=text,
            anchor=parse_timestamp(anchor),
            locator_hint_start=hint_start,
            locator_hint_end=hint_end,
        )
        segments.setdefault(seg_id, []).append(b)

    return ReadingBullets(source_id=ingest_id, generated_at="", segments=segments)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def has_structure(conn: sqlite3.Connection, ingest_id: str) -> bool:
    """Return True if any segments row exists for ingest_id (Stage 1a output)."""
    row = conn.execute(
        "SELECT 1 FROM segments WHERE ingest_id=? LIMIT 1", (ingest_id,)
    ).fetchone()
    return row is not None


def list_segments(conn: sqlite3.Connection, ingest_id: str) -> list[SegmentRow]:
    """Return all SegmentRows for ingest_id sorted by start."""
    rows = conn.execute(
        "SELECT id, ingest_id, segment_id, start, end, title, speaker, notes, "
        "       segment_status, overrides_json, flags_json "
        "FROM segments WHERE ingest_id=? ORDER BY start",
        (ingest_id,),
    ).fetchall()
    return [
        SegmentRow(
            pk=r[0],
            ingest_id=r[1],
            segment_id=r[2],
            start=r[3],
            end=r[4],
            title=r[5],
            speaker=r[6],
            notes=r[7],
            segment_status=r[8],
            overrides=_decode_overrides(r[9]),
            flags=_decode_flags(r[10]),
        )
        for r in rows
    ]


def get_segment(
    conn: sqlite3.Connection, ingest_id: str, segment_id: str
) -> SegmentRow | None:
    """Return SegmentRow or None if not found."""
    row = conn.execute(
        "SELECT id, ingest_id, segment_id, start, end, title, speaker, notes, "
        "       segment_status, overrides_json, flags_json "
        "FROM segments WHERE ingest_id=? AND segment_id=?",
        (ingest_id, segment_id),
    ).fetchone()
    if row is None:
        return None
    return SegmentRow(
        pk=row[0],
        ingest_id=row[1],
        segment_id=row[2],
        start=row[3],
        end=row[4],
        title=row[5],
        speaker=row[6],
        notes=row[7],
        segment_status=row[8],
        overrides=_decode_overrides(row[9]),
        flags=_decode_flags(row[10]),
    )


def list_bullets_for_segment(
    conn: sqlite3.Connection, segment_pk: int
) -> list[BulletRow]:
    """Return BulletRows for a segment, ordered by bullet_index."""
    rows = conn.execute(
        "SELECT id, segment_pk, bullet_index, text, anchor, locator_hint "
        "FROM segment_bullets WHERE segment_pk=? ORDER BY bullet_index",
        (segment_pk,),
    ).fetchall()
    return [
        BulletRow(
            pk=r[0],
            segment_pk=r[1],
            bullet_index=r[2],
            text=r[3],
            anchor=r[4],
            locator_hint=r[5],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Mutation helpers
# ---------------------------------------------------------------------------


def set_segment_status(
    conn: sqlite3.Connection, ingest_id: str, segment_id: str, status: str
) -> None:
    """Update segment_status; CHECK constraint enforced by DB.

    :raises sqlite3.IntegrityError: status not in VALID_STATUSES.
    """
    conn.execute(
        "UPDATE segments SET segment_status=? WHERE ingest_id=? AND segment_id=?",
        (status, ingest_id, segment_id),
    )


def update_segment_overrides(
    conn: sqlite3.Connection,
    ingest_id: str,
    segment_id: str,
    overrides: list[dict],
) -> None:
    """Replace overrides_json for one segment."""
    conn.execute(
        "UPDATE segments SET overrides_json=? WHERE ingest_id=? AND segment_id=?",
        (json.dumps(overrides), ingest_id, segment_id),
    )


def delete_ingest_segments(conn: sqlite3.Connection, ingest_id: str) -> None:
    """Delete all segments for ingest_id (cascades to segment_bullets)."""
    conn.execute("DELETE FROM segments WHERE ingest_id=?", (ingest_id,))
