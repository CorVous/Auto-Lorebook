"""Tests for the SRT subtitle file parser."""

import textwrap
from pathlib import Path

import pytest

from auto_lorebook.models import SourceMetadata, SrtBlock
from auto_lorebook.parsers.srt import (
    SrtParseError,
    chunk_srt_blocks,
    parse_srt,
    parse_srt_file,
    srt_timestamp_to_seconds,
)

SIMPLE_SRT = textwrap.dedent("""\
    1
    00:00:00,000 --> 00:00:01,500
    Hello world

    2
    00:00:02,000 --> 00:00:03,500
    The kingdom of Aldara was founded long ago.
""")

MULTILINE_SRT = textwrap.dedent("""\
    1
    00:00:00,000 --> 00:00:02,000
    The dragon flew
    over the mountains.
""")

HTML_TAGS_SRT = textwrap.dedent("""\
    1
    00:00:00,000 --> 00:00:02,000
    <i>The narrator spoke</i> about <b>the kingdom</b>.
""")

HTML_ENTITIES_SRT = textwrap.dedent("""\
    1
    00:00:00,000 --> 00:00:02,000
    It&#39;s a &amp; b &amp;amp; c.
""")

WINDOWS_ENDINGS_SRT = "1\r\n00:00:00,000 --> 00:00:01,000\r\nHello.\r\n\r\n"

BOM_SRT = "\ufeff" + SIMPLE_SRT

MALFORMED_TIMESTAMP_SRT = textwrap.dedent("""\
    1
    00:00:00.000 -> 00:00:01.500
    Bad timestamp format.
""")


# --- srt_timestamp_to_seconds ---


def test_timestamp_to_seconds_basic() -> None:
    """Convert HH:MM:SS,mmm to total seconds."""
    assert srt_timestamp_to_seconds("00:00:01,000") == pytest.approx(1.0)


def test_timestamp_to_seconds_minutes() -> None:
    """Handles minutes correctly."""
    assert srt_timestamp_to_seconds("00:01:00,000") == pytest.approx(60.0)


def test_timestamp_to_seconds_hours() -> None:
    """Handles hours correctly."""
    assert srt_timestamp_to_seconds("01:00:00,000") == pytest.approx(3600.0)


def test_timestamp_to_seconds_fractional() -> None:
    """Milliseconds contribute fractional seconds."""
    assert srt_timestamp_to_seconds("00:04:32,500") == pytest.approx(272.5)


def test_timestamp_to_seconds_youtube_example() -> None:
    """Matches YouTube &t= link format: 4m32s = 272s."""
    assert srt_timestamp_to_seconds("00:04:32,000") == pytest.approx(272.0)


# --- parse_srt ---


def test_parse_srt_empty_string() -> None:
    """Empty string returns empty list."""
    assert parse_srt("") == []


def test_parse_srt_single_block() -> None:
    """Parses a single SRT block correctly."""
    blocks = parse_srt(SIMPLE_SRT)
    assert len(blocks) == 2
    b = blocks[0]
    assert b.index == 1
    assert b.start_seconds == pytest.approx(0.0)
    assert b.end_seconds == pytest.approx(1.5)
    assert b.text == "Hello world"


def test_parse_srt_multiple_blocks() -> None:
    """Parses all blocks and preserves order."""
    blocks = parse_srt(SIMPLE_SRT)
    assert len(blocks) == 2
    assert blocks[1].index == 2
    assert blocks[1].text == "The kingdom of Aldara was founded long ago."


def test_parse_srt_multiline_text() -> None:
    """Multi-line dialogue is joined with a space."""
    blocks = parse_srt(MULTILINE_SRT)
    assert len(blocks) == 1
    assert blocks[0].text == "The dragon flew over the mountains."


def test_parse_srt_strips_html_tags() -> None:
    """HTML tags like <i> and <b> are removed from text."""
    blocks = parse_srt(HTML_TAGS_SRT)
    assert blocks[0].text == "The narrator spoke about the kingdom."


def test_parse_srt_html_entities() -> None:
    """HTML entities like &#39; and &amp; are decoded."""
    blocks = parse_srt(HTML_ENTITIES_SRT)
    assert blocks[0].text == "It's a & b & c."


def test_parse_srt_windows_line_endings() -> None:
    """CRLF line endings are normalized before parsing."""
    blocks = parse_srt(WINDOWS_ENDINGS_SRT)
    assert len(blocks) == 1
    assert blocks[0].text == "Hello."


def test_parse_srt_bom() -> None:
    """BOM character at file start is stripped."""
    blocks = parse_srt(BOM_SRT)
    assert len(blocks) == 2
    assert blocks[0].index == 1


def test_parse_srt_malformed_timestamp() -> None:
    """Malformed timestamp raises SrtParseError."""
    with pytest.raises(SrtParseError):
        parse_srt(MALFORMED_TIMESTAMP_SRT)


# --- parse_srt_file ---


def test_parse_srt_file_reads_utf8(tmp_path: Path) -> None:
    """parse_srt_file reads a UTF-8 .srt file from disk."""
    srt_file = tmp_path / "test.srt"
    srt_file.write_text(SIMPLE_SRT, encoding="utf-8")
    blocks = parse_srt_file(srt_file)
    assert len(blocks) == 2
    assert blocks[0].text == "Hello world"


# --- chunk_srt_blocks ---


def test_chunk_srt_blocks_empty() -> None:
    """Empty block list returns empty chunk list."""
    source = SourceMetadata(filename="test.srt")
    assert chunk_srt_blocks([], source) == []


def test_chunk_srt_blocks_small_gap_merged() -> None:
    """Blocks with a small gap are kept in one chunk."""
    source = SourceMetadata(filename="test.srt")
    blocks = [
        SrtBlock(index=1, start_seconds=0.0, end_seconds=1.0, text="First."),
        SrtBlock(index=2, start_seconds=1.5, end_seconds=2.5, text="Second."),
    ]
    chunks = chunk_srt_blocks(blocks, source, max_gap_seconds=3.0)
    assert len(chunks) == 1
    assert "First." in chunks[0].text
    assert "Second." in chunks[0].text


def test_chunk_srt_blocks_large_gap_splits() -> None:
    """Blocks with a large gap produce separate chunks."""
    source = SourceMetadata(filename="test.srt")
    blocks = [
        SrtBlock(index=1, start_seconds=0.0, end_seconds=1.0, text="First."),
        SrtBlock(index=2, start_seconds=20.0, end_seconds=21.0, text="Second."),
    ]
    chunks = chunk_srt_blocks(blocks, source, max_gap_seconds=3.0)
    assert len(chunks) == 2
    assert chunks[0].text == "First."
    assert chunks[1].text == "Second."


def test_chunk_srt_blocks_timestamps() -> None:
    """Chunk timestamps come from the first and last block in each group."""
    source = SourceMetadata(filename="test.srt")
    blocks = [
        SrtBlock(index=1, start_seconds=5.0, end_seconds=6.0, text="A."),
        SrtBlock(index=2, start_seconds=6.5, end_seconds=7.5, text="B."),
    ]
    chunks = chunk_srt_blocks(blocks, source, max_gap_seconds=3.0)
    assert len(chunks) == 1
    assert chunks[0].start_seconds == pytest.approx(5.0)
    assert chunks[0].end_seconds == pytest.approx(7.5)


def test_chunk_srt_blocks_source_propagated() -> None:
    """Source metadata is propagated to each chunk."""
    source = SourceMetadata(
        filename="vid.srt", source_url="https://youtube.com/watch?v=abc"
    )
    blocks = [SrtBlock(index=1, start_seconds=0.0, end_seconds=1.0, text="Hi.")]
    chunks = chunk_srt_blocks(blocks, source)
    assert chunks[0].source is source
