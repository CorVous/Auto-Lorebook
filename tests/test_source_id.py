"""Tests for source ID derivation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from auto_lorebook.sources.source_id import (
    derive_local_source_id,
    derive_youtube_source_id,
    extract_youtube_video_id,
    is_source_duplicate,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_extract_youtube_video_id_watch_url() -> None:
    """Standard watch URL yields video ID."""
    assert (
        extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        == "dQw4w9WgXcQ"
    )


def test_extract_youtube_video_id_short_url() -> None:
    """youtu.be short URL yields video ID."""
    assert extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_extract_youtube_video_id_no_www() -> None:
    """youtube.com without www yields video ID."""
    assert (
        extract_youtube_video_id("https://youtube.com/watch?v=abc12345678")
        == "abc12345678"
    )


def test_extract_youtube_video_id_non_youtube_returns_none() -> None:
    """Non-YouTube URL returns None."""
    assert extract_youtube_video_id("https://example.com/video") is None


def test_extract_youtube_video_id_local_path_returns_none() -> None:
    """/path/to/file returns None."""
    assert extract_youtube_video_id("/path/to/file.srt") is None


def test_derive_youtube_source_id_format() -> None:
    """Returns yt-<video_id>."""
    result = derive_youtube_source_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert result == "yt-dQw4w9WgXcQ"


def test_derive_youtube_source_id_invalid_raises() -> None:
    """Non-YouTube URL raises ValueError."""
    with pytest.raises(ValueError, match="cannot extract"):
        derive_youtube_source_id("https://example.com/")


def test_derive_local_source_id_format() -> None:
    """Returns srt-<10 hex chars>."""
    result = derive_local_source_id(b"test content")
    assert result.startswith("srt-")
    assert len(result) == 14  # "srt-" + 10


def test_derive_local_source_id_deterministic() -> None:
    """Same bytes always yield same ID."""
    data = b"same content"
    assert derive_local_source_id(data) == derive_local_source_id(data)


def test_derive_local_source_id_different_content() -> None:
    """Different bytes yield different IDs."""
    assert derive_local_source_id(b"aaa") != derive_local_source_id(b"bbb")


def test_derive_local_source_id_custom_prefix() -> None:
    """Custom prefix is used."""
    result = derive_local_source_id(b"data", prefix="txt")
    assert result.startswith("txt-")


def test_is_source_duplicate_false_when_no_info_yaml(tmp_path: Path) -> None:
    """Returns False when info.yaml absent."""
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    assert not is_source_duplicate("yt-abc123", sources_dir)


def test_is_source_duplicate_true_when_info_yaml_exists(tmp_path: Path) -> None:
    """Returns True when sources/<id>/info.yaml exists."""
    sources_dir = tmp_path / "sources"
    (sources_dir / "yt-abc123").mkdir(parents=True)
    (sources_dir / "yt-abc123" / "info.yaml").write_text(
        "schema_version: 1\n", encoding="utf-8"
    )
    assert is_source_duplicate("yt-abc123", sources_dir)
