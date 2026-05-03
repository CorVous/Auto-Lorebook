"""Tests for reading.py — frontmatter helpers (linkify, apply, read, write).

Assembly tests live in test_reading_assembly.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from auto_lorebook.reading import (
    ReadingError,
    apply_name_corrections,
    linkify_timestamp,
    read_frontmatter,
    write,
)

if TYPE_CHECKING:
    from pathlib import Path


_SAMPLE_READING_MD = """\
---
schema_version: 1
source_id: yt-abc12345678
---
# Reading: Session 3

Some content.
"""


class TestLinkifyTimestamp:
    def test_youtube_watch_url(self) -> None:
        assert (
            linkify_timestamp("https://youtube.com/watch?v=abc", 270)
            == "https://youtube.com/watch?v=abc&t=270"
        )

    def test_youtube_short_url(self) -> None:
        assert (
            linkify_timestamp("https://youtu.be/abc", 270)
            == "https://youtu.be/abc?t=270"
        )

    def test_no_url_returns_none(self) -> None:
        assert linkify_timestamp(None, 270) is None

    def test_preserves_existing_query(self) -> None:
        got = linkify_timestamp("https://example.com/path?x=1", 5)
        assert got == "https://example.com/path?x=1&t=5"


class TestApplyNameCorrections:
    def test_empty_returns_unchanged(self) -> None:
        assert apply_name_corrections("King Fair-on", {}) == "King Fair-on"

    def test_substitutes_literally(self) -> None:
        got = apply_name_corrections("King Fair-on", {"Fair-on": "Theron"})
        assert got == "King Theron"

    def test_handles_multiple(self) -> None:
        got = apply_name_corrections("A says B", {"A": "Alice", "B": "Bob"})
        assert got == "Alice says Bob"


class TestWriteReadFrontmatter:
    def test_write_and_read_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "reading.md"
        write(path, _SAMPLE_READING_MD)
        fm = read_frontmatter(path)
        assert fm["source_id"] == "yt-abc12345678"

    def test_read_frontmatter_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ReadingError):
            read_frontmatter(tmp_path / "nope.md")

    def test_read_frontmatter_no_fence_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "reading.md"
        path.write_text("no frontmatter here", encoding="utf-8")
        with pytest.raises(ReadingError):
            read_frontmatter(path)
