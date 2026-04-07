"""Tests for source metadata model."""

from __future__ import annotations

import pytest

from auto_lorebook.parsers.source import (
    SourceMetadata,
    SourceType,
    make_source,
    youtube_timestamp_url,
)

# ---------------------------------------------------------------------------
# SourceType
# ---------------------------------------------------------------------------


class TestSourceType:
    """Tests for the SourceType enum."""

    def test_has_youtube(self) -> None:
        """SourceType has a youtube member."""
        assert SourceType.youtube

    def test_has_url(self) -> None:
        """SourceType has a url member."""
        assert SourceType.url

    def test_has_local(self) -> None:
        """SourceType has a local member."""
        assert SourceType.local


# ---------------------------------------------------------------------------
# SourceMetadata
# ---------------------------------------------------------------------------


class TestSourceMetadata:
    """Tests for the SourceMetadata dataclass."""

    def test_youtube_source(self) -> None:
        """Can create a YouTube source metadata object."""
        src = SourceMetadata(
            url="https://youtube.com/watch?v=abc123",
            source_type=SourceType.youtube,
            filename=None,
        )
        assert src.source_type == SourceType.youtube
        assert src.url == "https://youtube.com/watch?v=abc123"
        assert src.filename is None

    def test_url_source(self) -> None:
        """Can create a generic URL source metadata object."""
        src = SourceMetadata(
            url="https://example.com/lore",
            source_type=SourceType.url,
            filename=None,
        )
        assert src.source_type == SourceType.url

    def test_local_source(self) -> None:
        """Can create a local file source metadata object."""
        src = SourceMetadata(
            url=None,
            source_type=SourceType.local,
            filename="notes.txt",
        )
        assert src.source_type == SourceType.local
        assert src.filename == "notes.txt"
        assert src.url is None


# ---------------------------------------------------------------------------
# youtube_timestamp_url
# ---------------------------------------------------------------------------


class TestYoutubeTimestampUrl:
    """Tests for youtube_timestamp_url."""

    def test_appends_t_param(self) -> None:
        """Appends &t=<seconds> to a YouTube URL."""
        url = "https://youtube.com/watch?v=abc123"
        result = youtube_timestamp_url(url, 272.0)
        assert result == "https://youtube.com/watch?v=abc123&t=272"

    def test_truncates_to_int(self) -> None:
        """Fractional seconds are truncated (not rounded) to whole seconds."""
        url = "https://youtube.com/watch?v=abc123"
        result = youtube_timestamp_url(url, 90.9)
        assert result == "https://youtube.com/watch?v=abc123&t=90"

    def test_zero_seconds(self) -> None:
        """Zero seconds appends &t=0."""
        url = "https://youtube.com/watch?v=abc123"
        result = youtube_timestamp_url(url, 0.0)
        assert result == "https://youtube.com/watch?v=abc123&t=0"

    def test_youtu_be_short_url(self) -> None:
        """Works with youtu.be short URLs too."""
        url = "https://youtu.be/abc123"
        result = youtube_timestamp_url(url, 60.0)
        assert result == "https://youtu.be/abc123&t=60"

    def test_non_youtube_url_raises(self) -> None:
        """Raises ValueError for non-YouTube URLs."""
        with pytest.raises(ValueError, match="YouTube"):
            youtube_timestamp_url("https://example.com/page", 10.0)


# ---------------------------------------------------------------------------
# make_source
# ---------------------------------------------------------------------------


class TestMakeSource:
    """Tests for make_source factory."""

    def test_youtube_watch_url(self) -> None:
        """youtube.com/watch URLs produce SourceType.youtube."""
        src = make_source(url="https://youtube.com/watch?v=abc123", filename="vid.srt")
        assert src.source_type == SourceType.youtube
        assert src.url == "https://youtube.com/watch?v=abc123"

    def test_youtu_be_url(self) -> None:
        """youtu.be short URLs produce SourceType.youtube."""
        src = make_source(url="https://youtu.be/abc123", filename="vid.srt")
        assert src.source_type == SourceType.youtube

    def test_generic_url(self) -> None:
        """Non-YouTube http URLs produce SourceType.url."""
        src = make_source(url="https://example.com/lore", filename="lore.txt")
        assert src.source_type == SourceType.url
        assert src.url == "https://example.com/lore"

    def test_no_url_is_local(self) -> None:
        """No URL produces SourceType.local."""
        src = make_source(url=None, filename="campaign-notes.txt")
        assert src.source_type == SourceType.local
        assert src.filename == "campaign-notes.txt"
        assert src.url is None

    def test_filename_always_stored(self) -> None:
        """Filename is stored regardless of URL."""
        src = make_source(url="https://youtube.com/watch?v=x", filename="sub.srt")
        assert src.filename == "sub.srt"
