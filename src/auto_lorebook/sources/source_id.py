"""Source ID derivation and duplicate detection."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from pathlib import Path

_YOUTUBE_HOSTS = frozenset({"www.youtube.com", "youtube.com", "m.youtube.com"})
_YOUTU_BE_HOST = "youtu.be"


def extract_youtube_video_id(url: str) -> str | None:
    """Extract 11-char video ID from a YouTube URL, or None."""
    parsed = urlparse(url)
    if parsed.netloc in _YOUTUBE_HOSTS:
        params = parse_qs(parsed.query)
        ids = params.get("v")
        return ids[0] if ids else None
    if parsed.netloc == _YOUTU_BE_HOST:
        return parsed.path.lstrip("/") or None
    return None


def derive_youtube_source_id(url: str) -> str:
    """Return yt-<video_id> for a YouTube URL.

    :raises ValueError: video ID cannot be extracted
    """
    video_id = extract_youtube_video_id(url)
    if not video_id:
        msg = f"cannot extract YouTube video ID from: {url}"
        raise ValueError(msg)
    return f"yt-{video_id}"


def derive_local_source_id(data: bytes, *, prefix: str = "srt") -> str:
    """Return <prefix>-<first10hex> for local file bytes (SHA-256).

    :param data: raw file bytes
    :param prefix: type prefix (e.g. 'srt', 'txt')
    """
    digest = hashlib.sha256(data).hexdigest()[:10]
    return f"{prefix}-{digest}"


def is_source_duplicate(source_id: str, sources_dir: Path) -> bool:
    """Return True if sources/<source_id>/info.yaml exists (completed ingest)."""
    return (sources_dir / source_id / "info.yaml").exists()
