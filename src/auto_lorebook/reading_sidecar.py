"""pending/<id>/reading/reading.yaml: sidecar for the reading session.

Stores session metadata (default_speaker, session_date, name_corrections)
separately from per-segment content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text
from auto_lorebook.schema import SchemaVersionError, read_schema_version

if TYPE_CHECKING:
    from pathlib import Path

_MAX_SCHEMA = 1


class ReadingSidecarError(ValueError):
    """Raised when reading.yaml is missing or malformed."""


@dataclass
class Sidecar:
    """In-memory representation of reading.yaml."""

    default_speaker: str
    name_corrections: dict[str, str] = field(default_factory=dict)
    session_date: str | None = None


def _to_dict(sc: Sidecar) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "default_speaker": sc.default_speaker,
        "session_date": sc.session_date,
        "name_corrections": dict(sc.name_corrections),
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

    return Sidecar(
        default_speaker=default_speaker,
        name_corrections=name_corrections,
        session_date=session_date,
    )
