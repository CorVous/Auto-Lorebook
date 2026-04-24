"""Tests for corrections.py."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from auto_lorebook.corrections import Corrections, read


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    c = read(tmp_path / ".transcription-corrections.yaml")
    assert c.corrections == []


def test_empty_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / ".transcription-corrections.yaml"
    path.write_text("", encoding="utf-8")
    assert read(path).corrections == []


def test_schema_version_only(tmp_path: Path) -> None:
    path = tmp_path / ".transcription-corrections.yaml"
    path.write_text("schema_version: 1\n", encoding="utf-8")
    c = read(path)
    assert isinstance(c, Corrections)
    assert c.corrections == []


def test_missing_schema_version_warns_and_reads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / ".transcription-corrections.yaml"
    path.write_text(
        "corrections:\n  - wrong: Aldera\n    right: Aldara\n", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        c = read(path)
    assert "missing schema_version" in caplog.text
    assert len(c.corrections) == 1


def test_full_file(tmp_path: Path) -> None:
    path = tmp_path / ".transcription-corrections.yaml"
    path.write_text(
        "schema_version: 1\n"
        "corrections:\n"
        "  - wrong: Aldera\n"
        "    right: Aldara\n"
        "    first_seen_in: yt-abc123\n"
        "    also_seen_in: [yt-def456]\n",
        encoding="utf-8",
    )
    c = read(path)
    assert len(c.corrections) == 1
    cor = c.corrections[0]
    assert cor.wrong == "Aldera"
    assert cor.right == "Aldara"
    assert cor.first_seen_in == "yt-abc123"
    assert cor.also_seen_in == ["yt-def456"]


def test_malformed_yaml_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / ".transcription-corrections.yaml"
    path.write_text(":::invalid:::\n", encoding="utf-8")
    c = read(path)
    assert c.corrections == []


def test_entries_missing_wrong_or_right_skipped(tmp_path: Path) -> None:
    path = tmp_path / ".transcription-corrections.yaml"
    path.write_text(
        "schema_version: 1\n"
        "corrections:\n"
        "  - wrong: Aldera\n"
        "  - right: Aldara\n"
        "  - wrong: Kelm\n"
        "    right: Khelm\n",
        encoding="utf-8",
    )
    c = read(path)
    assert len(c.corrections) == 1
    assert c.corrections[0].wrong == "Kelm"
