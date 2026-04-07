"""Core data models for the Auto-Lorebook pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceMetadata:
    """Metadata about the source of ingested lore content.

    :param filename: Original filename of the source.
    :param source_url: Optional URL (YouTube video or web page) for citations.
    """

    filename: str
    source_url: str | None = None


@dataclass
class SrtBlock:
    """A single subtitle block parsed from an SRT file.

    :param index: Sequential block number (1-based).
    :param start_seconds: Start time in seconds.
    :param end_seconds: End time in seconds.
    :param text: Dialogue/narration text, whitespace-stripped.
    """

    index: int
    start_seconds: float
    end_seconds: float
    text: str


@dataclass
class TranscriptChunk:
    """A logical chunk of transcript text, with optional timestamps.

    Timestamps are present for SRT-sourced chunks; None for plain text.

    :param text: The chunk's lore text.
    :param source: Metadata about where this chunk came from.
    :param start_seconds: Optional start timestamp (SRT only).
    :param end_seconds: Optional end timestamp (SRT only).
    """

    text: str
    source: SourceMetadata
    start_seconds: float | None = None
    end_seconds: float | None = None


@dataclass
class WikiExcerpt:
    """A relevant excerpt from an existing wiki page.

    :param entity_name: Name of the wiki entity (e.g. "Aldara").
    :param category: Entity category (e.g. "locations", "characters").
    :param content: The relevant excerpt text from the wiki page.
    """

    entity_name: str
    category: str
    content: str


@dataclass
class SectionMapping:
    """Maps a transcript chunk to relevant existing wiki excerpts.

    :param chunk: The transcript chunk being mapped.
    :param relevant_wiki_excerpts: Wiki excerpts relevant to this chunk.
    """

    chunk: TranscriptChunk
    relevant_wiki_excerpts: list[WikiExcerpt] = field(default_factory=list)


@dataclass
class PreprocessorOutput:
    """Output from the Stage 1 pre-processor LLM call.

    :param section_mappings: Each chunk mapped to relevant wiki excerpts.
    :param new_entity_mentions: Entity names not found in the existing wiki.
    """

    section_mappings: list[SectionMapping]
    new_entity_mentions: list[str]
