"""Tests for the SRT subtitle file parser."""

from __future__ import annotations

import textwrap

import pytest

from auto_lorebook.parsers.srt import (
    ParsedSRT,
    SubtitleBlock,
    parse_srt,
    parse_timestamp,
    seconds_to_timestamp,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_SRT = textwrap.dedent("""\
    1
    00:00:01,000 --> 00:00:04,000
    Hello, world!

    2
    00:00:05,500 --> 00:00:08,750
    This is a second subtitle.

    3
    00:00:10,000 --> 00:00:13,000
    And a third one.
""")

MULTILINE_SRT = textwrap.dedent("""\
    1
    00:00:01,000 --> 00:00:04,000
    Line one of subtitle.
    Line two of subtitle.

    2
    00:00:05,000 --> 00:00:08,000
    Single line here.
""")

WINDOWS_CRLF_SRT = (
    "1\r\n"
    "00:00:01,000 --> 00:00:04,000\r\n"
    "Hello CRLF!\r\n"
    "\r\n"
    "2\r\n"
    "00:00:05,000 --> 00:00:08,000\r\n"
    "Second block.\r\n"
    "\r\n"
)

EMPTY_SRT = ""

SINGLE_BLOCK_SRT = textwrap.dedent("""\
    1
    00:00:00,000 --> 00:00:02,500
    Only one block.
""")


# ---------------------------------------------------------------------------
# parse_timestamp
# ---------------------------------------------------------------------------


class TestParseTimestamp:
    """Tests for parse_timestamp helper."""

    def test_basic_timestamp(self) -> None:
        """Parse a standard HH:MM:SS,mmm SRT timestamp to seconds."""
        assert parse_timestamp("00:00:01,000") == pytest.approx(1.0)

    def test_minutes(self) -> None:
        """Minutes are correctly converted."""
        assert parse_timestamp("00:01:00,000") == pytest.approx(60.0)

    def test_hours(self) -> None:
        """Hours are correctly converted."""
        assert parse_timestamp("01:00:00,000") == pytest.approx(3600.0)

    def test_milliseconds(self) -> None:
        """Milliseconds are included in the result."""
        assert parse_timestamp("00:00:00,500") == pytest.approx(0.5)

    def test_combined(self) -> None:
        """Combined hours, minutes, seconds, milliseconds."""
        # 1h + 2m + 3s + 456ms
        expected = 3600 + 120 + 3 + 0.456
        assert parse_timestamp("01:02:03,456") == pytest.approx(expected)

    def test_invalid_format_raises(self) -> None:
        """Invalid timestamp string raises ValueError."""
        with pytest.raises(ValueError, match="timestamp"):
            parse_timestamp("not-a-timestamp")


# ---------------------------------------------------------------------------
# seconds_to_timestamp
# ---------------------------------------------------------------------------


class TestSecondsToTimestamp:
    """Tests for seconds_to_timestamp helper."""

    def test_zero(self) -> None:
        """Zero seconds converts to start timestamp."""
        assert seconds_to_timestamp(0.0) == "00:00:00,000"

    def test_one_second(self) -> None:
        """One second converts correctly."""
        assert seconds_to_timestamp(1.0) == "00:00:01,000"

    def test_minutes(self) -> None:
        """Minutes convert correctly."""
        assert seconds_to_timestamp(60.0) == "00:01:00,000"

    def test_hours(self) -> None:
        """Hours convert correctly."""
        assert seconds_to_timestamp(3600.0) == "01:00:00,000"

    def test_milliseconds(self) -> None:
        """Milliseconds round-trip correctly."""
        assert seconds_to_timestamp(0.5) == "00:00:00,500"

    def test_round_trip(self) -> None:
        """parse_timestamp and seconds_to_timestamp are inverses."""
        original = "01:02:03,456"
        assert seconds_to_timestamp(parse_timestamp(original)) == original


# ---------------------------------------------------------------------------
# SubtitleBlock
# ---------------------------------------------------------------------------


class TestSubtitleBlock:
    """Tests for the SubtitleBlock dataclass."""

    def test_creation(self) -> None:
        """SubtitleBlock stores its fields."""
        block = SubtitleBlock(sequence=1, start=1.0, end=4.0, text="Hello!")
        assert block.sequence == 1
        assert block.start == pytest.approx(1.0)
        assert block.end == pytest.approx(4.0)
        assert block.text == "Hello!"

    def test_duration(self) -> None:
        """duration property returns end - start."""
        block = SubtitleBlock(sequence=1, start=1.0, end=4.0, text="Hi")
        assert block.duration == pytest.approx(3.0)

    def test_equality(self) -> None:
        """Two blocks with same data are equal."""
        a = SubtitleBlock(sequence=1, start=0.0, end=1.0, text="X")
        b = SubtitleBlock(sequence=1, start=0.0, end=1.0, text="X")
        assert a == b


# ---------------------------------------------------------------------------
# parse_srt
# ---------------------------------------------------------------------------


class TestParseSRT:
    """Tests for parse_srt."""

    def test_returns_parsed_srt(self) -> None:
        """parse_srt returns a ParsedSRT instance."""
        result = parse_srt(SIMPLE_SRT)
        assert isinstance(result, ParsedSRT)

    def test_block_count(self) -> None:
        """All three blocks are parsed."""
        result = parse_srt(SIMPLE_SRT)
        assert len(result.blocks) == 3

    def test_first_block_sequence(self) -> None:
        """First block has correct sequence number."""
        result = parse_srt(SIMPLE_SRT)
        assert result.blocks[0].sequence == 1

    def test_first_block_start(self) -> None:
        """First block has correct start time."""
        result = parse_srt(SIMPLE_SRT)
        assert result.blocks[0].start == pytest.approx(1.0)

    def test_first_block_end(self) -> None:
        """First block has correct end time."""
        result = parse_srt(SIMPLE_SRT)
        assert result.blocks[0].end == pytest.approx(4.0)

    def test_first_block_text(self) -> None:
        """First block has correct text."""
        result = parse_srt(SIMPLE_SRT)
        assert result.blocks[0].text == "Hello, world!"

    def test_second_block_start(self) -> None:
        """Second block start time includes milliseconds."""
        result = parse_srt(SIMPLE_SRT)
        assert result.blocks[1].start == pytest.approx(5.5)

    def test_second_block_end(self) -> None:
        """Second block end time includes milliseconds."""
        result = parse_srt(SIMPLE_SRT)
        assert result.blocks[1].end == pytest.approx(8.75)

    def test_multiline_text_joined(self) -> None:
        """Multi-line subtitle text is joined with a space."""
        result = parse_srt(MULTILINE_SRT)
        assert result.blocks[0].text == "Line one of subtitle. Line two of subtitle."

    def test_crlf_line_endings(self) -> None:
        """Windows CRLF line endings are handled correctly."""
        result = parse_srt(WINDOWS_CRLF_SRT)
        assert len(result.blocks) == 2
        assert result.blocks[0].text == "Hello CRLF!"

    def test_empty_srt(self) -> None:
        """Empty SRT content yields zero blocks."""
        result = parse_srt(EMPTY_SRT)
        assert result.blocks == []

    def test_single_block(self) -> None:
        """Single-block SRT parses correctly."""
        result = parse_srt(SINGLE_BLOCK_SRT)
        assert len(result.blocks) == 1
        assert result.blocks[0].text == "Only one block."
        assert result.blocks[0].start == pytest.approx(0.0)
        assert result.blocks[0].end == pytest.approx(2.5)

    def test_blocks_in_order(self) -> None:
        """Blocks are returned in sequence order."""
        result = parse_srt(SIMPLE_SRT)
        sequences = [b.sequence for b in result.blocks]
        assert sequences == sorted(sequences)

    def test_trailing_whitespace_stripped(self) -> None:
        """Leading/trailing whitespace in text is stripped."""
        srt = "1\n00:00:00,000 --> 00:00:02,000\n  spaced  \n\n"
        result = parse_srt(srt)
        assert result.blocks[0].text == "spaced"
