"""Tests for local SRT ingest routing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from auto_lorebook.sources.ingest import (
    LocalIngestResult,
    ingest_local_srt,
    is_youtube_url,
)

if TYPE_CHECKING:
    from pathlib import Path

_SIMPLE_SRT = "1\n00:00:00,000 --> 00:01:00,000\nHello\n"


def _write_srt(path: Path, content: str = _SIMPLE_SRT) -> None:
    path.write_text(content, encoding="utf-8")


# ── URL detection ────────────────────────────────────────────────────────────


def test_is_youtube_url_https() -> None:
    """https:// URLs are YouTube."""
    assert is_youtube_url("https://www.youtube.com/watch?v=abc")


def test_is_youtube_url_http() -> None:
    """http:// URLs are YouTube."""
    assert is_youtube_url("http://youtube.com/watch?v=abc")


def test_is_youtube_url_youtube_prefix() -> None:
    """youtube.com/ prefix detected."""
    assert is_youtube_url("youtube.com/watch?v=abc")


def test_is_youtube_url_youtu_be_prefix() -> None:
    """youtu.be/ prefix detected."""
    assert is_youtube_url("youtu.be/abc123")


def test_is_youtube_url_local_path_false() -> None:
    """Local file path not detected as YouTube."""
    assert not is_youtube_url("/path/to/session.srt")


def test_is_youtube_url_relative_path_false() -> None:
    """Relative path not detected as YouTube."""
    assert not is_youtube_url("session.srt")


# ── ingest_local_srt ─────────────────────────────────────────────────────────


def test_ingest_local_srt_returns_result(tmp_path: Path) -> None:
    """ingest_local_srt returns LocalIngestResult."""
    srt_file = tmp_path / "session.srt"
    _write_srt(srt_file)
    sources_dir = tmp_path / "sources"
    result = ingest_local_srt(srt_file, sources_dir)
    assert isinstance(result, LocalIngestResult)


def test_ingest_local_srt_source_type(tmp_path: Path) -> None:
    """source_type is 'srt'."""
    srt_file = tmp_path / "session.srt"
    _write_srt(srt_file)
    result = ingest_local_srt(srt_file, tmp_path / "sources")
    assert result.source_type == "srt"


def test_ingest_local_srt_caption_type(tmp_path: Path) -> None:
    """caption_type is 'n/a' for local files."""
    srt_file = tmp_path / "session.srt"
    _write_srt(srt_file)
    result = ingest_local_srt(srt_file, tmp_path / "sources")
    assert result.caption_type == "n/a"


def test_ingest_local_srt_title_from_stem(tmp_path: Path) -> None:
    """Title derived from filename stem when not provided."""
    srt_file = tmp_path / "session-14.srt"
    _write_srt(srt_file)
    result = ingest_local_srt(srt_file, tmp_path / "sources")
    assert result.title == "session-14"


def test_ingest_local_srt_title_override(tmp_path: Path) -> None:
    """Explicit title overrides filename stem."""
    srt_file = tmp_path / "session.srt"
    _write_srt(srt_file)
    result = ingest_local_srt(srt_file, tmp_path / "sources", title="My Session")
    assert result.title == "My Session"


def test_ingest_local_srt_duration_from_last_cue(tmp_path: Path) -> None:
    """Duration derived from last cue end timestamp."""
    srt_file = tmp_path / "session.srt"
    _write_srt(srt_file)  # ends at 0:01:00 = 60s
    result = ingest_local_srt(srt_file, tmp_path / "sources")
    assert result.duration_seconds == pytest.approx(60.0)


def test_ingest_local_srt_writes_transcript_cache(tmp_path: Path) -> None:
    """Cache miss: transcript written to sources/<id>/transcript.en.srt."""
    srt_file = tmp_path / "session.srt"
    _write_srt(srt_file)
    sources_dir = tmp_path / "sources"
    result = ingest_local_srt(srt_file, sources_dir)
    assert result.transcript_path.exists()
    assert not result.from_cache


def test_ingest_local_srt_cache_hit(tmp_path: Path) -> None:
    """Same bytes from different path uses cached transcript."""
    srt_a = tmp_path / "a.srt"
    srt_b = tmp_path / "b.srt"  # same content, different name
    _write_srt(srt_a)
    _write_srt(srt_b)
    sources_dir = tmp_path / "sources"
    # first ingest: cache miss
    result_a = ingest_local_srt(srt_a, sources_dir)
    # second ingest with same bytes: cache hit
    result_b = ingest_local_srt(srt_b, sources_dir)
    assert result_a.source_id == result_b.source_id
    assert result_b.from_cache


def test_ingest_local_srt_no_source_url_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Absent source_url logs a warning."""
    srt_file = tmp_path / "session.srt"
    _write_srt(srt_file)
    sources_dir = tmp_path / "sources"
    with caplog.at_level(logging.WARNING):
        ingest_local_srt(srt_file, sources_dir)
    assert "citation" in caplog.text


def test_ingest_local_srt_source_url_stored(tmp_path: Path) -> None:
    """Provided source_url stored in result."""
    srt_file = tmp_path / "session.srt"
    _write_srt(srt_file)
    result = ingest_local_srt(
        srt_file, tmp_path / "sources", source_url="https://example.com/vod"
    )
    assert result.source_url == "https://example.com/vod"


def test_ingest_local_srt_source_id_deterministic(tmp_path: Path) -> None:
    """Same file content always yields same source_id."""
    srt_file = tmp_path / "session.srt"
    _write_srt(srt_file)
    r1 = ingest_local_srt(srt_file, tmp_path / "sources1")
    srt_file2 = tmp_path / "other.srt"
    _write_srt(srt_file2)
    r2 = ingest_local_srt(srt_file2, tmp_path / "sources2")
    assert r1.source_id == r2.source_id
