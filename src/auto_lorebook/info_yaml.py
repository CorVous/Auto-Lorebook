"""Per-source info.yaml read/write."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text
from auto_lorebook.schema import SchemaVersionError, read_schema_version

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)

_MAX_SCHEMA = 1


@dataclass
class SourceContext:
    """info.yaml context sub-object."""

    perspective: str | None = None
    source_nature: str | None = None
    setting: str | None = None
    speakers: list[dict[str, str]] = field(default_factory=list)
    notes: str | None = None


@dataclass
class Info:
    """In-memory representation of sources/<source_id>/info.yaml."""

    source_id: str
    source_type: str  # youtube | srt | text | markdown
    fetched_at: str  # RFC 3339 UTC
    source_url: str | None = None
    title: str | None = None
    duration_seconds: int | None = None
    caption_type: str | None = None
    session_date: str | None = None
    context: SourceContext = field(default_factory=SourceContext)
    transcript_filename: str | None = None


class InfoError(ValueError):
    """Raised when info.yaml is missing or malformed."""


def _to_dict(info: Info) -> dict[str, Any]:
    ctx = info.context
    return {
        "schema_version": 1,
        "source_id": info.source_id,
        "source_type": info.source_type,
        "source_url": info.source_url,
        "title": info.title,
        "duration_seconds": info.duration_seconds,
        "caption_type": info.caption_type,
        "fetched_at": info.fetched_at,
        "session_date": info.session_date,
        "transcript_filename": info.transcript_filename,
        "context": {
            "perspective": ctx.perspective,
            "source_nature": ctx.source_nature,
            "setting": ctx.setting,
            "speakers": ctx.speakers,
            "notes": ctx.notes,
        },
    }


def _from_dict(data: dict[str, Any]) -> Info:
    ctx_raw: dict[str, Any] = data.get("context") or {}
    ctx = SourceContext(
        perspective=ctx_raw.get("perspective") or None,
        source_nature=ctx_raw.get("source_nature") or None,
        setting=ctx_raw.get("setting") or None,
        speakers=ctx_raw.get("speakers") or [],
        notes=ctx_raw.get("notes") or None,
    )
    return Info(
        source_id=data["source_id"],
        source_type=data["source_type"],
        fetched_at=data["fetched_at"],
        source_url=data.get("source_url") or None,
        title=data.get("title") or None,
        duration_seconds=data.get("duration_seconds"),
        caption_type=data.get("caption_type") or None,
        session_date=data.get("session_date") or None,
        transcript_filename=data.get("transcript_filename") or None,
        context=ctx,
    )


def read(path: Path) -> Info:
    """Read and validate info.yaml.

    :raises InfoError: if file is missing or malformed
    """
    if not path.exists():
        msg = f"{path}: file not found"
        raise InfoError(msg)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a YAML mapping"
        raise InfoError(msg)
    try:
        read_schema_version(raw, str(path), max_supported=_MAX_SCHEMA)
    except SchemaVersionError as e:
        raise InfoError(str(e)) from e
    return _from_dict(raw)


def write(info: Info, path: Path) -> None:
    """Atomically write info to path (tempfile + os.replace).

    schema_version is always the first key.
    """
    text = yaml.safe_dump(
        _to_dict(info),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    atomic_write_text(path, text)
