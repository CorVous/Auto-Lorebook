"""Tests for source_store.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from auto_lorebook.source_store import (
    CollisionError,
    DuplicateSourceError,
    copy_transcript,
)


def _write(path: Path, content: bytes = b"hello world") -> Path:
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# Happy-path copy
# ---------------------------------------------------------------------------


def test_copy_txt_creates_transcript_txt(tmp_path: Path, tmp_wiki: Path) -> None:
    src = _write(tmp_path / "notes.txt")
    dest, fname = copy_transcript(src, "txt-abc1234567", "text", tmp_wiki)
    assert fname == "transcript.txt"
    assert dest.exists()
    assert dest.read_bytes() == b"hello world"


def test_copy_srt_creates_transcript_en_srt(tmp_path: Path, tmp_wiki: Path) -> None:
    src = _write(tmp_path / "clip.srt")
    _, fname = copy_transcript(src, "srt-abc1234567", "srt", tmp_wiki)
    assert fname == "transcript.en.srt"


def test_copy_md_creates_transcript_md(tmp_path: Path, tmp_wiki: Path) -> None:
    src = _write(tmp_path / "notes.md")
    _, fname = copy_transcript(src, "txt-abc1234567", "markdown", tmp_wiki)
    assert fname == "transcript.md"


def test_copy_creates_source_dir(tmp_path: Path, tmp_wiki: Path) -> None:
    src = _write(tmp_path / "f.txt")
    copy_transcript(src, "txt-newid00001", "text", tmp_wiki)
    assert (tmp_wiki / "sources" / "txt-newid00001").is_dir()


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------


def test_same_content_raises_duplicate(tmp_path: Path, tmp_wiki: Path) -> None:
    src = _write(tmp_path / "f.txt")
    copy_transcript(src, "txt-abc1234567", "text", tmp_wiki)
    with pytest.raises(DuplicateSourceError, match="already ingested"):
        copy_transcript(src, "txt-abc1234567", "text", tmp_wiki)


def test_different_content_same_id_raises_collision(
    tmp_path: Path, tmp_wiki: Path
) -> None:
    src1 = _write(tmp_path / "a.txt", b"content A")
    src2 = _write(tmp_path / "b.txt", b"content B")
    copy_transcript(src1, "txt-sameid000", "text", tmp_wiki)
    with pytest.raises(CollisionError, match="differs"):
        copy_transcript(src2, "txt-sameid000", "text", tmp_wiki)


# ---------------------------------------------------------------------------
# Content fidelity
# ---------------------------------------------------------------------------


def test_stored_content_matches_source(tmp_path: Path, tmp_wiki: Path) -> None:
    data = b"line1\nline2\nline3\n"
    src = _write(tmp_path / "f.txt", data)
    dest, _ = copy_transcript(src, "txt-fidelity00", "text", tmp_wiki)
    assert dest.read_bytes() == data
