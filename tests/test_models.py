"""Tests for core data models."""

import pytest

from auto_lorebook.models import (
    PreprocessorOutput,
    SectionMapping,
    SourceMetadata,
    SrtBlock,
    TranscriptChunk,
    WikiExcerpt,
)


def test_source_metadata_required_fields() -> None:
    """SourceMetadata requires filename; source_url defaults to None."""
    source = SourceMetadata(filename="notes.txt")
    assert source.filename == "notes.txt"
    assert source.source_url is None


def test_source_metadata_with_url() -> None:
    """SourceMetadata accepts an optional source_url."""
    source = SourceMetadata(
        filename="subs.en.srt",
        source_url="https://youtube.com/watch?v=abc123",
    )
    assert source.source_url == "https://youtube.com/watch?v=abc123"


def test_srt_block_fields() -> None:
    """SrtBlock stores index, start/end seconds, and text."""
    block = SrtBlock(index=1, start_seconds=0.0, end_seconds=1.5, text="Hello world")
    assert block.index == 1
    assert block.start_seconds == pytest.approx(0.0)
    assert block.end_seconds == pytest.approx(1.5)
    assert block.text == "Hello world"


def test_transcript_chunk_minimal() -> None:
    """TranscriptChunk requires text and source; timestamps default to None."""
    source = SourceMetadata(filename="notes.txt")
    chunk = TranscriptChunk(text="Some lore text.", source=source)
    assert chunk.text == "Some lore text."
    assert chunk.source is source
    assert chunk.start_seconds is None
    assert chunk.end_seconds is None


def test_transcript_chunk_with_timestamps() -> None:
    """TranscriptChunk accepts optional timestamps."""
    source = SourceMetadata(filename="subs.srt")
    chunk = TranscriptChunk(
        text="The kingdom fell.",
        source=source,
        start_seconds=10.0,
        end_seconds=12.5,
    )
    assert chunk.start_seconds == pytest.approx(10.0)
    assert chunk.end_seconds == pytest.approx(12.5)


def test_wiki_excerpt_fields() -> None:
    """WikiExcerpt stores entity_name, category, and content."""
    excerpt = WikiExcerpt(
        entity_name="Aldara",
        category="locations",
        content="Aldara is a city in the north.",
    )
    assert excerpt.entity_name == "Aldara"
    assert excerpt.category == "locations"
    assert excerpt.content == "Aldara is a city in the north."


def test_section_mapping_fields() -> None:
    """SectionMapping stores chunk and list of wiki excerpts."""
    source = SourceMetadata(filename="notes.txt")
    chunk = TranscriptChunk(text="The city of Aldara burned.", source=source)
    excerpt = WikiExcerpt(
        entity_name="Aldara", category="locations", content="Aldara is a city."
    )
    mapping = SectionMapping(chunk=chunk, relevant_wiki_excerpts=[excerpt])
    assert mapping.chunk is chunk
    assert len(mapping.relevant_wiki_excerpts) == 1
    assert mapping.relevant_wiki_excerpts[0] is excerpt


def test_section_mapping_empty_excerpts() -> None:
    """SectionMapping allows empty wiki excerpt list."""
    source = SourceMetadata(filename="notes.txt")
    chunk = TranscriptChunk(text="An unknown entity appeared.", source=source)
    mapping = SectionMapping(chunk=chunk, relevant_wiki_excerpts=[])
    assert mapping.relevant_wiki_excerpts == []


def test_preprocessor_output_fields() -> None:
    """PreprocessorOutput stores section_mappings and new_entity_mentions."""
    source = SourceMetadata(filename="subs.srt")
    chunk = TranscriptChunk(text="The Dragon Vex attacked.", source=source)
    mapping = SectionMapping(chunk=chunk, relevant_wiki_excerpts=[])
    output = PreprocessorOutput(
        section_mappings=[mapping],
        new_entity_mentions=["Dragon Vex"],
    )
    assert len(output.section_mappings) == 1
    assert output.new_entity_mentions == ["Dragon Vex"]


def test_preprocessor_output_empty() -> None:
    """PreprocessorOutput can be constructed with empty lists."""
    output = PreprocessorOutput(section_mappings=[], new_entity_mentions=[])
    assert output.section_mappings == []
    assert output.new_entity_mentions == []
