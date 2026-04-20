"""Tests for the YouTube subtitle downloader."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from auto_lorebook.parsers.srt import parse_srt
from auto_lorebook.parsers.youtube import (
    YouTubeSubtitleError,
    fetch_youtube_transcript,
)

if TYPE_CHECKING:
    from pathlib import Path

# --- URL validation (tested via the public API) ---


def test_fetch_accepts_standard_youtube_url() -> None:
    """Standard youtube.com/watch URL is accepted without download error."""

    def fake_download(_url: str, out_dir: Path, lang: str) -> None:
        (out_dir / f"vid.{lang}.srt").write_text(SAMPLE_SRT, encoding="utf-8")

    with patch("auto_lorebook.parsers.youtube._yt_dlp_download", fake_download):
        fetch_youtube_transcript("https://www.youtube.com/watch?v=abc123")


def test_fetch_accepts_short_youtu_be_url() -> None:
    """Short youtu.be URL is accepted without error."""

    def fake_download(_url: str, out_dir: Path, lang: str) -> None:
        (out_dir / f"vid.{lang}.srt").write_text(SAMPLE_SRT, encoding="utf-8")

    with patch("auto_lorebook.parsers.youtube._yt_dlp_download", fake_download):
        fetch_youtube_transcript("https://youtu.be/abc123")


def test_fetch_accepts_mobile_youtube_url() -> None:
    """Mobile m.youtube.com URL is accepted without error."""

    def fake_download(_url: str, out_dir: Path, lang: str) -> None:
        (out_dir / f"vid.{lang}.srt").write_text(SAMPLE_SRT, encoding="utf-8")

    with patch("auto_lorebook.parsers.youtube._yt_dlp_download", fake_download):
        fetch_youtube_transcript("https://m.youtube.com/watch?v=abc123")


def test_fetch_rejects_non_youtube_url() -> None:
    """Non-YouTube URL raises ValueError before any download."""
    with pytest.raises(ValueError, match="YouTube"):
        fetch_youtube_transcript("https://vimeo.com/123456")


def test_fetch_rejects_plain_text() -> None:
    """Arbitrary string raises ValueError."""
    with pytest.raises(ValueError, match="YouTube"):
        fetch_youtube_transcript("not a url at all")


def test_fetch_rejects_empty_string() -> None:
    """Empty string raises ValueError."""
    with pytest.raises(ValueError, match="YouTube"):
        fetch_youtube_transcript("")


def test_fetch_rejects_non_http_scheme() -> None:
    """YouTube URL with non-http/https scheme raises ValueError."""
    with pytest.raises(ValueError, match="YouTube"):
        fetch_youtube_transcript("ftp://youtube.com/watch?v=abc123")


def test_fetch_rejects_javascript_scheme() -> None:
    """YouTube URL with javascript: scheme raises ValueError."""
    with pytest.raises(ValueError, match="YouTube"):
        fetch_youtube_transcript("javascript://youtube.com/watch?v=abc123")


# --- fetch_youtube_transcript ---

SAMPLE_SRT = """\
1
00:00:00,000 --> 00:00:02,000
Hello from YouTube.

2
00:00:02,500 --> 00:00:04,000
This is a test subtitle.
"""


def _fake_srt_download(_url: str, out_dir: Path, lang: str) -> None:
    """Write a fake SRT file simulating yt-dlp output."""
    (out_dir / f"testvideo.{lang}.srt").write_text(SAMPLE_SRT, encoding="utf-8")


def test_fetch_youtube_transcript_returns_srt_content() -> None:
    """Returns the SRT content string when subtitles are found."""
    with patch("auto_lorebook.parsers.youtube._yt_dlp_download", _fake_srt_download):
        srt_content, _ = fetch_youtube_transcript(
            "https://www.youtube.com/watch?v=testvideo"
        )
    assert "Hello from YouTube." in srt_content


def test_fetch_youtube_transcript_source_metadata_url() -> None:
    """Returned SourceMetadata carries the original YouTube URL."""
    target_url = "https://www.youtube.com/watch?v=testvideo"
    with patch("auto_lorebook.parsers.youtube._yt_dlp_download", _fake_srt_download):
        _, source = fetch_youtube_transcript(target_url)
    assert source.source_url == target_url


def test_fetch_youtube_transcript_source_metadata_filename() -> None:
    """Returned SourceMetadata filename matches the downloaded SRT filename."""
    with patch("auto_lorebook.parsers.youtube._yt_dlp_download", _fake_srt_download):
        _, source = fetch_youtube_transcript(
            "https://www.youtube.com/watch?v=testvideo"
        )
    assert source.filename.endswith(".srt")


def test_fetch_youtube_transcript_no_subtitles_raises() -> None:
    """Raises YouTubeSubtitleError when yt-dlp writes no SRT files."""

    def no_files(_url: str, _out_dir: Path, _lang: str) -> None:
        pass  # writes nothing

    with (
        patch("auto_lorebook.parsers.youtube._yt_dlp_download", no_files),
        pytest.raises(YouTubeSubtitleError),
    ):
        fetch_youtube_transcript("https://www.youtube.com/watch?v=nosubs")


def test_fetch_youtube_transcript_passes_lang_to_downloader() -> None:
    """The lang parameter is forwarded to _yt_dlp_download."""
    received: list[str] = []

    def capture_lang(_url: str, out_dir: Path, lang: str) -> None:
        received.append(lang)
        (out_dir / f"testvideo.{lang}.srt").write_text(SAMPLE_SRT, encoding="utf-8")

    with patch("auto_lorebook.parsers.youtube._yt_dlp_download", capture_lang):
        fetch_youtube_transcript("https://www.youtube.com/watch?v=testvideo", lang="fr")
    assert received == ["fr"]


def test_fetch_youtube_transcript_calls_downloader_with_url() -> None:
    """The original URL is forwarded to _yt_dlp_download."""
    received: list[str] = []

    def capture_url(url: str, out_dir: Path, lang: str) -> None:
        received.append(url)
        (out_dir / f"testvideo.{lang}.srt").write_text(SAMPLE_SRT, encoding="utf-8")

    target = "https://www.youtube.com/watch?v=testvideo"
    with patch("auto_lorebook.parsers.youtube._yt_dlp_download", capture_url):
        fetch_youtube_transcript(target)
    assert received == [target]


def test_fetch_youtube_transcript_integrates_with_srt_parser() -> None:
    """The returned SRT content can be parsed by parse_srt."""
    with patch("auto_lorebook.parsers.youtube._yt_dlp_download", _fake_srt_download):
        srt_content, _ = fetch_youtube_transcript(
            "https://www.youtube.com/watch?v=testvideo"
        )
    blocks = parse_srt(srt_content)
    assert len(blocks) == 2
    assert blocks[0].text == "Hello from YouTube."
