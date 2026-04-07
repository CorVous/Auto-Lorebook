"""Chunk grouping for subtitle blocks.

Groups consecutive :class:`~auto_lorebook.parsers.srt.SubtitleBlock`
objects into larger :class:`Chunk` objects suitable for feeding into an
LLM context window.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auto_lorebook.parsers.srt import SubtitleBlock


@dataclass
class Chunk:
    """A contiguous group of subtitle blocks.

    :param blocks: The subtitle blocks that make up this chunk.
    :param text: All block texts joined by a single space.
    :param start: Start time of the first block in seconds.
    :param end: End time of the last block in seconds.
    """

    blocks: list[SubtitleBlock]
    text: str
    start: float
    end: float

    @property
    def block_count(self) -> int:
        """Return the number of subtitle blocks in this chunk."""
        return len(self.blocks)


def group_into_chunks(
    blocks: list[SubtitleBlock],
    max_blocks: int = 10,
) -> list[Chunk]:
    """Group consecutive subtitle blocks into fixed-size chunks.

    :param blocks: Subtitle blocks in sequence order.
    :param max_blocks: Maximum number of blocks per chunk (must be ≥ 1).
    :return: List of :class:`Chunk` objects covering all *blocks*.
    :raises ValueError: If *max_blocks* is less than 1.
    """
    if max_blocks < 1:
        msg = f"max_blocks must be >= 1, got {max_blocks}"
        raise ValueError(msg)

    if not blocks:
        return []

    chunks: list[Chunk] = []
    for i in range(0, len(blocks), max_blocks):
        group = blocks[i : i + max_blocks]
        chunks.append(
            Chunk(
                blocks=group,
                text=" ".join(b.text for b in group),
                start=group[0].start,
                end=group[-1].end,
            )
        )
    return chunks
