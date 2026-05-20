"""Copy transcripts into sources/<source_id>/ with duplicate detection.

Also provides record_in_db for writing sources + ingests rows after copy.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook._io import atomic_copy, hash_file
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from auto_lorebook.info_yaml import Info

_logger = logging.getLogger(__name__)


class DuplicateSourceError(Exception):
    """Re-ingest of an identical file that is already stored."""


class CollisionError(Exception):
    """Different file hashes collide on the same source_id."""


def _transcript_filename(source_type: str) -> str:
    if source_type in {"srt", "youtube"}:
        return "transcript.en.srt"
    if source_type == "markdown":
        return "transcript.md"
    return "transcript.txt"


def copy_transcript(
    source_path: Path,
    source_id: str,
    source_type: str,
    wiki_repo: Path,
) -> tuple[Path, str]:
    """Copy transcript file into sources/<source_id>/ atomically.

    :param source_path: local file to copy
    :param source_id: derived source ID
    :param source_type: one of youtube | srt | text | markdown
    :param wiki_repo: root of the wiki repository
    :returns: (dest_path, transcript_filename)
    :raises DuplicateSourceError: if file already stored with matching hash
    :raises CollisionError: if source_id dir exists but hashes differ
    """
    dest_dir = wiki_repo / "sources" / source_id
    fname = _transcript_filename(source_type)
    dest = dest_dir / fname

    if dest.exists():
        if hash_file(dest) == hash_file(source_path):
            msg = (
                f"Source '{source_id}' already ingested with the same content. "
                "Run `configure-context` to edit its metadata."
            )
            raise DuplicateSourceError(msg)
        msg = (
            f"Source ID '{source_id}' exists but stored transcript differs. "
            "Use --source-id to specify an explicit ID."
        )
        raise CollisionError(msg)

    atomic_copy(source_path, dest)
    return dest, fname


def record_in_db(
    conn: sqlite3.Connection,
    info: Info,
    source_id: str,
    source_type: str,
) -> None:
    """INSERT OR IGNORE source + ingest rows for idempotent re-runs.

    sources row is keyed by source_id; ingests row uses source_id as ingest_id
    (one ingest per source in the reading pipeline). Calling twice is safe.
    """
    now = format_iso_now()
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(source_id, source_type, source_url, title, duration_seconds, "
        " caption_type, fetched_at, session_date, context_json) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            source_id,
            source_type,
            info.source_url,
            info.title,
            info.duration_seconds,
            info.caption_type,
            info.fetched_at or now,
            info.session_date,
            "{}",
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO ingests "
        "(ingest_id, source_id, started_at, state, default_speaker, "
        " name_corrections_json, session_date) "
        "VALUES (?,?,?,'reading',NULL,'{}',NULL)",
        (source_id, source_id, now),
    )
