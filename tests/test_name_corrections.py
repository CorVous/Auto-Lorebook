"""Tests for reading.name_corrections module."""

from __future__ import annotations

from auto_lorebook.reading.name_corrections import (
    apply_name_corrections,
    merge_with_globals,
)


def test_apply_basic_substitution() -> None:
    result = apply_name_corrections("Theron and Aldara met.", {"Theron": "King Theron"})
    assert "King Theron" in result
    assert "Aldara" in result


def test_apply_empty_corrections_noop() -> None:
    assert apply_name_corrections("unchanged", {}) == "unchanged"


def test_apply_skips_empty_key() -> None:
    assert apply_name_corrections("text", {"": "x"}) == "text"


def test_apply_multiple_corrections() -> None:
    result = apply_name_corrections("A and B", {"A": "Alpha", "B": "Beta"})
    assert "Alpha" in result
    assert "Beta" in result


def test_merge_per_source_wins_on_conflict() -> None:
    globs = [{"from": "A", "to": "X"}]
    source = {"A": "Z"}
    merged = merge_with_globals(globs, source)
    assert merged["A"] == "Z"


def test_merge_preserves_globals_not_overridden() -> None:
    globs = [{"from": "A", "to": "X"}, {"from": "B", "to": "Y"}]
    source = {"A": "Z"}
    merged = merge_with_globals(globs, source)
    assert merged["B"] == "Y"
    assert merged["A"] == "Z"


def test_merge_empty_globals() -> None:
    assert merge_with_globals([], {"A": "Z"}) == {"A": "Z"}


def test_merge_empty_source() -> None:
    globs = [{"from": "A", "to": "X"}]
    assert merge_with_globals(globs, {})["A"] == "X"
