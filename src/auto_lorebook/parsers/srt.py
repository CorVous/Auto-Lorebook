"""SRT subtitle file parser."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

from auto_lorebook.models import SrtBlock, TranscriptChunk

if TYPE_CHECKING:
    from pathlib import Path

    from auto_lorebook.models import SourceMetadata

_TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)
_SINGLE_TIMESTAMP_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MIN_LINES_PER_BLOCK = 2


class SrtParseError(ValueError):
    """Raised when an SRT file cannot be parsed."""


def srt_timestamp_to_seconds(ts: str) -> float:
    """Convert an SRT timestamp string to total seconds.

    :param ts: Timestamp string in HH:MM:SS,mmm format.
    :return: Total seconds as a float.
    :raises SrtParseError: If the timestamp format is invalid.
    """
    m = _SINGLE_TIMESTAMP_RE.fullmatch(ts.strip())
    if m is None:
        msg = f"Invalid SRT timestamp: {ts!r}"
        raise SrtParseError(msg)
    hours, minutes, seconds, millis = (int(g) for g in m.groups())
    return hours * 3600.0 + minutes * 60.0 + seconds + millis / 1000.0


def _clean_text(raw: str) -> str:
    """Strip HTML tags and decode HTML entities from subtitle text.

    :param raw: Raw text from an SRT block.
    :return: Cleaned plain-text string.
    """
    stripped = _HTML_TAG_RE.sub("", raw)
    # Two passes to handle double-encoded entities like &amp;amp; → &amp; → &
    once = html.unescape(stripped)
    return html.unescape(once)


def parse_srt(content: str) -> list[SrtBlock]:
    """Parse SRT content string into a list of SrtBlocks.

    :param content: Raw SRT file content.
    :return: List of parsed SrtBlock instances.
    :raises SrtParseError: If a timestamp line cannot be parsed.
    """
    # Normalize: strip BOM, normalize line endings
    content = content.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")

    blocks: list[SrtBlock] = []
    segments = re.split(r"\n{2,}", content.strip())

    for segment in segments:
        segment = segment.strip()  # noqa: PLW2901
        if not segment:
            continue
        lines = segment.splitlines()
        if len(lines) < _MIN_LINES_PER_BLOCK:
            continue

        # Line 0: sequence index
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue  # skip malformed segment

        # Line 1: timestamps
        ts_line = lines[1].strip()
        m = _TIMESTAMP_RE.fullmatch(ts_line)
        if m is None:
            msg = f"Invalid SRT timestamp line: {ts_line!r}"
            raise SrtParseError(msg)
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in m.groups())
        start = h1 * 3600.0 + m1 * 60.0 + s1 + ms1 / 1000.0
        end = h2 * 3600.0 + m2 * 60.0 + s2 + ms2 / 1000.0

        # Remaining lines: text
        raw_text = " ".join(lines[2:])
        text = _clean_text(raw_text).strip()
        if not text:
            continue

        blocks.append(
            SrtBlock(index=index, start_seconds=start, end_seconds=end, text=text)
        )

    return blocks


def parse_srt_file(path: Path) -> list[SrtBlock]:
    """Read and parse an SRT file from disk.

    :param path: Path to the .srt file.
    :return: List of parsed SrtBlock instances.
    :raises SrtParseError: If the file content cannot be parsed.
    """
    return parse_srt(path.read_text(encoding="utf-8"))


def chunk_srt_blocks(
    blocks: list[SrtBlock],
    source: SourceMetadata,
    *,
    max_gap_seconds: float = 3.0,
) -> list[TranscriptChunk]:
    """Group SRT blocks into logical transcript chunks.

    Consecutive blocks whose gap is within ``max_gap_seconds`` are merged into
    one chunk. A large silence gap starts a new chunk.

    :param blocks: Parsed SRT blocks.
    :param source: Source metadata to attach to each chunk.
    :param max_gap_seconds: Max silence gap before starting a new chunk.
    :return: List of TranscriptChunk instances.
    """
    if not blocks:
        return []

    chunks: list[TranscriptChunk] = []
    group: list[SrtBlock] = [blocks[0]]

    for block in blocks[1:]:
        gap = block.start_seconds - group[-1].end_seconds
        if gap <= max_gap_seconds:
            group.append(block)
        else:
            chunks.append(_group_to_chunk(group, source))
            group = [block]

    chunks.append(_group_to_chunk(group, source))
    return chunks


def _group_to_chunk(group: list[SrtBlock], source: SourceMetadata) -> TranscriptChunk:
    """Convert a list of SRT blocks into a single TranscriptChunk.

    :param group: Non-empty list of consecutive SRT blocks.
    :param source: Source metadata for the chunk.
    :return: TranscriptChunk spanning the group's time range.
    """
    text = " ".join(b.text for b in group)
    return TranscriptChunk(
        text=text,
        source=source,
        start_seconds=group[0].start_seconds,
        end_seconds=group[-1].end_seconds,
    )
