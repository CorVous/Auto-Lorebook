"""ReadingScreen: segment-by-segment navigation contract."""

from __future__ import annotations

from auto_lorebook.tui.screens.reading import _split_segments

# ---------------------------------------------------------------------------
# _split_segments unit tests
# ---------------------------------------------------------------------------

_READING = """\
---
schema_version: 1
source_id: yt-abc
---

# Reading: Test

## [0:00-0:01] Alpha

Speaker: DM

- fact one

## [0:01-0:02] Beta

Speaker: mixed

_No claims extracted from this segment._

## [0:02-0:03] Gamma

Speaker: DM

- fact two
"""


def test_split_segments_count() -> None:
    segs = _split_segments(_READING)
    assert len(segs) == 3


def test_split_segments_first_starts_with_header() -> None:
    segs = _split_segments(_READING)
    assert segs[0].startswith("## [0:00-0:01] Alpha")


def test_split_segments_preserves_content() -> None:
    segs = _split_segments(_READING)
    assert "fact one" in segs[0]
    assert "_No claims extracted" in segs[1]
    assert "fact two" in segs[2]


def test_split_segments_empty_text() -> None:
    assert _split_segments("") == []


def test_split_segments_no_sections() -> None:
    """Preamble-only text (no ## headings) returns empty list."""
    assert _split_segments("# Reading: Foo\n\nsome text\n") == []
