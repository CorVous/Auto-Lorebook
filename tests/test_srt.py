"""Tests for srt.py parser."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from auto_lorebook.srt import Cue, SrtError, parse, parse_file

if TYPE_CHECKING:
    from pathlib import Path


SIMPLE = (
    "1\n"
    "00:00:01,000 --> 00:00:05,000\n"
    "First cue.\n"
    "\n"
    "2\n"
    "00:00:06,500 --> 00:00:09,000\n"
    "Second cue\n"
    "across two lines.\n"
)


class TestParse:
    def test_simple(self) -> None:
        cues = parse(SIMPLE)
        assert cues == [
            Cue(index=1, start=1.0, end=5.0, text="First cue."),
            Cue(
                index=2,
                start=6.5,
                end=9.0,
                text="Second cue\nacross two lines.",
            ),
        ]

    def test_crlf(self) -> None:
        cues = parse(SIMPLE.replace("\n", "\r\n"))
        assert len(cues) == 2
        assert cues[0].text == "First cue."

    def test_bom_stripped(self) -> None:
        cues = parse("﻿" + SIMPLE)
        assert len(cues) == 2

    def test_empty_returns_empty(self) -> None:
        assert parse("") == []

    def test_whitespace_only_returns_empty(self) -> None:
        assert parse("   \n\n\n") == []

    def test_extra_blank_lines_between_cues(self) -> None:
        text = (
            "1\n"
            "00:00:01,000 --> 00:00:02,000\n"
            "A\n"
            "\n\n\n"
            "2\n"
            "00:00:03,000 --> 00:00:04,000\n"
            "B\n"
        )
        cues = parse(text)
        assert [c.text for c in cues] == ["A", "B"]

    def test_missing_arrow_raises(self) -> None:
        text = "1\n00:00:01,000 00:00:02,000\ntext\n"
        with pytest.raises(SrtError):
            parse(text)

    def test_non_integer_index_tolerated(self) -> None:
        # some SRTs have garbage indices; we keep the cue and assign sequence
        text = (
            "X\n"
            "00:00:01,000 --> 00:00:02,000\n"
            "A\n"
            "\n"
            "Y\n"
            "00:00:03,000 --> 00:00:04,000\n"
            "B\n"
        )
        cues = parse(text)
        assert [c.index for c in cues] == [1, 2]
        assert [c.text for c in cues] == ["A", "B"]

    def test_bad_timestamp_raises(self) -> None:
        text = "1\nnot-a-time --> 00:00:02,000\ntext\n"
        with pytest.raises(SrtError):
            parse(text)

    def test_end_before_start_raises(self) -> None:
        text = "1\n00:00:05,000 --> 00:00:02,000\ntext\n"
        with pytest.raises(SrtError):
            parse(text)


class TestParseFile:
    def test_reads_file(self, tmp_path: Path) -> None:
        path = tmp_path / "sample.srt"
        path.write_text(SIMPLE, encoding="utf-8")
        cues = parse_file(path)
        assert len(cues) == 2

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_file(tmp_path / "nope.srt")
