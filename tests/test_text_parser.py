"""Tests for the plain text / markdown parser."""

from pathlib import Path

from auto_lorebook.models import SourceMetadata
from auto_lorebook.parsers.text import parse_text, parse_text_file


def _source(filename: str = "notes.txt") -> SourceMetadata:
    return SourceMetadata(filename=filename)


def test_parse_text_empty_string() -> None:
    """Empty content returns an empty list."""
    assert parse_text("", _source()) == []


def test_parse_text_single_paragraph() -> None:
    """A single paragraph becomes a single chunk."""
    chunks = parse_text("The kingdom was founded long ago.", _source())
    assert len(chunks) == 1
    assert chunks[0].text == "The kingdom was founded long ago."


def test_parse_text_multiple_paragraphs() -> None:
    """Double newlines split text into multiple chunks."""
    content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = parse_text(content, _source())
    assert len(chunks) == 3
    assert chunks[0].text == "First paragraph."
    assert chunks[1].text == "Second paragraph."
    assert chunks[2].text == "Third paragraph."


def test_parse_text_no_timestamps() -> None:
    """Plain text chunks have no timestamps."""
    chunks = parse_text("Some lore.", _source())
    assert chunks[0].start_seconds is None
    assert chunks[0].end_seconds is None


def test_parse_text_source_propagated() -> None:
    """Source metadata is attached to every chunk."""
    source = SourceMetadata(filename="lore.md", source_url="https://example.com/lore")
    chunks = parse_text("Paragraph one.\n\nParagraph two.", source)
    assert all(c.source is source for c in chunks)


def test_parse_text_strips_surrounding_whitespace() -> None:
    """Leading/trailing whitespace on each paragraph is stripped."""
    content = "  Hello world.  \n\n  Second.  "
    chunks = parse_text(content, _source())
    assert chunks[0].text == "Hello world."
    assert chunks[1].text == "Second."


def test_parse_text_ignores_blank_only_paragraphs() -> None:
    """Paragraphs that are blank after stripping are skipped."""
    content = "First.\n\n   \n\nSecond."
    chunks = parse_text(content, _source())
    assert len(chunks) == 2


def test_parse_text_single_newline_not_split() -> None:
    """Single newlines within a paragraph are preserved as spaces."""
    content = "Line one.\nLine two."
    chunks = parse_text(content, _source())
    assert len(chunks) == 1
    assert "Line one." in chunks[0].text
    assert "Line two." in chunks[0].text


def test_parse_text_file_reads_utf8(tmp_path: Path) -> None:
    """parse_text_file reads a UTF-8 text file from disk."""
    text_file = tmp_path / "notes.txt"
    text_file.write_text("Magic exists here.\n\nDragons are real.", encoding="utf-8")
    source = SourceMetadata(filename="notes.txt")
    chunks = parse_text_file(text_file, source)
    assert len(chunks) == 2
    assert chunks[0].text == "Magic exists here."
    assert chunks[1].text == "Dragons are real."
