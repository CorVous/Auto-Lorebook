"""Plain text and markdown parser."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from auto_lorebook.models import SourceMetadata, TranscriptChunk

if TYPE_CHECKING:
    from pathlib import Path


def parse_text(content: str, source: SourceMetadata) -> list[TranscriptChunk]:
    """Parse plain-text or markdown content into transcript chunks.

    Splits on blank lines (double newlines). Single newlines within a paragraph
    are kept as spaces. Empty paragraphs are skipped.

    :param content: Raw text content.
    :param source: Source metadata to attach to each chunk.
    :return: List of TranscriptChunk instances (no timestamps).
    """
    if not content.strip():
        return []

    raw_paragraphs = re.split(r"\n{2,}", content)
    chunks: list[TranscriptChunk] = []

    for raw in raw_paragraphs:
        # Join internal single newlines with a space, then strip
        text = " ".join(raw.splitlines()).strip()
        if not text:
            continue
        chunks.append(TranscriptChunk(text=text, source=source))

    return chunks


def parse_text_file(path: Path, source: SourceMetadata) -> list[TranscriptChunk]:
    """Read and parse a plain-text or markdown file from disk.

    :param path: Path to the text file.
    :param source: Source metadata to attach to each chunk.
    :return: List of TranscriptChunk instances.
    """
    return parse_text(path.read_text(encoding="utf-8"), source)
