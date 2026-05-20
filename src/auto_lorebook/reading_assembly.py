"""Assemble wiki-side reading.md from DB segments + sidecar + info.

Pure-function assemble() now reads segments + bullets from DB via structure_store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook.reading import apply_name_corrections, linkify_timestamp
from auto_lorebook.timestamps import format_timestamp, parse_timestamp

if TYPE_CHECKING:
    import sqlite3

    from auto_lorebook.info_yaml import Info
    from auto_lorebook.reading_sidecar import IngestState

_EMPTY_MARKER = "_No claims extracted from this segment._"


def assemble(
    *,
    conn: sqlite3.Connection,
    ingest_id: str,
    info: Info,
    sidecar: IngestState,
) -> str:
    """Render wiki-side reading.md from DB.

    Derived approval — file presence is gate.
    """
    from auto_lorebook import structure_store  # noqa: PLC0415

    corrections = dict(sidecar.name_corrections)
    seg_rows = structure_store.list_segments(conn, ingest_id)
    bullets_map = structure_store.read_bullets(conn, ingest_id)

    parts: list[str] = [_render_frontmatter(info, sidecar)]
    parts.append(f"# Reading: {info.title or info.source_id}")

    for seg in seg_rows:
        start_f = parse_timestamp(seg.start)
        end_f = parse_timestamp(seg.end)
        parts.append(
            _render_segment_header(
                start_f,
                end_f,
                seg.title,
                seg.speaker or "",
                info.source_url,
                corrections,
            )
        )
        seg_bullets = bullets_map.segments.get(seg.segment_id, [])
        flags = _decode_flags(seg.flags)
        body = build_segment_body(
            seg_bullets=seg_bullets,
            flags=flags,
            source_url=info.source_url,
            name_corrections=corrections,
            seg_start=start_f,
        )
        if body:
            parts.append(body.rstrip("\n"))
        else:
            parts.append(_EMPTY_MARKER)

    return "\n\n".join(parts) + "\n"


def _decode_flags(flags: list[dict]) -> list[dict]:
    return list(flags)


def build_segment_body(
    *,
    seg_bullets: list,
    flags: list[dict],
    source_url: str | None,
    name_corrections: dict[str, str],
    seg_start: float = 0.0,  # noqa: ARG001
) -> str:
    """Render segment body: uncertainty flags + bullets (or empty marker)."""
    parts: list[str] = []
    for flag in flags:
        locator = parse_timestamp(str(flag.get("locator") or "0:00:00"))
        ts = format_timestamp(locator)
        kind = flag.get("kind", "other")
        span = flag.get("span", "")
        note = flag.get("note")
        note_str = f"; {note}" if note else ""
        parts.append(f"- [{ts}] uncertain {kind}: {span}{note_str}")
    if not seg_bullets:
        parts.append(_EMPTY_MARKER)
    else:
        for b in seg_bullets:
            text = apply_name_corrections(b.text, name_corrections)
            anchor_ts = format_timestamp(b.anchor)
            link = linkify_timestamp(source_url, b.anchor)
            if link:
                parts.append(f"- {text} [[{anchor_ts}]]({link})")
            else:
                parts.append(f"- {text} [{anchor_ts}]")
    return "\n\n".join(parts) + "\n"


def _render_frontmatter(info: Info, sidecar: IngestState) -> str:
    fm: dict[str, Any] = {
        "schema_version": 1,
        "source_id": info.source_id,
        "source_name": info.title,
        "source_url": info.source_url,
        "source_type": info.source_type,
        "session_date": (
            sidecar.session_date
            if sidecar.session_date is not None
            else info.session_date
        ),
        "ingested_at": info.fetched_at,
        "default_speaker": sidecar.default_speaker,
        "name_corrections": dict(sidecar.name_corrections),
    }
    body = yaml.safe_dump(
        fm,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip("\n")
    return f"---\n{body}\n---"


def _render_segment_header(
    start: float,
    end: float,
    title: str,
    speaker: str,
    source_url: str | None,
    name_corrections: dict[str, str],
) -> str:
    start_ts = format_timestamp(start)
    end_ts = format_timestamp(end)
    header_text = apply_name_corrections(title, name_corrections)
    link = linkify_timestamp(source_url, start)
    if link:
        header = f"## [[{start_ts}-{end_ts}]]({link}) {header_text}"
    else:
        header = f"## [{start_ts}-{end_ts}] {header_text}"
    speaker_line = f"Speaker: {speaker}"
    return f"{header}\n\n{speaker_line}"
