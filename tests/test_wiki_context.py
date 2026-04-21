"""Tests for context.wiki_context."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook.context.wiki_context import WikiContext, read_wiki_context
from auto_lorebook.schema import SchemaVersionError

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


# ── missing / empty ───────────────────────────────────────────────────────────


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    """Absent file returns empty defaults."""
    result = read_wiki_context(tmp_path / ".wiki-context.yaml")
    assert result["setting"] is None
    assert result["naming_conventions"] == []
    assert result["interpretation_defaults"] == {}
    assert result["recurring_speakers"] == []


def test_empty_file_returns_empty(tmp_path: Path) -> None:
    """Empty YAML file returns empty defaults."""
    p = tmp_path / ".wiki-context.yaml"
    p.write_text("", encoding="utf-8")
    result = read_wiki_context(p)
    assert result["setting"] is None
    assert result["naming_conventions"] == []


def test_no_schema_version_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing schema_version logs a warning."""
    p = tmp_path / ".wiki-context.yaml"
    _write(p, {"setting": "test world"})
    with caplog.at_level(logging.WARNING):
        read_wiki_context(p)
    assert "schema_version" in caplog.text


# ── populated file ────────────────────────────────────────────────────────────


def test_setting_read(tmp_path: Path) -> None:
    """Setting field is read correctly."""
    p = tmp_path / ".wiki-context.yaml"
    _write(p, {"schema_version": 1, "setting": "Eberron"})
    result = read_wiki_context(p)
    assert result["setting"] == "Eberron"


def test_naming_conventions_read(tmp_path: Path) -> None:
    """naming_conventions list read correctly."""
    p = tmp_path / ".wiki-context.yaml"
    _write(
        p,
        {
            "schema_version": 1,
            "naming_conventions": ["Capitalize god names", "Use full titles"],
        },
    )
    result = read_wiki_context(p)
    assert result["naming_conventions"] == ["Capitalize god names", "Use full titles"]


def test_interpretation_defaults_read(tmp_path: Path) -> None:
    """interpretation_defaults dict read correctly."""
    p = tmp_path / ".wiki-context.yaml"
    _write(
        p,
        {"schema_version": 1, "interpretation_defaults": {"ambiguous_roll": "failed"}},
    )
    result = read_wiki_context(p)
    assert result["interpretation_defaults"] == {"ambiguous_roll": "failed"}


def test_recurring_speakers_read(tmp_path: Path) -> None:
    """recurring_speakers list read correctly."""
    p = tmp_path / ".wiki-context.yaml"
    _write(p, {"schema_version": 1, "recurring_speakers": ["Alice", "Bob"]})
    result = read_wiki_context(p)
    assert result["recurring_speakers"] == ["Alice", "Bob"]


def test_fully_populated(tmp_path: Path) -> None:
    """Fully-populated file round-trips all fields."""
    p = tmp_path / ".wiki-context.yaml"
    _write(
        p,
        {
            "schema_version": 1,
            "setting": "Forgotten Realms",
            "naming_conventions": ["Rule A"],
            "interpretation_defaults": {"x": "y"},
            "recurring_speakers": ["DM", "Player 1"],
        },
    )
    result = read_wiki_context(p)
    assert result["setting"] == "Forgotten Realms"
    assert result["naming_conventions"] == ["Rule A"]
    assert result["interpretation_defaults"] == {"x": "y"}
    assert result["recurring_speakers"] == ["DM", "Player 1"]


def test_schema_version_too_new_raises(tmp_path: Path) -> None:
    """schema_version > 1 raises SchemaVersionError."""
    p = tmp_path / ".wiki-context.yaml"
    _write(p, {"schema_version": 99, "setting": "future"})
    with pytest.raises(SchemaVersionError):
        read_wiki_context(p)


def test_return_type_is_wiki_context(tmp_path: Path) -> None:
    """Return type satisfies WikiContext TypedDict keys."""
    p = tmp_path / ".wiki-context.yaml"
    _write(p, {"schema_version": 1})
    result = read_wiki_context(p)
    assert isinstance(result, dict)
    # all required keys present
    assert set(WikiContext.__required_keys__).issubset(result.keys())  # type: ignore[attr-defined]
