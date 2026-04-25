"""Tests for gap_check.py."""

from __future__ import annotations

from auto_lorebook.gap_check import DEFAULT_LOW_YIELD_PATTERNS, check
from auto_lorebook.structure import Segment, Structure


def _struct(segments: list[Segment]) -> Structure:
    return Structure(
        source_id="yt-x",
        generated_at="2026-04-20T00:00:00Z",
        default_speaker="DM",
        segments=segments,
    )


class TestCheck:
    def test_no_low_yield_segments(self) -> None:
        s = _struct([
            Segment(id="seg-001", start=0, end=600, title="Founding", speaker="DM"),
        ])
        assert check(s, threshold_seconds=300) == []

    def test_single_short_low_yield_below_threshold(self) -> None:
        s = _struct([
            Segment(id="seg-001", start=0, end=120, title="Break", speaker="DM"),
            Segment(id="seg-002", start=120, end=1200, title="Founding", speaker="DM"),
        ])
        assert check(s, threshold_seconds=300) == []

    def test_long_low_yield_stretch_warns(self) -> None:
        s = _struct([
            Segment(
                id="seg-001", start=0, end=600, title="Rules discussion", speaker="DM"
            ),
            Segment(id="seg-002", start=600, end=1200, title="Break", speaker="DM"),
        ])
        warnings = check(s, threshold_seconds=300)
        assert len(warnings) == 1
        w = warnings[0]
        assert w.start == 0
        assert w.end == 1200
        assert len(w.segment_ids) == 2

    def test_notes_signal_low_yield(self) -> None:
        s = _struct([
            Segment(
                id="seg-001",
                start=0,
                end=500,
                title="Tangent",
                speaker="DM",
                notes="off-topic pizza order",
            ),
            Segment(
                id="seg-002",
                start=500,
                end=1100,
                title="Aside",
                speaker="DM",
                notes="break",
            ),
            Segment(
                id="seg-003",
                start=1100,
                end=2000,
                title="Founding",
                speaker="DM",
            ),
        ])
        warnings = check(s, threshold_seconds=300)
        assert len(warnings) == 1
        assert warnings[0].end == 1100

    def test_split_by_claim_bearing_segment(self) -> None:
        # two separate low-yield stretches split by a real segment
        s = _struct([
            Segment(id="seg-001", start=0, end=400, title="Break", speaker="DM"),
            Segment(
                id="seg-002",
                start=400,
                end=1000,
                title="Founding",
                speaker="DM",
            ),
            Segment(id="seg-003", start=1000, end=1500, title="Break", speaker="DM"),
        ])
        # only 400s stretches; below 500s threshold → no warnings
        assert check(s, threshold_seconds=500) == []
        # but raise threshold low → both stretches fire
        warnings = check(s, threshold_seconds=300)
        assert len(warnings) == 2

    def test_case_insensitive_match(self) -> None:
        s = _struct([
            Segment(id="seg-001", start=0, end=800, title="BREAK", speaker="DM"),
        ])
        warnings = check(s, threshold_seconds=300)
        assert len(warnings) == 1

    def test_custom_patterns(self) -> None:
        s = _struct([
            Segment(id="seg-001", start=0, end=800, title="Lunch", speaker="DM"),
        ])
        # default patterns don't include "lunch"
        assert check(s, threshold_seconds=300) == []
        # but custom patterns do
        warnings = check(s, threshold_seconds=300, low_yield_patterns=["lunch"])
        assert len(warnings) == 1

    def test_default_patterns_include_common_low_yield(self) -> None:
        expected = {"rules", "break", "off-topic", "silence", "inaudible"}
        assert expected <= {p.lower() for p in DEFAULT_LOW_YIELD_PATTERNS}

    def test_warning_format_string(self) -> None:
        s = _struct([
            Segment(id="seg-001", start=2_050, end=2_902, title="Break", speaker="DM"),
        ])
        warnings = check(s, threshold_seconds=500)
        assert len(warnings) == 1
        msg = warnings[0].format_warning()
        assert "0:34:10" in msg
        assert "0:48:22" in msg
        assert "Break" in msg
