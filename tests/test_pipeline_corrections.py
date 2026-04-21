"""Tests for pipeline.corrections module."""

from __future__ import annotations

from auto_lorebook.pipeline.corrections import apply_corrections, merge_corrections


def test_apply_single_correction() -> None:
    result = apply_corrections("hello Fair-on", [{"from": "Fair-on", "to": "Theron"}])
    assert result == "hello Theron"


def test_apply_multiple_corrections() -> None:
    corrs = [{"from": "A", "to": "X"}, {"from": "B", "to": "Y"}]
    assert apply_corrections("A and B", corrs) == "X and Y"


def test_apply_last_wins_on_same_from() -> None:
    corrs = [{"from": "A", "to": "X"}, {"from": "A", "to": "Z"}]
    assert apply_corrections("A", corrs) == "Z"


def test_apply_no_corrections_noop() -> None:
    assert apply_corrections("unchanged", []) == "unchanged"


def test_apply_skips_empty_from() -> None:
    result = apply_corrections("text", [{"from": "", "to": "X"}])
    assert result == "text"


def test_merge_corrections_source_wins() -> None:
    globs = [{"from": "A", "to": "X"}]
    source = [{"from": "A", "to": "Z"}]
    merged = merge_corrections(globs, source)
    froms = {c["from"]: c["to"] for c in merged}
    assert froms["A"] == "Z"


def test_merge_corrections_global_preserved() -> None:
    globs = [{"from": "A", "to": "X"}, {"from": "B", "to": "Y"}]
    source = [{"from": "A", "to": "Z"}]
    merged = merge_corrections(globs, source)
    froms = {c["from"]: c["to"] for c in merged}
    assert froms["B"] == "Y"
    assert froms["A"] == "Z"


def test_merge_corrections_empty_source() -> None:
    globs = [{"from": "A", "to": "X"}]
    merged = merge_corrections(globs, [])
    assert {"from": "A", "to": "X"} in merged
