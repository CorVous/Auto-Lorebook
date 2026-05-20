"""Tests for transcript.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from auto_lorebook.corrections import Correction, Corrections
from auto_lorebook.info_yaml import Info, SourceContext
from auto_lorebook.srt import Cue
from auto_lorebook.transcript import (
    LoadedTranscript,
    TranscriptError,
    apply_corrections,
    load,
    transcript_window,
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
        loaded = load(tmp_wiki, info, Corrections())
        assert "Hello world." in loaded.text_for_llm
        assert loaded.total_duration == pytest.approx(600.0)

    def test_missing_transcript_raises(self, tmp_wiki: Path) -> None:
        src_dir = tmp_wiki / "sources" / "yt-missing"
        src_dir.mkdir(parents=True)
        info = _info("youtube", duration=120, source_id="yt-missing")
        with pytest.raises(TranscriptError):
            load(tmp_wiki, info, Corrections())


class TestCuesField:
    def test_srt_populates_cues(self, tmp_wiki: Path) -> None:
        src_dir = tmp_wiki / "sources" / "yt-x"
        src_dir.mkdir(parents=True)
        (src_dir / "transcript.en.srt").write_text(_SRT, encoding="utf-8")
        loaded = load(tmp_wiki, _info("youtube", duration=120), Corrections())
        assert loaded.cues is not None
        assert len(loaded.cues) == 2
        assert loaded.cues[0].start == pytest.approx(1.0)
        assert loaded.cues[0].text == "King Fair-on rules Aldara."

    def test_plain_text_cues_is_none(self, tmp_wiki: Path) -> None:
        src_dir = tmp_wiki / "sources" / "txt-x"
        src_dir.mkdir(parents=True)
        (src_dir / "transcript.txt").write_text("Hello.\n", encoding="utf-8")
        info = _info("text", duration=10, source_id="txt-x")
        loaded = load(tmp_wiki, info, Corrections())
        assert loaded.cues is None

    def test_corrections_apply_per_cue(self, tmp_wiki: Path) -> None:
        """Corrections apply per-cue so cue.text agrees with text_for_llm.

        If corrections desync the two views, substring lookup breaks.
        """
        src_dir = tmp_wiki / "sources" / "yt-x"
        src_dir.mkdir(parents=True)
        (src_dir / "transcript.en.srt").write_text(_SRT, encoding="utf-8")
        cors = Corrections(corrections=[Correction(wrong="Fair-on", right="Theron")])
        loaded = load(tmp_wiki, _info("youtube", duration=120), cors)
        assert loaded.cues is not None
        assert "Theron" in loaded.cues[0].text
        assert "Fair-on" not in loaded.cues[0].text

    def test_text_for_llm_matches_corrected_cue_text(self, tmp_wiki: Path) -> None:
        """text_for_llm and cues[*].text agree after corrections."""
        src_dir = tmp_wiki / "sources" / "yt-x"
        src_dir.mkdir(parents=True)
        (src_dir / "transcript.en.srt").write_text(_SRT, encoding="utf-8")
        cors = Corrections(corrections=[Correction(wrong="Fair-on", right="Theron")])
        loaded = load(tmp_wiki, _info("youtube", duration=120), cors)
        assert loaded.cues is not None
        for cue in loaded.cues:
            assert cue.text in loaded.text_for_llm


class TestTranscriptWindow:
    def _loaded(self, cues: list[Cue]) -> LoadedTranscript:
        rendered = "\n".join(f"[{c.start:.0f}] {c.text}" for c in cues)
        return LoadedTranscript(
            text_for_llm=rendered,
            total_duration=cues[-1].end if cues else 0.0,
            cues=tuple(cues),
        )

    def test_returns_window_and_cues(self) -> None:
        loaded = self._loaded([
            Cue(index=1, start=1.0, end=3.0, text="alpha"),
            Cue(index=2, start=10.0, end=14.0, text="beta"),
            Cue(index=3, start=20.0, end=22.0, text="gamma"),
        ])
        rendered, cues = transcript_window(loaded, 5.0, 15.0)
        assert "beta" in rendered
        assert "alpha" not in rendered
        assert "gamma" not in rendered
        assert [c.text for c in cues] == ["beta"]

    def test_window_is_half_open(self) -> None:
        """Cue starting exactly at `end` is excluded."""
        loaded = self._loaded([
            Cue(index=1, start=0.0, end=2.0, text="a"),
            Cue(index=2, start=5.0, end=7.0, text="b"),
        ])
        _, cues = transcript_window(loaded, 0.0, 5.0)
        assert [c.text for c in cues] == ["a"]

    def test_raises_on_plain_text(self) -> None:
        loaded = LoadedTranscript(text_for_llm="hello", total_duration=0.0, cues=None)
        with pytest.raises(TranscriptError):
            transcript_window(loaded, 0.0, 1.0)
