"""Reading.md assembly, writer, and frontmatter helpers.

Interleaves Stage 1a segment headers with Stage 1b bullets, renders
inline uncertainty flags, applies `name_corrections` during rendering,
and produces clickable `h:mm:ss` links (for YouTube sources) via
URL post-processing.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text
from auto_lorebook.timestamps import format_timestamp

if TYPE_CHECKING:
    from pathlib import Path

    from auto_lorebook.info_yaml import Info
    from auto_lorebook.stage1b import Bullet, ReadingBullets
    from auto_lorebook.structure import Segment, Structure, UncertaintyFlag

_logger = logging.getLogger(__name__)

VALID_STATUSES = frozenset({"draft", "approved"})

_FRONTMATTER_RE = re.compile(r"^---\n(?P<body>.*?)\n---\n(?P<rest>.*)$", re.DOTALL)


class ReadingError(ValueError):
    """Raised for missing files, missing frontmatter, or invalid status."""


def linkify_timestamp(source_url: str | None, seconds: float) -> str | None:
    """Return a URL with a `t=` query for the given timestamp, or None."""
    if not source_url:
        return None
    sep = "&" if "?" in source_url else "?"
    return f"{source_url}{sep}t={int(seconds)}"


def apply_name_corrections(text: str, name_corrections: dict[str, str]) -> str:
    """Literally substitute each wrong→right mapping in text."""
    out = text
    for wrong, right in name_corrections.items():
        out = out.replace(wrong, right)
    return out


def assemble(
    *,
    info: Info,
    structure: Structure,
    bullets: ReadingBullets,
    name_corrections: dict[str, str] | None = None,
) -> str:
    """Render reading.md as a string.

    :param name_corrections: mapping applied to rendered text and
        written into the frontmatter map. Callers pass pre-existing
        corrections from the previous reading.md when regenerating.
    """
    corrections = dict(name_corrections or {})
    parts: list[str] = [_render_frontmatter(info, structure, corrections)]
    parts.append(f"# Reading: {info.title or info.source_id}")

    by_segment = bullets.segments
    flags_by_segment = _flags_by_segment(structure)

    for seg in structure.segments:
        parts.append(_render_segment(seg, info.source_url, corrections))
        parts.extend(
            _render_uncertainty_flag(flag) for flag in flags_by_segment.get(seg.id, [])
        )
        seg_bullets = by_segment.get(seg.id, [])
        if not seg_bullets:
            parts.append("_No claims extracted from this segment._")
        else:
            parts.extend(
                _render_bullet(b, info.source_url, corrections) for b in seg_bullets
            )
    return "\n\n".join(parts) + "\n"


def write(path: Path, text: str) -> None:
    """Atomically write reading.md."""
    atomic_write_text(path, text)


def read_frontmatter(path: Path) -> dict[str, Any]:
    """Parse the YAML frontmatter block of a reading.md file.

    :raises ReadingError: missing file, missing frontmatter fence, or
        invalid YAML.
    """
    if not path.exists():
        msg = f"{path}: file not found"
        raise ReadingError(msg)
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        msg = f"{path}: no '---' frontmatter block at top of file"
        raise ReadingError(msg)
    try:
        parsed = yaml.safe_load(m.group("body"))
    except yaml.YAMLError as e:
        msg = f"{path}: frontmatter YAML parse error: {e}"
        raise ReadingError(msg) from e
    if not isinstance(parsed, dict):
        msg = f"{path}: frontmatter is not a mapping"
        raise ReadingError(msg)
    return parsed


def with_status(path: Path, status: str) -> str:
    """Return the reading.md text with `reading_status` set to `status`.

    :raises FileNotFoundError: path doesn't exist
    :raises ReadingError: invalid status, missing/malformed frontmatter
    """
    if status not in VALID_STATUSES:
        msg = (
            f"invalid reading_status {status!r}; "
            f"expected one of {sorted(VALID_STATUSES)}"
        )
        raise ReadingError(msg)
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        msg = f"{path}: no frontmatter block"
        raise ReadingError(msg)
    parsed = yaml.safe_load(m.group("body")) or {}
    if not isinstance(parsed, dict):
        msg = f"{path}: frontmatter is not a mapping"
        raise ReadingError(msg)
    parsed["reading_status"] = status
    new_fm = yaml.safe_dump(
        parsed, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).rstrip("\n")
    return f"---\n{new_fm}\n---\n{m.group('rest')}"


def set_status(path: Path, status: str) -> None:
    """Rewrite the `reading_status` field in place."""
    atomic_write_text(path, with_status(path, status))


def _render_frontmatter(
    info: Info,
    structure: Structure,
    name_corrections: dict[str, str],
) -> str:
    fm: dict[str, Any] = {
        "schema_version": 1,
        "source_id": info.source_id,
        "source_name": info.title,
        "source_url": info.source_url,
        "source_type": info.source_type,
        "session_date": info.session_date,
        "ingested_at": info.fetched_at,
        "reading_status": "draft",
        "default_speaker": structure.default_speaker,
        "name_corrections": dict(name_corrections),
    }
    body = yaml.safe_dump(
        fm,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip("\n")
    return f"---\n{body}\n---"


def _render_segment(
    seg: Segment,
    source_url: str | None,
    name_corrections: dict[str, str],
) -> str:
    start_ts = format_timestamp(seg.start)
    end_ts = format_timestamp(seg.end)
    header_text = apply_name_corrections(seg.title, name_corrections)
    link = linkify_timestamp(source_url, seg.start)
    if link:
        header = f"## [[{start_ts}-{end_ts}]]({link}) {header_text}"
    else:
        header = f"## [{start_ts}-{end_ts}] {header_text}"
    speaker_line = f"Speaker: {seg.speaker}"
    return f"{header}\n\n{speaker_line}"


def _render_bullet(
    b: Bullet,
    source_url: str | None,
    name_corrections: dict[str, str],
) -> str:
    text = apply_name_corrections(b.text, name_corrections)
    anchor_ts = format_timestamp(b.anchor)
    link = linkify_timestamp(source_url, b.anchor)
    if link:
        return f"- {text} [[{anchor_ts}]]({link})"
    return f"- {text} [{anchor_ts}]"


def _render_uncertainty_flag(flag: UncertaintyFlag) -> str:
    ts = format_timestamp(flag.locator)
    note = f"; {flag.note}" if flag.note else ""
    return f"- [{ts}] uncertain {flag.kind}: {flag.span}{note}"


def _flags_by_segment(structure: Structure) -> dict[str, list[UncertaintyFlag]]:
    out: dict[str, list[UncertaintyFlag]] = {}
    for flag in structure.uncertainty_flags:
        for seg in structure.segments:
            if seg.start <= flag.locator <= seg.end:
                out.setdefault(seg.id, []).append(flag)
                break
    return out
