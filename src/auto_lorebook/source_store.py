"""Copy transcripts into sources/<source_id>/ with duplicate detection."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook._io import atomic_copy, hash_file

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)


class DuplicateSourceError(Exception):
    """Re-ingest of an identical file that is already stored."""


class CollisionError(Exception):
    """Different file hashes collide on the same source_id."""


def _transcript_filename(source_path: Path, source_type: str) -> str:
    suffix = source_path.suffix.lower()
    if suffix == ".srt" or source_type == "srt":
        return "transcript.en.srt"
    if suffix == ".md" or source_type == "markdown":
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
    fname = _transcript_filename(source_path, source_type)
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
