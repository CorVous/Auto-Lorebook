"""Assemble wiki-side reading.md from segments + sidecar + info.

Pure function — no filesystem I/O. Imports rendering helpers from
reading.py to avoid duplication.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook.reading import apply_name_corrections, linkify_timestamp
from auto_lorebook.timestamps import format_timestamp

if TYPE_CHECKING:
    from auto_lorebook.info_yaml import Info
    from auto_lorebook.reading_sidecar import Sidecar
    from auto_lorebook.segment_file import SegmentFile


def assemble(
    *,
    segments: list[SegmentFile],
    sidecar: Sidecar,
    info: Info,
) -> str:
    """Render wiki-side reading.md; always emits reading_status: approved."""
    corrections = dict(sidecar.name_corrections)
    parts: list[str] = [_render_frontmatter(info, sidecar)]
    parts.append(f"# Reading: {info.title or info.source_id}")

    for sf in segments:
        fm = sf.frontmatter
        parts.append(
            _render_segment_header(
                fm.start, fm.end, fm.title, fm.speaker, info.source_url, corrections
            )
        )
        # body is pre-rendered; strip trailing newline for join, re-add via body itself
        body = sf.body.rstrip("\n")
        if body:
            parts.append(body)
        else:
            parts.append("_No claims extracted from this segment._")

    return "\n\n".join(parts) + "\n"


def _render_frontmatter(info: Info, sidecar: Sidecar) -> str:
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
        "reading_status": "approved",
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
