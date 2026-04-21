"""Tests for context.corrections."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook.context.corrections import read_corrections
from auto_lorebook.schema import SchemaVersionError

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


# ── missing / empty ───────────────────────────────────────────────────────────


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    """Absent file returns empty list."""
    result = read_corrections(tmp_path / ".transcription-corrections.yaml")
    assert result == []


def test_empty_file_returns_empty(tmp_path: Path) -> None:
    """Empty YAML file returns empty list."""
    p = tmp_path / ".transcription-corrections.yaml"
    p.write_text("", encoding="utf-8")
    assert read_corrections(p) == []


def test_no_corrections_key_returns_empty(tmp_path: Path) -> None:
    """File with schema_version but no corrections key returns []."""
    p = tmp_path / ".transcription-corrections.yaml"
    _write(p, {"schema_version": 1})
    assert read_corrections(p) == []


def test_no_schema_version_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing schema_version logs a warning."""
    p = tmp_path / ".transcription-corrections.yaml"
    _write(p, {"corrections": [{"from": "a", "to": "b"}]})
    with caplog.at_level(logging.WARNING):
        read_corrections(p)
    assert "schema_version" in caplog.text


def test_schema_version_too_new_raises(tmp_path: Path) -> None:
    """schema_version > 1 raises SchemaVersionError."""
    p = tmp_path / ".transcription-corrections.yaml"
    _write(p, {"schema_version": 99, "corrections": []})
    with pytest.raises(SchemaVersionError):
        read_corrections(p)


# ── single correction ─────────────────────────────────────────────────────────


def test_single_correction_from_to(tmp_path: Path) -> None:
    """from_ and to fields parsed correctly."""
    p = tmp_path / ".transcription-corrections.yaml"
    _write(
        p,
        {
            "schema_version": 1,
            "corrections": [{"from": "Aasimar", "to": "Aasimaar"}],
        },
    )
    result = read_corrections(p)
    assert len(result) == 1
    assert result[0]["from_"] == "Aasimar"
    assert result[0]["to"] == "Aasimaar"


def test_optional_fields_none_on_missing(tmp_path: Path) -> None:
    """Optional fields default to None when absent."""
    p = tmp_path / ".transcription-corrections.yaml"
    _write(p, {"schema_version": 1, "corrections": [{"from": "x", "to": "y"}]})
    c = read_corrections(p)[0]
    assert c["first_seen_in"] is None
    assert c["also_seen_in"] == []
    assert c["promoted_at"] is None
    assert c["notes"] is None


def test_also_seen_in_parsed(tmp_path: Path) -> None:
    """also_seen_in list parsed correctly."""
    p = tmp_path / ".transcription-corrections.yaml"
    _write(
        p,
        {
            "schema_version": 1,
            "corrections": [
                {"from": "x", "to": "y", "also_seen_in": ["src-001", "src-002"]}
            ],
        },
    )
    c = read_corrections(p)[0]
    assert c["also_seen_in"] == ["src-001", "src-002"]


def test_all_optional_fields(tmp_path: Path) -> None:
    """All optional fields populated correctly."""
    p = tmp_path / ".transcription-corrections.yaml"
    _write(
        p,
        {
            "schema_version": 1,
            "corrections": [
                {
                    "from": "DnD",
                    "to": "D&D",
                    "first_seen_in": "yt-abc123",
                    "also_seen_in": ["yt-def456"],
                    "promoted_at": "2025-01-01",
                    "notes": "abbreviation",
                }
            ],
        },
    )
    c = read_corrections(p)[0]
    assert c["first_seen_in"] == "yt-abc123"
    assert c["also_seen_in"] == ["yt-def456"]
    assert c["promoted_at"] == "2025-01-01"
    assert c["notes"] == "abbreviation"


def test_multiple_corrections(tmp_path: Path) -> None:
    """Multiple correction entries all parsed."""
    p = tmp_path / ".transcription-corrections.yaml"
    _write(
        p,
        {
            "schema_version": 1,
            "corrections": [
                {"from": "a", "to": "A"},
                {"from": "b", "to": "B"},
                {"from": "c", "to": "C"},
            ],
        },
    )
    result = read_corrections(p)
    assert len(result) == 3
    assert [c["from_"] for c in result] == ["a", "b", "c"]


def test_non_dict_entries_skipped(tmp_path: Path) -> None:
    """Non-dict list entries are skipped without error."""
    p = tmp_path / ".transcription-corrections.yaml"
    _write(
        p,
        {
            "schema_version": 1,
            "corrections": [
                {"from": "x", "to": "y"},
                "not a dict",
                42,
            ],
        },
    )
    result = read_corrections(p)
    assert len(result) == 1
