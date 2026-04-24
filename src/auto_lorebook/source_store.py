"""Copy transcripts into sources/<source_id>/ with duplicate detection."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import shutil
import tempfile
from pathlib import Path

_logger = logging.getLogger(__name__)

_CHUNK = 65536


class DuplicateSourceError(Exception):
    """Re-ingest of an identical file that is already stored."""


class CollisionError(Exception):
    """Different file hashes collide on the same source_id."""


def _hash_bytes(path: Path) -> str:
    """SHA-256 of file bytes, streamed."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


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
    incoming_hash = _hash_bytes(source_path)

    if dest_dir.exists() and dest.exists():
        stored_hash = _hash_bytes(dest)
        if stored_hash == incoming_hash:
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

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Atomic copy: write to temp, then replace
    fd, tmp = tempfile.mkstemp(dir=dest_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as out, source_path.open("rb") as src:
            shutil.copyfileobj(src, out)
        Path(tmp).replace(dest)
    except Exception:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise

    return dest, fname
