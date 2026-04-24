"""Stage 1a `structure.yaml`: schema, read/write, mechanical validation.

Internal representation uses float seconds. Writers emit canonical
`h:mm:ss` strings; readers accept any form `parse_timestamp` handles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text
from auto_lorebook.schema import SchemaVersionError, read_schema_version
from auto_lorebook.timestamps import format_timestamp, parse_timestamp

if TYPE_CHECKING:
    from pathlib import Path

_MAX_SCHEMA = 1
UNCERTAINTY_KINDS = frozenset({"name", "attribution", "other"})


class StructureError(ValueError):
    """Raised when structure.yaml is missing or malformed on read."""


class StructureValidationError(ValueError):
    """Raised when mechanical checks on a Structure fail."""


@dataclass
class Override:
    """Mid-segment speaker override."""

    start: float
    end: float
    speaker: str
    voiced_by: str | None = None
    note: str | None = None


@dataclass
class Segment:
    """One topic-bounded segment with attributed speaker."""

    id: str
    start: float
    end: float
    title: str
    speaker: str
    notes: str | None = None
    overrides: list[Override] = field(default_factory=list)


@dataclass
class UncertaintyFlag:
    """Model-flagged uncertain word, name, or attribution."""

    locator: float
    span: str
    kind: str  # name | attribution | other
    note: str | None = None


@dataclass
class Structure:
    """In-memory representation of structure.yaml."""

    source_id: str
    generated_at: str
    default_speaker: str
    segments: list[Segment] = field(default_factory=list)
    uncertainty_flags: list[UncertaintyFlag] = field(default_factory=list)


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


def _segment_to_dict(s: Segment) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": s.id,
        "start": format_timestamp(s.start),
        "end": format_timestamp(s.end),
        "title": s.title,
        "speaker": s.speaker,
    }
    if s.notes is not None:
        out["notes"] = s.notes
    if s.overrides:
        out["overrides"] = [_override_to_dict(o) for o in s.overrides]
    return out


def _flag_to_dict(f: UncertaintyFlag) -> dict[str, Any]:
    out: dict[str, Any] = {
        "locator": format_timestamp(f.locator),
        "span": f.span,
        "kind": f.kind,
    }
    if f.note is not None:
        out["note"] = f.note
    return out


def _to_dict(s: Structure) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source_id": s.source_id,
        "generated_at": s.generated_at,
        "default_speaker": s.default_speaker,
        "segments": [_segment_to_dict(seg) for seg in s.segments],
        "uncertainty_flags": [_flag_to_dict(f) for f in s.uncertainty_flags],
    }


def _parse_override(raw: dict[str, Any]) -> Override:
    return Override(
        start=parse_timestamp(str(raw["start"])),
        end=parse_timestamp(str(raw["end"])),
        speaker=str(raw["speaker"]),
        voiced_by=(raw.get("voiced_by") or None),
        note=(raw.get("note") or None),
    )


def _parse_segment(raw: dict[str, Any]) -> Segment:
    overrides_raw = raw.get("overrides") or []
    return Segment(
        id=str(raw["id"]),
        start=parse_timestamp(str(raw["start"])),
        end=parse_timestamp(str(raw["end"])),
        title=str(raw["title"]),
        speaker=str(raw["speaker"]),
        notes=(raw.get("notes") or None),
        overrides=[_parse_override(o) for o in overrides_raw],
    )


def _parse_flag(raw: dict[str, Any]) -> UncertaintyFlag:
    return UncertaintyFlag(
        locator=parse_timestamp(str(raw["locator"])),
        span=str(raw.get("span") or ""),
        kind=str(raw.get("kind") or "other"),
        note=(raw.get("note") or None),
    )


def read(path: Path) -> Structure:
    """Read and parse structure.yaml.

    :raises StructureError: missing / malformed / unsupported schema
    """
    if not path.exists():
        msg = f"{path}: file not found"
        raise StructureError(msg)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a YAML mapping"
        raise StructureError(msg)
    try:
        read_schema_version(raw, str(path), max_supported=_MAX_SCHEMA)
    except SchemaVersionError as e:
        raise StructureError(str(e)) from e
    try:
        return Structure(
            source_id=str(raw["source_id"]),
            generated_at=str(raw["generated_at"]),
            default_speaker=str(raw.get("default_speaker") or ""),
            segments=[_parse_segment(s) for s in (raw.get("segments") or [])],
            uncertainty_flags=[
                _parse_flag(f) for f in (raw.get("uncertainty_flags") or [])
            ],
        )
    except (KeyError, ValueError) as e:
        msg = f"{path}: malformed structure ({e})"
        raise StructureError(msg) from e


def write(structure: Structure, path: Path) -> None:
    """Atomically write structure.yaml."""
    text = yaml.safe_dump(
        _to_dict(structure),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    atomic_write_text(path, text)


def validate(
    structure: Structure,
    total_duration: float,
    *,
    tolerance: float = 1.0,
) -> None:
    """Run mechanical checks on a Structure.

    :param total_duration: transcript length in seconds
    :param tolerance: allowed slack (seconds) at segment boundaries / coverage
    :raises StructureValidationError: on first failing check
    """
    if not structure.segments:
        msg = "structure has no segments"
        raise StructureValidationError(msg)

    ids = [s.id for s in structure.segments]
    if len(set(ids)) != len(ids):
        msg = f"duplicate segment ids: {[i for i in ids if ids.count(i) > 1]}"
        raise StructureValidationError(msg)

    first = structure.segments[0]
    last = structure.segments[-1]
    if first.start > tolerance:
        msg = f"first segment must start at 0 (got {first.start}s)"
        raise StructureValidationError(msg)
    if last.end < total_duration - tolerance:
        msg = (
            f"last segment ends at {last.end}s but transcript "
            f"duration is {total_duration}s"
        )
        raise StructureValidationError(msg)

    for seg in structure.segments:
        if seg.end < seg.start:
            msg = f"segment {seg.id}: end {seg.end} before start {seg.start}"
            raise StructureValidationError(msg)

    for prev, nxt in zip(structure.segments, structure.segments[1:], strict=False):
        delta = nxt.start - prev.end
        if delta > tolerance:
            msg = (
                f"gap between {prev.id} (ends {prev.end}s) and "
                f"{nxt.id} (starts {nxt.start}s): {delta:.1f}s"
            )
            raise StructureValidationError(msg)
        if delta < -tolerance:
            msg = (
                f"overlap between {prev.id} (ends {prev.end}s) and "
                f"{nxt.id} (starts {nxt.start}s): {-delta:.1f}s"
            )
            raise StructureValidationError(msg)

    for seg in structure.segments:
        for ov in seg.overrides:
            if ov.start < seg.start - tolerance or ov.end > seg.end + tolerance:
                msg = (
                    f"override in {seg.id} ({ov.start}-{ov.end}) falls outside "
                    f"parent segment ({seg.start}-{seg.end})"
                )
                raise StructureValidationError(msg)
            if ov.end < ov.start:
                msg = f"override in {seg.id}: end {ov.end} before start {ov.start}"
                raise StructureValidationError(msg)

    for flag in structure.uncertainty_flags:
        if flag.kind not in UNCERTAINTY_KINDS:
            msg = f"uncertainty flag kind must be one of {sorted(UNCERTAINTY_KINDS)}"
            raise StructureValidationError(msg)
        if not any(
            seg.start - tolerance <= flag.locator <= seg.end + tolerance
            for seg in structure.segments
        ):
            msg = f"uncertainty flag locator {flag.locator}s outside all segments"
            raise StructureValidationError(msg)
