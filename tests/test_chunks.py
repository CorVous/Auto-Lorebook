"""Tests for subtitle block chunk grouping."""

from __future__ import annotations

import pytest

from auto_lorebook.parsers.chunks import Chunk, group_into_chunks
from auto_lorebook.parsers.srt import SubtitleBlock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_block(
    seq: int, start: float = 0.0, end: float = 1.0, text: str = "x"
) -> SubtitleBlock:
    """Construct a SubtitleBlock with minimal boilerplate."""
    return SubtitleBlock(sequence=seq, start=start, end=end, text=text)


def make_blocks(n: int) -> list[SubtitleBlock]:
    """Create *n* sequential subtitle blocks, each 2 seconds long."""
    return [
        make_block(i + 1, start=i * 2.0, end=i * 2.0 + 1.0, text=f"Block {i + 1}")
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Chunk dataclass
# ---------------------------------------------------------------------------


class TestChunk:
    """Tests for the Chunk dataclass."""

    def test_creation(self) -> None:
        """Chunk stores blocks, text, start, and end."""
        blocks = make_blocks(2)
        chunk = Chunk(blocks=blocks, text="Block 1 Block 2", start=0.0, end=3.0)
        assert len(chunk.blocks) == 2
        assert chunk.text == "Block 1 Block 2"
        assert chunk.start == pytest.approx(0.0)
        assert chunk.end == pytest.approx(3.0)

    def test_block_count(self) -> None:
        """block_count property returns len(blocks)."""
        chunk = Chunk(blocks=make_blocks(3), text="", start=0.0, end=1.0)
        assert chunk.block_count == 3


# ---------------------------------------------------------------------------
# group_into_chunks
# ---------------------------------------------------------------------------


class TestGroupIntoChunks:
    """Tests for group_into_chunks."""

    def test_empty_input(self) -> None:
        """Empty block list returns empty chunk list."""
        assert group_into_chunks([]) == []

    def test_single_block(self) -> None:
        """A single block produces a single chunk."""
        blocks = make_blocks(1)
        chunks = group_into_chunks(blocks)
        assert len(chunks) == 1
        assert chunks[0].blocks == blocks

    def test_chunk_text_joins_blocks(self) -> None:
        """Chunk text is all block texts joined by a space."""
        blocks = [
            make_block(1, text="Hello"),
            make_block(2, text="world"),
        ]
        chunks = group_into_chunks(blocks, max_blocks=10)
        assert chunks[0].text == "Hello world"

    def test_chunk_start_is_first_block_start(self) -> None:
        """Chunk start time equals the first block's start time."""
        blocks = [make_block(1, start=5.0, end=7.0), make_block(2, start=7.0, end=9.0)]
        chunks = group_into_chunks(blocks, max_blocks=10)
        assert chunks[0].start == pytest.approx(5.0)

    def test_chunk_end_is_last_block_end(self) -> None:
        """Chunk end time equals the last block's end time."""
        blocks = [make_block(1, start=5.0, end=7.0), make_block(2, start=7.0, end=9.0)]
        chunks = group_into_chunks(blocks, max_blocks=10)
        assert chunks[0].end == pytest.approx(9.0)

    def test_exact_multiple(self) -> None:
        """10 blocks with max_blocks=5 yields exactly 2 chunks."""
        blocks = make_blocks(10)
        chunks = group_into_chunks(blocks, max_blocks=5)
        assert len(chunks) == 2

    def test_remainder(self) -> None:
        """11 blocks with max_blocks=5 yields 3 chunks (5+5+1)."""
        blocks = make_blocks(11)
        chunks = group_into_chunks(blocks, max_blocks=5)
        assert len(chunks) == 3
        assert chunks[-1].block_count == 1

    def test_default_max_blocks_is_10(self) -> None:
        """Default max_blocks is 10."""
        blocks = make_blocks(15)
        chunks = group_into_chunks(blocks)
        assert len(chunks) == 2  # 10 + 5

    def test_each_chunk_has_correct_blocks(self) -> None:
        """Blocks are assigned to chunks in order without overlap or gap."""
        blocks = make_blocks(7)
        chunks = group_into_chunks(blocks, max_blocks=3)
        assert chunks[0].blocks == blocks[0:3]
        assert chunks[1].blocks == blocks[3:6]
        assert chunks[2].blocks == blocks[6:7]

    def test_max_blocks_one(self) -> None:
        """max_blocks=1 produces one chunk per block."""
        blocks = make_blocks(4)
        chunks = group_into_chunks(blocks, max_blocks=1)
        assert len(chunks) == 4

    def test_invalid_max_blocks_raises(self) -> None:
        """max_blocks < 1 raises ValueError."""
        with pytest.raises(ValueError, match="max_blocks"):
            group_into_chunks(make_blocks(3), max_blocks=0)
