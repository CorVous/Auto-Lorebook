"""Tests for source_id.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from auto_lorebook.source_id import SourceIdError, derive

# ---------------------------------------------------------------------------
# YouTube URL patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123defgh",
        "https://youtu.be/abc123defgh",
        "https://youtube.com/shorts/abc123defgh",
        "https://m.youtube.com/watch?v=abc123defgh",
        "http://youtube.com/watch?v=abc123defgh",
        "https://www.youtube.com/watch?feature=share&v=abc123defgh",
    ],
)
def test_youtube_url_extracts_video_id(url: str) -> None:
    result = derive(url, None, None)
    assert result == "yt-abc123defgh"


def test_non_youtube_url_raises() -> None:
    with pytest.raises(SourceIdError, match="fetch not implemented"):
        derive("https://example.com/video.srt", None, None)


# ---------------------------------------------------------------------------
# Override
# ---------------------------------------------------------------------------


def test_override_returned_as_is() -> None:
    assert derive("anything", "my-custom-id", None) == "my-custom-id"


def test_override_wins_over_youtube_url() -> None:
    assert derive("https://youtu.be/abc123defgh", "explicit", None) == "explicit"


# ---------------------------------------------------------------------------
# Local file hashing
# ---------------------------------------------------------------------------


def test_srt_file_gets_srt_prefix(tmp_path: Path) -> None:
    f = tmp_path / "clip.srt"
    f.write_bytes(b"hello world")
    result = derive(str(f), None, None)
    assert result.startswith("srt-")
    assert len(result) == len("srt-") + 10


def test_txt_file_gets_txt_prefix(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_bytes(b"hello world")
    result = derive(str(f), None, None)
    assert result.startswith("txt-")


def test_md_file_gets_txt_prefix(tmp_path: Path) -> None:
    f = tmp_path / "notes.md"
    f.write_bytes(b"hello world")
    result = derive(str(f), None, None)
    assert result.startswith("txt-")


def test_hash_is_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_bytes(b"stable content")
    r1 = derive(str(f), None, None)
    r2 = derive(str(f), None, None)
    assert r1 == r2


def test_different_content_different_id(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")
    assert derive(str(a), None, None) != derive(str(b), None, None)


def test_hash_length_is_ten(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_bytes(b"data")
    result = derive(str(f), None, None)
    suffix = result.split("-", 1)[1]
    assert len(suffix) == 10


# ---------------------------------------------------------------------------
# --source-url interactions
# ---------------------------------------------------------------------------


def test_local_srt_with_youtube_source_url(tmp_path: Path) -> None:
    f = tmp_path / "clip.srt"
    f.write_bytes(b"data")
    result = derive(str(f), None, "https://youtu.be/abc123defgh")
    assert result == "yt-abc123defgh"


def test_local_txt_with_youtube_source_url(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_bytes(b"data")
    result = derive(str(f), None, "https://www.youtube.com/watch?v=abc123defgh")
    assert result == "yt-abc123defgh"


def test_local_file_with_non_youtube_source_url(tmp_path: Path) -> None:
    f = tmp_path / "notes.txt"
    f.write_bytes(b"data")
    result = derive(str(f), None, "https://twitch.tv/vod/123")
    assert result.startswith("txt-")


def test_local_srt_with_non_youtube_source_url_gets_srt_prefix(tmp_path: Path) -> None:
    f = tmp_path / "clip.srt"
    f.write_bytes(b"data")
    result = derive(str(f), None, "https://twitch.tv/vod/123")
    assert result.startswith("srt-")
