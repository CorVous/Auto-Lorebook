"""pending/<id>/reading/reading.yaml: sidecar for the reading session.

Stores session metadata (default_speaker, session_date, name_corrections)
separately from per-segment content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text
from auto_lorebook.gap_check import GapWarning
from auto_lorebook.schema import SchemaVersionError, read_schema_version

if TYPE_CHECKING:
    from pathlib import Path

# v2: persist gap_warnings produced at generate time
_MAX_SCHEMA = 2


class ReadingSidecarError(ValueError):
    """Raised when reading.yaml is missing or malformed."""


@dataclass
class Sidecar:
    """In-memory representation of reading.yaml."""

    default_speaker: str
    name_corrections: dict[str, str] = field(default_factory=dict)
    session_date: str | None = None
    gap_warnings: list[GapWarning] = field(default_factory=list)


def _warning_to_dict(w: GapWarning) -> dict[str, Any]:
    return {
        "start": w.start,
        "end": w.end,
        "segment_ids": list(w.segment_ids),
        "segment_titles": list(w.segment_titles),
    }


def _warning_from_dict(d: dict[str, Any], path: str) -> GapWarning:
    """Parse a gap_warnings list entry; raise ReadingSidecarError on bad shape."""
    try:
        start = float(d["start"])
        end = float(d["end"])
        segment_ids = tuple(str(s) for s in d["segment_ids"])
        segment_titles = tuple(str(s) for s in d["segment_titles"])
    except (KeyError, TypeError, ValueError) as e:
        msg = f"{path}: malformed gap_warnings entry: {e}"
        raise ReadingSidecarError(msg) from e
    return GapWarning(
        start=start,
        end=end,
        segment_ids=segment_ids,
        segment_titles=segment_titles,
    )


def _to_dict(sc: Sidecar) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "default_speaker": sc.default_speaker,
        "session_date": sc.session_date,
        "name_corrections": dict(sc.name_corrections),
        "gap_warnings": [_warning_to_dict(w) for w in sc.gap_warnings],
    }


def write(sc: Sidecar, path: Path) -> None:
    """Atomically write reading.yaml."""
    body = yaml.safe_dump(
        _to_dict(sc),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    atomic_write_text(path, body)


def read(path: Path) -> Sidecar:
    """Read reading.yaml into a Sidecar.

    :raises ReadingSidecarError: missing file, missing/future schema_version,
        or malformed YAML.
    """
    if not path.exists():
        msg = f"{path}: not found"
        raise ReadingSidecarError(msg)
    text = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        msg = f"{path}: YAML parse error: {e}"
        raise ReadingSidecarError(msg) from e
    if not isinstance(data, dict):
        msg = f"{path}: expected YAML mapping"
        raise ReadingSidecarError(msg)
    try:
        read_schema_version(data, str(path), max_supported=_MAX_SCHEMA)
    except SchemaVersionError as e:
        raise ReadingSidecarError(str(e)) from e

    default_speaker = data.get("default_speaker", "")
    if not isinstance(default_speaker, str):
        msg = f"{path}: default_speaker must be a string"
        raise ReadingSidecarError(msg)

    session_date = data.get("session_date")
    if session_date is not None and not isinstance(session_date, str):
        session_date = str(session_date)

    raw_corrections = data.get("name_corrections") or {}
    if not isinstance(raw_corrections, dict):
        msg = f"{path}: name_corrections must be a mapping"
        raise ReadingSidecarError(msg)
    name_corrections = {str(k): str(v) for k, v in raw_corrections.items()}

    raw_warnings = data.get("gap_warnings") or []
    if not isinstance(raw_warnings, list):
        msg = f"{path}: gap_warnings must be a list"
        raise ReadingSidecarError(msg)
    gap_warnings = [_warning_from_dict(w, str(path)) for w in raw_warnings]

    return Sidecar(
        default_speaker=default_speaker,
        name_corrections=name_corrections,
        session_date=session_date,
        gap_warnings=gap_warnings,
    )
