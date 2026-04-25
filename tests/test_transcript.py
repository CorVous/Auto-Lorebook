"""Tests for transcript.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from auto_lorebook.corrections import Correction, Corrections
from auto_lorebook.info_yaml import Info, SourceContext
from auto_lorebook.transcript import (
    TranscriptError,
    apply_corrections,
    load,
)

if TYPE_CHECKING:
    from pathlib import Path


_SRT = (
    "1\n"
    "00:00:01,000 --> 00:00:03,000\n"
    "King Fair-on rules Aldara.\n"
    "\n"
    "2\n"
    "00:01:00,000 --> 00:01:04,500\n"
    "His son is heir.\n"
)


def _info(
    source_type: str, duration: int | None = None, source_id: str = "yt-x"
) -> Info:
    return Info(
        source_id=source_id,
        source_type=source_type,
        fetched_at="2026-04-20T00:00:00Z",
        title="t",
        duration_seconds=duration,
        transcript_filename="transcript.en.srt"
        if source_type in {"srt", "youtube"}
        else "transcript.txt",
        context=SourceContext(),
    )


class TestApplyCorrections:
    def test_empty_corrections_returns_unchanged(self) -> None:
        assert apply_corrections("hello", Corrections()) == "hello"

    def test_single_correction(self) -> None:
        cors = Corrections(corrections=[Correction(wrong="Fair-on", right="Theron")])
        assert apply_corrections("King Fair-on", cors) == "King Theron"

    def test_multiple_occurrences(self) -> None:
        cors = Corrections(corrections=[Correction(wrong="x", right="y")])
        assert apply_corrections("x and x and x", cors) == "y and y and y"

    def test_order_independent(self) -> None:
        cors = Corrections(
            corrections=[
                Correction(wrong="a", right="b"),
                Correction(wrong="c", right="d"),
            ]
        )
        assert apply_corrections("a c a c", cors) == "b d b d"


class TestLoad:
    def test_srt(self, tmp_wiki: Path) -> None:
        src_dir = tmp_wiki / "sources" / "yt-x"
        src_dir.mkdir(parents=True)
        (src_dir / "transcript.en.srt").write_text(_SRT, encoding="utf-8")
        info = _info("youtube", duration=120)
        loaded = load(tmp_wiki, info, Corrections())
        # flattened with canonical timestamps
        assert "[0:00:01]" in loaded.text_for_llm
        assert "King Fair-on rules Aldara." in loaded.text_for_llm
        assert "[0:01:00]" in loaded.text_for_llm
        assert loaded.total_duration == pytest.approx(120.0)

    def test_srt_duration_falls_back_to_last_cue_end(self, tmp_wiki: Path) -> None:
        src_dir = tmp_wiki / "sources" / "yt-x"
        src_dir.mkdir(parents=True)
        (src_dir / "transcript.en.srt").write_text(_SRT, encoding="utf-8")
        info = _info("youtube", duration=None)
        loaded = load(tmp_wiki, info, Corrections())
        assert loaded.total_duration == pytest.approx(64.5)

    def test_srt_applies_corrections(self, tmp_wiki: Path) -> None:
        src_dir = tmp_wiki / "sources" / "yt-x"
        src_dir.mkdir(parents=True)
        (src_dir / "transcript.en.srt").write_text(_SRT, encoding="utf-8")
        info = _info("youtube", duration=120)
        cors = Corrections(corrections=[Correction(wrong="Fair-on", right="Theron")])
        loaded = load(tmp_wiki, info, cors)
        assert "Theron" in loaded.text_for_llm
        assert "Fair-on" not in loaded.text_for_llm

    def test_text_source(self, tmp_wiki: Path) -> None:
        src_dir = tmp_wiki / "sources" / "txt-x"
        src_dir.mkdir(parents=True)
        (src_dir / "transcript.txt").write_text(
            "Hello world.\nAnother line.\n", encoding="utf-8"
        )
        info = _info("text", duration=600, source_id="txt-x")
        info.transcript_filename = "transcript.txt"
        loaded = load(tmp_wiki, info, Corrections())
        assert "Hello world." in loaded.text_for_llm
        assert loaded.total_duration == pytest.approx(600.0)

    def test_missing_transcript_raises(self, tmp_wiki: Path) -> None:
        src_dir = tmp_wiki / "sources" / "yt-missing"
        src_dir.mkdir(parents=True)
        info = _info("youtube", duration=120, source_id="yt-missing")
        with pytest.raises(TranscriptError):
            load(tmp_wiki, info, Corrections())
