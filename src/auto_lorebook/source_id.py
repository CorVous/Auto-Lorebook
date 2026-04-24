"""Derive source IDs from paths or URLs."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# YouTube URL patterns
_YT_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/shorts/)"
    r"([A-Za-z0-9_-]{11})"
)

_CHUNK = 65536  # 64 KiB


class SourceIdError(ValueError):
    """Raised when a source ID cannot be derived."""


def _extract_video_id(url: str) -> str | None:
    """Return YouTube video_id from URL, or None if not a YouTube URL."""
    m = _YT_RE.search(url)
    return m.group(1) if m else None


def _hash_file(path: Path) -> str:
    """SHA-256 of file bytes, streamed; return first 10 hex chars."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()[:10]


def _prefix_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".srt":
        return "srt"
    return "txt"


def derive(path_or_url: str, override: str | None, source_url: str | None) -> str:
    """Derive source ID from positional arg, override, and optional source URL.

    Priority: override > YouTube URL > content hash.

    :param path_or_url: positional CLI argument (local path or URL)
    :param override: value of --source-id flag; returned as-is if set
    :param source_url: value of --source-url flag
    :raises SourceIdError: if positional is a URL but fetch is not implemented
    """
    if override:
        return override

    # Check if the positional itself is a URL
    if path_or_url.startswith(("http://", "https://")):
        vid = _extract_video_id(path_or_url)
        if vid:
            return f"yt-{vid}"
        msg = "fetch not implemented; pass a local file instead of a non-YouTube URL"
        raise SourceIdError(msg)

    # Positional is a local file path
    path = Path(path_or_url)

    # If --source-url is a YouTube URL, use that video_id; otherwise hash the file
    if source_url:
        vid = _extract_video_id(source_url)
        if vid:
            return f"yt-{vid}"

    prefix = _prefix_for_path(path)
    return f"{prefix}-{_hash_file(path)}"
