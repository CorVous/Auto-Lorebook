"""pending/<id>/reading/segments/seg-NNN.md: per-segment frontmatter + body.

Frontmatter carries segment metadata; body carries rendered bullets and
uncertainty flags.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text
from auto_lorebook.schema import SchemaVersionError, read_schema_version
from auto_lorebook.structure import Override
from auto_lorebook.timestamps import format_timestamp, parse_timestamp

if TYPE_CHECKING:
    from pathlib import Path

_MAX_SCHEMA = 1
_FRONTMATTER_RE = re.compile(r"^---\n(?P<fm>.*?)\n---\n(?P<rest>.*)$", re.DOTALL)

VALID_STATUSES = frozenset({"draft", "accepted", "skipped", "regenerating"})


class SegmentFileError(ValueError):
    """Raised for missing files, missing frontmatter, or invalid status."""


@dataclass(frozen=True)
class SegmentFrontmatter:
    """Parsed frontmatter for a single segment file."""

    segment_id: str
    segment_status: str
    start: float
    end: float
    title: str
    speaker: str
    notes: str | None = None
    overrides: list[Override] = field(default_factory=list)


@dataclass(frozen=True)
class SegmentFile:
    """In-memory representation of a seg-NNN.md file."""

    frontmatter: SegmentFrontmatter
    body: str


def _override_to_dict(o: Override) -> dict[str, Any]:
    out: dict[str, Any] = {
        "start": format_timestamp(o.start),
        "end": format_timestamp(o.end),
        "speaker": o.speaker,
    }
    if o.voiced_by is not None:
        out["voiced_by"] = o.voiced_by
    if o.note is not None:
        out["note"] = o.note
    return out


def _fm_to_dict(fm: SegmentFrontmatter) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": 1,
        "segment_id": fm.segment_id,
        "segment_status": fm.segment_status,
        "start": format_timestamp(fm.start),
        "end": format_timestamp(fm.end),
        "title": fm.title,
        "speaker": fm.speaker,
        "notes": fm.notes,
        "overrides": [_override_to_dict(o) for o in fm.overrides],
    }
    return out


def write(sf: SegmentFile, path: Path) -> None:
    """Atomically write a seg-NNN.md file."""
    fm_text = yaml.safe_dump(
        _fm_to_dict(sf.frontmatter),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip("\n")
    text = f"---\n{fm_text}\n---\n{sf.body}"
    atomic_write_text(path, text)


def read(path: Path) -> SegmentFile:
    """Read a seg-NNN.md file.

    :raises SegmentFileError: missing file, missing frontmatter, bad status,
        or invalid schema_version.
    """
    if not path.exists():
        msg = f"{path}: not found"
        raise SegmentFileError(msg)
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        msg = f"{path}: no '---' frontmatter block"
        raise SegmentFileError(msg)
    try:
        data = yaml.safe_load(m.group("fm"))
    except yaml.YAMLError as e:
        msg = f"{path}: frontmatter YAML parse error: {e}"
        raise SegmentFileError(msg) from e
    if not isinstance(data, dict):
        msg = f"{path}: frontmatter is not a mapping"
        raise SegmentFileError(msg)
    try:
        read_schema_version(data, str(path), max_supported=_MAX_SCHEMA)
    except SchemaVersionError as e:
        raise SegmentFileError(str(e)) from e

    status = data.get("segment_status", "draft")
    if status not in VALID_STATUSES:
        msg = (
            f"{path}: invalid segment_status {status!r}; "
            f"expected one of {sorted(VALID_STATUSES)}"
        )
        raise SegmentFileError(msg)

    overrides = [
        Override(
            start=parse_timestamp(raw_o["start"]),
            end=parse_timestamp(raw_o["end"]),
            speaker=raw_o["speaker"],
            voiced_by=raw_o.get("voiced_by"),
            note=raw_o.get("note"),
        )
        for raw_o in data.get("overrides") or []
    ]

    fm = SegmentFrontmatter(
        segment_id=data["segment_id"],
        segment_status=status,
        start=parse_timestamp(data["start"]),
        end=parse_timestamp(data["end"]),
        title=data["title"],
        speaker=data["speaker"],
        notes=data.get("notes"),
        overrides=overrides,
    )
    return SegmentFile(frontmatter=fm, body=m.group("rest"))


def with_status(path: Path, status: str) -> str:
    """Return segment file text with segment_status set to status.

    :raises SegmentFileError: invalid status or missing/malformed frontmatter.
    """
    if status not in VALID_STATUSES:
        msg = (
            f"invalid segment_status {status!r}; "
            f"expected one of {sorted(VALID_STATUSES)}"
        )
        raise SegmentFileError(msg)
    sf = read(path)
    new_fm = SegmentFrontmatter(
        segment_id=sf.frontmatter.segment_id,
        segment_status=status,
        start=sf.frontmatter.start,
        end=sf.frontmatter.end,
        title=sf.frontmatter.title,
        speaker=sf.frontmatter.speaker,
        notes=sf.frontmatter.notes,
        overrides=sf.frontmatter.overrides,
    )
    fm_text = yaml.safe_dump(
        _fm_to_dict(new_fm),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip("\n")
    return f"---\n{fm_text}\n---\n{sf.body}"


def set_status(path: Path, status: str) -> None:
    """Rewrite segment_status in place."""
    atomic_write_text(path, with_status(path, status))
