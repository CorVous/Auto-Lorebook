"""DB-backed wiki context; YAML is a lazy-backfill source."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from auto_lorebook.schema import read_tolerant_yaml
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

_logger = logging.getLogger("auto_lorebook.wiki_context")
_MAX_SCHEMA = 1
_FILE_LABEL = ".wiki-context.yaml"


@dataclass
class SettingContext:
    """Setting sub-object from wiki_context."""

    name: str | None = None
    description: str | None = None


@dataclass
class WikiContext:
    """In-memory wiki context."""

    setting: SettingContext = field(default_factory=SettingContext)
    naming_conventions: str | None = None
    interpretation_defaults: str | None = None
    recurring_speakers: list[dict[str, Any]] = field(default_factory=list)


def _is_empty(row: Any) -> bool:  # noqa: ANN401
    """Check if row has no content (all fields None or default empty)."""
    return (
        row["setting_name"] is None
        and row["setting_description"] is None
        and row["naming_conventions"] is None
        and row["interpretation_defaults"] is None
        and (row["recurring_speakers_json"] or "[]") == "[]"
    )


def _row_to_ctx(row: Any) -> WikiContext:  # noqa: ANN401
    speakers: list[dict[str, Any]] = json.loads(row["recurring_speakers_json"] or "[]")
    return WikiContext(
        setting=SettingContext(
            name=row["setting_name"],
            description=row["setting_description"],
        ),
        naming_conventions=row["naming_conventions"],
        interpretation_defaults=row["interpretation_defaults"],
        recurring_speakers=speakers,
    )


def read(conn: sqlite3.Connection, *, wiki_repo: Path | None = None) -> WikiContext:
    """Return singleton wiki context (id=1).

    Lazy-backfills from ``<wiki_repo>/.wiki-context.yaml`` when row is
    empty and *wiki_repo* is provided.
    """
    row = conn.execute("SELECT * FROM wiki_context WHERE id = 1").fetchone()
    if row is None:
        # seed the singleton row
        now = format_iso_now()
        conn.execute(
            "INSERT OR IGNORE INTO wiki_context(id, updated_at) VALUES (1, ?)", (now,)
        )
        row = conn.execute("SELECT * FROM wiki_context WHERE id = 1").fetchone()
    if row is not None and _is_empty(row) and wiki_repo is not None:
        _backfill_from_yaml(conn, wiki_repo)
        row = conn.execute("SELECT * FROM wiki_context WHERE id = 1").fetchone()
    if row is None:
        return WikiContext()
    return _row_to_ctx(row)


def write(conn: sqlite3.Connection, ctx: WikiContext) -> None:
    """Upsert row id=1 with *ctx*."""
    now = format_iso_now()
    conn.execute(
        """
        INSERT INTO wiki_context(
            id, setting_name, setting_description, naming_conventions,
            interpretation_defaults, recurring_speakers_json, updated_at
        ) VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            setting_name            = excluded.setting_name,
            setting_description     = excluded.setting_description,
            naming_conventions      = excluded.naming_conventions,
            interpretation_defaults = excluded.interpretation_defaults,
            recurring_speakers_json = excluded.recurring_speakers_json,
            updated_at              = excluded.updated_at
        """,
        (
            ctx.setting.name,
            ctx.setting.description,
            ctx.naming_conventions,
            ctx.interpretation_defaults,
            json.dumps(ctx.recurring_speakers),
            now,
        ),
    )


def _backfill_from_yaml(conn: sqlite3.Connection, wiki_repo: Path) -> None:
    """Read YAML and write to DB once; idempotent."""
    path = wiki_repo / _FILE_LABEL
    raw = read_tolerant_yaml(path, _FILE_LABEL, max_supported=_MAX_SCHEMA)
    if raw is None:
        return
    setting_raw: dict[str, Any] = raw.get("setting") or {}
    ctx = WikiContext(
        setting=SettingContext(
            name=setting_raw.get("name") or None,
            description=setting_raw.get("description") or None,
        ),
        naming_conventions=raw.get("naming_conventions") or None,
        interpretation_defaults=raw.get("interpretation_defaults") or None,
        recurring_speakers=raw.get("recurring_speakers") or [],
    )
    _logger.info("backfilling wiki_context from %s", path)
    write(conn, ctx)
