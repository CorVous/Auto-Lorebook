"""reading.md assembly from Stage 1a structure and Stage 1b summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from auto_lorebook.reading.frontmatter import join_frontmatter
from auto_lorebook.schema import TOOL_SCHEMA_VERSION
from auto_lorebook.sources.srt import ts_to_seconds

if TYPE_CHECKING:
    from auto_lorebook.pipeline.stage1b import SegmentSummary
    from auto_lorebook.sources.info_yaml import InfoYaml

_EMPTY_SEGMENT = "_No claims extracted from this segment._"


def _flag_in_segment(
    flag: dict[str, object],
    start_s: float,
    end_s: float,
) -> bool:
    try:
        loc_s = ts_to_seconds(str(flag.get("locator", "0:00:00")))
    except ValueError:
        return False
    return start_s <= loc_s <= end_s


def assemble_reading_md(
    info: InfoYaml,
    structure: dict[str, object],
    summaries: list[SegmentSummary],
    *,
    ingested_at: str,
) -> str:
    """Assemble reading.md content from structure and Stage 1b summaries.

    :param info: source info.yaml data
    :param structure: validated structure.yaml dict
    :param summaries: Stage 1b per-segment summaries (may be empty)
    :param ingested_at: RFC 3339 UTC timestamp for the frontmatter
    :return: full reading.md string with YAML frontmatter and markdown body
    """
    fm: dict[str, object] = {
        "schema_version": TOOL_SCHEMA_VERSION,
        "source_id": info["source_id"],
        "source_name": info["title"],
        "source_url": info["source_url"],
        "source_type": info["source_type"],
        "session_date": info["session_date"],
        "ingested_at": ingested_at,
        "reading_status": "draft",
        "default_speaker": structure.get("default_speaker"),
        "name_corrections": {},
    }

    raw_segs = structure.get("segments")
    segments: list[dict[str, object]] = cast(
        "list[dict[str, object]]",
        [s if isinstance(s, dict) else {} for s in raw_segs]  # type: ignore[union-attr]
        if isinstance(raw_segs, list)
        else [],
    )
    raw_flags = structure.get("uncertainty_flags")
    uncertainty_flags: list[dict[str, object]] = cast(
        "list[dict[str, object]]",
        [f if isinstance(f, dict) else {} for f in raw_flags]  # type: ignore[union-attr]
        if isinstance(raw_flags, list)
        else [],
    )

    summaries_by_id: dict[str, SegmentSummary] = {s.segment_id: s for s in summaries}

    body_parts: list[str] = []
    for seg in segments:
        seg_id = str(seg.get("id", ""))
        start = str(seg.get("start", "0:00:00"))
        end = str(seg.get("end", "0:00:00"))
        title = str(seg.get("title", ""))

        body_parts.append(f"\n## [{start}] {title}\n")

        summary = summaries_by_id.get(seg_id)
        if summary and summary.bullets:
            body_parts.extend(
                f"- {bullet.text} [{bullet.anchor}]" for bullet in summary.bullets
            )
        else:
            body_parts.append(_EMPTY_SEGMENT)

        # Uncertainty flags whose locator falls within this segment
        start_s = ts_to_seconds(start)
        end_s = ts_to_seconds(end)
        for flag in uncertainty_flags:
            if _flag_in_segment(flag, start_s, end_s):
                desc = str(flag.get("description", ""))
                loc = str(flag.get("locator", ""))
                body_parts.append(f"\n> **Uncertainty:** {desc} [{loc}]")

    body = "\n".join(body_parts) + "\n"
    return join_frontmatter(fm, body)
