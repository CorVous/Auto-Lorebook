"""Per-source info: DB-backed; YAML is lazy-backfill source."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook.schema import SchemaVersionError, read_schema_version

if TYPE_CHECKING:
    import sqlite3

_logger = logging.getLogger("auto_lorebook.info_yaml")
_MAX_SCHEMA = 1


@dataclass
class SourceContext:
    """info context sub-object."""

    perspective: str | None = None
    source_nature: str | None = None
    setting: str | None = None
    speakers: list[dict[str, str]] = field(default_factory=list)
    notes: str | None = None


@dataclass
class Info:
    """In-memory representation of a source row."""

    source_id: str
    source_type: str  # youtube | srt | text | markdown
    fetched_at: str  # RFC 3339 UTC
    source_url: str | None = None
    title: str | None = None
    duration_seconds: int | None = None
    caption_type: str | None = None
    session_date: str | None = None
    context: SourceContext = field(default_factory=SourceContext)


class InfoError(ValueError):
    """Raised when source info is missing or malformed."""


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def transcript_filename_for(source_type: str) -> str:
    """Return default transcript filename for *source_type*."""
    if source_type in {"srt", "youtube"}:
        return "transcript.en.srt"
    if source_type == "markdown":
        return "transcript.md"
    return "transcript.txt"


# ---------------------------------------------------------------------------
# DB API
# ---------------------------------------------------------------------------


def read(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    wiki_repo: Path | None = None,
) -> Info:
    """Return Info for *source_id*; lazy-backfill from YAML if missing.

    :raises InfoError: if still missing after backfill attempt.
    """
    row = _fetch_row(conn, source_id)
    if row is None and wiki_repo is not None:
        _backfill_one(conn, wiki_repo, source_id)
        row = _fetch_row(conn, source_id)
    if row is None:
        msg = f"source not found: {source_id}"
        raise InfoError(msg)
    return _row_to_info(row)


def write(conn: sqlite3.Connection, info: Info) -> None:
    """Upsert *info* into sources (and context_json into that column).

    Coerces ``caption_type='auto'`` → ``'auto-generated'``.
    """
    caption = info.caption_type
    if caption == "auto":
        caption = "auto-generated"
    ctx = info.context
    context_json = json.dumps({
        "perspective": ctx.perspective,
        "source_nature": ctx.source_nature,
        "setting": ctx.setting,
        "speakers": ctx.speakers,
        "notes": ctx.notes,
    })
    conn.execute(
        """
        INSERT INTO sources(
            source_id, source_type, source_url, title, duration_seconds,
            caption_type, fetched_at, session_date, context_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            source_type      = excluded.source_type,
            source_url       = excluded.source_url,
            title            = excluded.title,
            duration_seconds = excluded.duration_seconds,
            caption_type     = excluded.caption_type,
            fetched_at       = excluded.fetched_at,
            session_date     = excluded.session_date,
            context_json     = excluded.context_json
        """,
        (
            info.source_id,
            info.source_type,
            info.source_url,
            info.title,
            info.duration_seconds,
            caption,
            info.fetched_at,
            info.session_date,
            context_json,
        ),
    )


def exists(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    wiki_repo: Path | None = None,
) -> bool:
    """Check presence of *source_id*; lazily backfills if YAML present."""
    if _fetch_row(conn, source_id) is not None:
        return True
    if wiki_repo is not None:
        info_path = wiki_repo / "sources" / source_id / "info.yaml"
        if info_path.exists():
            _backfill_one(conn, wiki_repo, source_id)
            return _fetch_row(conn, source_id) is not None
    return False


def list_source_ids(
    conn: sqlite3.Connection,
    *,
    wiki_repo: Path | None = None,
) -> list[str]:
    """Sorted list of source_ids in DB.

    Triggers full backfill scan when DB has no rows and *wiki_repo* given.
    """
    rows = conn.execute("SELECT source_id FROM sources").fetchall()
    if not rows and wiki_repo is not None:
        _backfill_all(conn, wiki_repo)
        rows = conn.execute("SELECT source_id FROM sources").fetchall()
    return sorted(r[0] for r in rows)


# ---------------------------------------------------------------------------
# YAML compatibility (still used by old callers until transition complete)
# ---------------------------------------------------------------------------


def read_yaml(path: Path) -> Info:
    """Read and validate info.yaml from *path*.

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
    return _from_yaml_dict(raw)


def write_yaml(info: Info, path: Path) -> None:
    """Atomically write *info* to *path* as YAML."""
    from auto_lorebook._io import atomic_write_text  # noqa: PLC0415

    text = yaml.safe_dump(
        _to_yaml_dict(info),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    atomic_write_text(path, text)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _fetch_row(conn: sqlite3.Connection, source_id: str) -> Any:  # noqa: ANN401
    return conn.execute(
        "SELECT * FROM sources WHERE source_id = ?", (source_id,)
    ).fetchone()


def _row_to_info(row: Any) -> Info:  # noqa: ANN401
    ctx_raw: dict[str, Any] = json.loads(row["context_json"] or "{}")
    ctx = SourceContext(
        perspective=ctx_raw.get("perspective") or None,
        source_nature=ctx_raw.get("source_nature") or None,
        setting=ctx_raw.get("setting") or None,
        speakers=ctx_raw.get("speakers") or [],
        notes=ctx_raw.get("notes") or None,
    )
    return Info(
        source_id=row["source_id"],
        source_type=row["source_type"],
        fetched_at=row["fetched_at"],
        source_url=row["source_url"],
        title=row["title"],
        duration_seconds=row["duration_seconds"],
        caption_type=row["caption_type"],
        session_date=row["session_date"],
        context=ctx,
    )


def _from_yaml_dict(data: dict[str, Any]) -> Info:
    try:
        source_id = data["source_id"]
        source_type = data["source_type"]
        fetched_at = data["fetched_at"]
    except KeyError as e:
        msg = f"info.yaml missing required field {e}"
        raise InfoError(msg) from e
    ctx_raw: dict[str, Any] = data.get("context") or {}
    ctx = SourceContext(
        perspective=ctx_raw.get("perspective") or None,
        source_nature=ctx_raw.get("source_nature") or None,
        setting=ctx_raw.get("setting") or None,
        speakers=ctx_raw.get("speakers") or [],
        notes=ctx_raw.get("notes") or None,
    )
    return Info(
        source_id=source_id,
        source_type=source_type,
        fetched_at=fetched_at,
        source_url=data.get("source_url") or None,
        title=data.get("title") or None,
        duration_seconds=data.get("duration_seconds"),
        caption_type=data.get("caption_type") or None,
        session_date=data.get("session_date") or None,
        context=ctx,
    )


def _to_yaml_dict(info: Info) -> dict[str, Any]:
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
        "context": {
            "perspective": ctx.perspective,
            "source_nature": ctx.source_nature,
            "setting": ctx.setting,
            "speakers": ctx.speakers,
            "notes": ctx.notes,
        },
    }


def _backfill_one(
    conn: sqlite3.Connection,
    wiki_repo: Path,
    source_id: str,
) -> None:
    """Read YAML for *source_id* and upsert into DB."""
    info_path = wiki_repo / "sources" / source_id / "info.yaml"
    if not info_path.exists():
        return
    try:
        info = read_yaml(info_path)
    except InfoError as e:
        _logger.warning("backfill skipped %s: %s", source_id, e)
        return
    write(conn, info)
    _logger.info("backfilled info for %s from %s", source_id, info_path)


def _backfill_all(conn: sqlite3.Connection, wiki_repo: Path) -> None:
    """Backfill all source dirs that have an info.yaml."""
    sources_dir = wiki_repo / "sources"
    if not sources_dir.exists():
        return
    for source_dir in sources_dir.iterdir():
        if not source_dir.is_dir():
            continue
        source_id = source_dir.name
        if _fetch_row(conn, source_id) is None:
            _backfill_one(conn, wiki_repo, source_id)
