"""Tests for structure.py — Stage 1a output schema + validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from auto_lorebook.structure import (
    Override,
    Segment,
    Structure,
    StructureValidationError,
    UncertaintyFlag,
    read,
    validate,
    write,
)

if TYPE_CHECKING:
    from pathlib import Path


def _mk_struct(**overrides: object) -> Structure:
    defaults: dict[str, object] = {
        "source_id": "yt-abc",
        "generated_at": "2026-04-20T00:00:00Z",
        "default_speaker": "DM",
        "segments": [
            Segment(
                id="seg-001",
                start=0.0,
                end=120.0,
                title="Intro",
                speaker="DM",
            ),
            Segment(
                id="seg-002",
                start=120.0,
                end=600.0,
                title="Body",
                speaker="DM",
            ),
        ],
        "uncertainty_flags": [],
    }
    defaults.update(overrides)
    return Structure(**defaults)  # type: ignore[arg-type]


class TestReadWrite:
    def test_round_trip(self, tmp_path: Path) -> None:
        s = _mk_struct(
            uncertainty_flags=[
                UncertaintyFlag(
                    locator=347.0, span="a place name", kind="name", note="unclear"
                )
            ],
        )
        s.segments[1].overrides = [
            Override(start=180.0, end=195.0, speaker="NPC", voiced_by="DM")
        ]
        path = tmp_path / "structure.yaml"
        write(s, path)
        loaded = read(path)
        assert loaded.source_id == "yt-abc"
        assert len(loaded.segments) == 2
        assert loaded.segments[1].overrides[0].speaker == "NPC"
        assert loaded.uncertainty_flags[0].span == "a place name"

    def test_writer_emits_canonical_timestamps(self, tmp_path: Path) -> None:
        s = _mk_struct()
        path = tmp_path / "structure.yaml"
        write(s, path)
        text = path.read_text(encoding="utf-8")
        assert "0:00:00" in text
        assert "0:02:00" in text
        assert "0:10:00" in text

    def test_reader_accepts_srt_comma_decimals(self, tmp_path: Path) -> None:
        path = tmp_path / "structure.yaml"
        path.write_text(
            """schema_version: 1
source_id: yt-abc
generated_at: '2026-04-20T00:00:00Z'
default_speaker: DM
segments:
  - id: seg-001
    start: '00:00:00,000'
    end: '00:02:00,500'
    title: Intro
    speaker: DM
uncertainty_flags: []
""",
            encoding="utf-8",
        )
        loaded = read(path)
        assert loaded.segments[0].start == pytest.approx(0.0)
        assert loaded.segments[0].end == pytest.approx(120.5)


class TestValidate:
    def test_ok(self) -> None:
        validate(_mk_struct(), total_duration=600.0)

    def test_no_segments_raises(self) -> None:
        s = _mk_struct(segments=[])
        with pytest.raises(StructureValidationError, match="no segments"):
            validate(s, total_duration=600.0)

    def test_first_segment_must_start_at_zero(self) -> None:
        s = _mk_struct(
            segments=[
                Segment(id="seg-001", start=30.0, end=600.0, title="x", speaker="DM"),
            ]
        )
        with pytest.raises(StructureValidationError, match="start"):
            validate(s, total_duration=600.0)

    def test_last_segment_must_reach_duration(self) -> None:
        s = _mk_struct(
            segments=[
                Segment(id="seg-001", start=0.0, end=300.0, title="x", speaker="DM"),
            ]
        )
        with pytest.raises(StructureValidationError, match=r"end|duration"):
            validate(s, total_duration=600.0)

    def test_gap_between_segments_raises(self) -> None:
        s = _mk_struct(
            segments=[
                Segment(id="seg-001", start=0.0, end=100.0, title="a", speaker="DM"),
                Segment(id="seg-002", start=150.0, end=600.0, title="b", speaker="DM"),
            ]
        )
        with pytest.raises(StructureValidationError, match="gap"):
            validate(s, total_duration=600.0)

    def test_overlap_between_segments_raises(self) -> None:
        s = _mk_struct(
            segments=[
                Segment(id="seg-001", start=0.0, end=200.0, title="a", speaker="DM"),
                Segment(id="seg-002", start=100.0, end=600.0, title="b", speaker="DM"),
            ]
        )
        with pytest.raises(StructureValidationError, match=r"overlap|gap"):
            validate(s, total_duration=600.0)

    def test_segment_end_before_start_raises(self) -> None:
        s = _mk_struct(
            segments=[
                Segment(id="seg-001", start=100.0, end=50.0, title="x", speaker="DM"),
            ]
        )
        with pytest.raises(StructureValidationError):
            validate(s, total_duration=600.0)

    def test_override_outside_segment_raises(self) -> None:
        s = _mk_struct()
        s.segments[0].overrides = [
            Override(start=500.0, end=550.0, speaker="NPC"),
        ]
        with pytest.raises(StructureValidationError, match="override"):
            validate(s, total_duration=600.0)

    def test_uncertainty_locator_outside_segments_raises(self) -> None:
        s = _mk_struct(
            uncertainty_flags=[
                UncertaintyFlag(locator=700.0, span="x", kind="name"),
            ]
        )
        with pytest.raises(StructureValidationError, match="uncertainty"):
            validate(s, total_duration=600.0)

    def test_duplicate_segment_ids_raise(self) -> None:
        s = _mk_struct(
            segments=[
                Segment(id="seg-001", start=0.0, end=100.0, title="a", speaker="DM"),
                Segment(id="seg-001", start=100.0, end=600.0, title="b", speaker="DM"),
            ]
        )
        with pytest.raises(StructureValidationError, match="duplicate"):
            validate(s, total_duration=600.0)

    def test_tolerance_allows_small_gap(self) -> None:
        # small gap/overlap within tolerance should not raise
        s = _mk_struct(
            segments=[
                Segment(id="seg-001", start=0.0, end=100.5, title="a", speaker="DM"),
                Segment(id="seg-002", start=100.0, end=600.0, title="b", speaker="DM"),
            ]
        )
        validate(s, total_duration=600.0, tolerance=1.0)
