"""Tests for pipeline.gap_check module."""

from __future__ import annotations

from auto_lorebook.pipeline.gap_check import check_gaps


def _seg(
    seg_id: str,
    start: str,
    end: str,
    title: str = "",
    notes: str | None = None,
) -> dict[str, object]:
    return {
        "id": seg_id,
        "start": start,
        "end": end,
        "title": title,
        "notes": notes,
        "overrides": [],
    }


def test_no_warnings_for_high_yield_content() -> None:
    structure: dict[str, object] = {
        "segments": [
            _seg("s1", "0:00:00", "0:10:00", "Introduction"),
            _seg("s2", "0:10:00", "0:20:00", "Combat"),
            _seg("s3", "0:20:00", "0:30:00", "Roleplay"),
        ]
    }
    assert check_gaps(structure) == []


def test_warns_for_long_low_yield_stretch() -> None:
    structure: dict[str, object] = {
        "segments": [
            _seg("s1", "0:00:00", "0:05:00", "break"),
            _seg("s2", "0:05:00", "0:15:00", "rules discussion"),
            _seg("s3", "0:15:00", "0:30:00", "Combat"),
        ]
    }
    warnings = check_gaps(structure, threshold_seconds=300)
    assert len(warnings) == 1
    assert warnings[0].duration_seconds >= 900  # 15 min total


def test_no_warning_below_threshold() -> None:
    structure: dict[str, object] = {
        "segments": [_seg("s1", "0:00:00", "0:02:00", "break")]
    }
    assert check_gaps(structure, threshold_seconds=300) == []


def test_warns_on_inaudible_notes() -> None:
    structure: dict[str, object] = {
        "segments": [
            _seg("s1", "0:00:00", "0:10:00", "Session", notes="mostly inaudible here"),
            _seg("s2", "0:10:00", "0:20:00", "Session", notes="still inaudible"),
        ]
    }
    warnings = check_gaps(structure, threshold_seconds=300)
    assert len(warnings) == 1


def test_multiple_separate_runs_each_warn() -> None:
    structure: dict[str, object] = {
        "segments": [
            _seg("s1", "0:00:00", "0:10:00", "break"),
            _seg("s2", "0:10:00", "0:20:00", "Combat"),
            _seg("s3", "0:20:00", "0:30:00", "break"),
        ]
    }
    warnings = check_gaps(structure, threshold_seconds=300)
    assert len(warnings) == 2


def test_empty_structure_no_warnings() -> None:
    assert check_gaps({"segments": []}) == []


def test_trailing_run_warns() -> None:
    structure: dict[str, object] = {
        "segments": [
            _seg("s1", "0:00:00", "0:05:00", "Combat"),
            _seg("s2", "0:05:00", "0:20:00", "silence"),
        ]
    }
    warnings = check_gaps(structure, threshold_seconds=300)
    assert len(warnings) == 1
    assert warnings[0].segment_ids == ["s2"]


def test_warning_includes_segment_ids() -> None:
    structure: dict[str, object] = {
        "segments": [
            _seg("s1", "0:00:00", "0:10:00", "break"),
            _seg("s2", "0:10:00", "0:20:00", "off-topic"),
            _seg("s3", "0:20:00", "0:30:00", "Combat"),
        ]
    }
    warnings = check_gaps(structure, threshold_seconds=300)
    assert len(warnings) == 1
    assert "s1" in warnings[0].segment_ids
    assert "s2" in warnings[0].segment_ids
