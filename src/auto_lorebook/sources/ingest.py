"""URL vs local-file routing for source ingestion."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook.sources.source_id import derive_local_source_id
from auto_lorebook.sources.srt import parse_srt

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)

_YOUTUBE_PREFIXES = ("http://", "https://", "youtube.com/", "youtu.be/")


def is_youtube_url(url_or_path: str) -> bool:
    """Return True if input looks like a YouTube URL."""
    return url_or_path.startswith(_YOUTUBE_PREFIXES)


@dataclass(slots=True)
class LocalIngestResult:
    """Result of ingesting a local SRT file."""

    source_id: str
    source_type: str  # "srt"
    source_url: str | None
    title: str
    duration_seconds: float
    caption_type: str  # "n/a"
    srt_text: str
    transcript_path: Path
    from_cache: bool


def ingest_local_srt(
    path: Path,
    sources_dir: Path,
    *,
    source_url: str | None = None,
    title: str | None = None,
) -> LocalIngestResult:
    """Ingest a local SRT file: check cache, copy if needed, derive metadata.

    :param path: local .srt file path
    :param sources_dir: wiki repo sources/ directory
    :param source_url: optional citation URL; warns if absent
    :param title: title override; defaults to path stem
    :return: LocalIngestResult with all metadata
    """
    raw_bytes = path.read_bytes()
    source_id = derive_local_source_id(raw_bytes, prefix="srt")
    transcript_cache = sources_dir / source_id / "transcript.en.srt"
    if transcript_cache.exists():
        print(f"Using cached transcript for {source_id}.")  # noqa: T201
        srt_text = transcript_cache.read_text(encoding="utf-8")
        from_cache = True
    else:
        srt_text = raw_bytes.decode("utf-8")
        transcript_cache.parent.mkdir(parents=True, exist_ok=True)
        transcript_cache.write_bytes(raw_bytes)
        from_cache = False
    if source_url is None:
        _logger.warning(
            "No --source-url provided; facts from this source"
            " will have no citation link."
        )
    cues = parse_srt(srt_text)
    duration = cues[-1].end_seconds if cues else 0.0
    return LocalIngestResult(
        source_id=source_id,
        source_type="srt",
        source_url=source_url,
        title=title or path.stem,
        duration_seconds=duration,
        caption_type="n/a",
        srt_text=srt_text,
        transcript_path=transcript_cache,
        from_cache=from_cache,
    )
