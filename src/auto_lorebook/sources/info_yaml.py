"""info.yaml reader/writer for sources/<source_id>/info.yaml."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, TypedDict, cast

import yaml

from auto_lorebook.schema import TOOL_SCHEMA_VERSION, check_schema_version

if TYPE_CHECKING:
    from pathlib import Path


class ContextBlock(TypedDict):
    """Per-source context metadata."""

    perspective: str | None
    source_nature: str | None
    setting: str | None
    speakers: list[str]
    notes: str | None


class InfoYaml(TypedDict):
    """Source info.yaml schema."""

    schema_version: int
    source_id: str
    source_type: str
    source_url: str | None
    title: str
    duration_seconds: float
    caption_type: str
    fetched_at: str  # RFC 3339 UTC
    session_date: str | None
    context: ContextBlock


def _now_utc() -> str:
    """Return current UTC time as RFC 3339 string."""
    return datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _str_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


def make_info_yaml(
    *,
    source_id: str,
    source_type: str,
    source_url: str | None,
    title: str,
    duration_seconds: float,
    caption_type: str,
    fetched_at: str | None = None,
) -> InfoYaml:
    """Construct a default InfoYaml dict for a new source."""
    return InfoYaml(
        schema_version=TOOL_SCHEMA_VERSION,
        source_id=source_id,
        source_type=source_type,
        source_url=source_url,
        title=title,
        duration_seconds=duration_seconds,
        caption_type=caption_type,
        fetched_at=fetched_at or _now_utc(),
        session_date=None,
        context=ContextBlock(
            perspective=None,
            source_nature=None,
            setting=None,
            speakers=[],
            notes=None,
        ),
    )


def write_info_yaml(path: Path, info: InfoYaml) -> None:
    """Write InfoYaml to disk as YAML, creating parent dirs.

    :param path: destination path
    :param info: data to serialize
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    ctx = info["context"]
    data: dict[str, object] = {
        "schema_version": info["schema_version"],
        "source_id": info["source_id"],
        "source_type": info["source_type"],
        "source_url": info["source_url"],
        "title": info["title"],
        "duration_seconds": info["duration_seconds"],
        "caption_type": info["caption_type"],
        "fetched_at": info["fetched_at"],
        "session_date": info["session_date"],
        "context": {
            "perspective": ctx["perspective"],
            "source_nature": ctx["source_nature"],
            "setting": ctx["setting"],
            "speakers": list(ctx["speakers"]),
            "notes": ctx["notes"],
        },
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)


def read_info_yaml(path: Path) -> InfoYaml:
    """Read and validate info.yaml from disk.

    :param path: source path
    :raises SchemaVersionError: schema version mismatch
    """
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    data = cast("dict[str, object]", raw or {})
    check_schema_version(data, str(path))
    ctx_raw = cast("dict[str, object]", data.get("context") or {})
    speakers_raw = ctx_raw.get("speakers")
    speakers = [str(s) for s in speakers_raw] if isinstance(speakers_raw, list) else []
    context = ContextBlock(
        perspective=_str_or_none(ctx_raw.get("perspective")),
        source_nature=_str_or_none(ctx_raw.get("source_nature")),
        setting=_str_or_none(ctx_raw.get("setting")),
        speakers=speakers,
        notes=_str_or_none(ctx_raw.get("notes")),
    )
    return InfoYaml(
        schema_version=int(str(data.get("schema_version", 1))),
        source_id=str(data.get("source_id", "")),
        source_type=str(data.get("source_type", "")),
        source_url=_str_or_none(data.get("source_url")),
        title=str(data.get("title", "")),
        duration_seconds=float(str(data.get("duration_seconds", 0))),
        caption_type=str(data.get("caption_type", "")),
        fetched_at=str(data.get("fetched_at", "")),
        session_date=_str_or_none(data.get("session_date")),
        context=context,
    )
