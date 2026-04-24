"""Derive source IDs from paths or URLs."""

from __future__ import annotations

import re
from pathlib import Path

from auto_lorebook._io import hash_file

_YT_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/shorts/)"
    r"([A-Za-z0-9_-]{11})"
)


class SourceIdError(ValueError):
    """Raised when a source ID cannot be derived."""


def extract_video_id(url: str) -> str | None:
    """Return YouTube video_id from URL, or None if not a YouTube URL."""
    m = _YT_RE.search(url)
    return m.group(1) if m else None


def _prefix_for_path(path: Path) -> str:
    if path.suffix.lower() == ".srt":
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

    if path_or_url.startswith(("http://", "https://")):
        vid = extract_video_id(path_or_url)
        if vid:
            return f"yt-{vid}"
        msg = "fetch not implemented; pass a local file instead of a non-YouTube URL"
        raise SourceIdError(msg)

    path = Path(path_or_url)

    if source_url:
        vid = extract_video_id(source_url)
        if vid:
            return f"yt-{vid}"

    return f"{_prefix_for_path(path)}-{hash_file(path)[:10]}"
