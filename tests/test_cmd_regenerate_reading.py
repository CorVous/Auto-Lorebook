"""Tests for commands.regenerate_reading."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest

from auto_lorebook.cli import create_parser
from auto_lorebook.commands.regenerate_reading import parse_segments, run

if TYPE_CHECKING:
    from pathlib import Path


# ── argument parsing ──────────────────────────────────────────────────────────


def test_parser_registered_structure() -> None:
    """regenerate-reading --from=structure parses correctly."""
    parser = create_parser()
    args = parser.parse_args(["regenerate-reading", "srt-abc", "--from", "structure"])
    assert args.command == "regenerate-reading"
    assert args.source_id == "srt-abc"
    assert args.from_ == "structure"


def test_parser_registered_summarize() -> None:
    """regenerate-reading --from=summarize parses correctly."""
    parser = create_parser()
    args = parser.parse_args(["regenerate-reading", "srt-abc", "--from", "summarize"])
    assert args.from_ == "summarize"


def test_from_required() -> None:
    """--from is required; omitting it raises SystemExit."""
    parser = create_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["regenerate-reading", "srt-abc"])


def test_from_invalid_choice() -> None:
    """Invalid --from value raises SystemExit."""
    parser = create_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["regenerate-reading", "srt-abc", "--from", "invalid"])


def test_segments_arg() -> None:
    """--segments value stored correctly."""
    parser = create_parser()
    args = parser.parse_args([
        "regenerate-reading",
        "srt-abc",
        "--from",
        "summarize",
        "--segments",
        "seg-001,seg-002",
    ])
    assert args.segments == "seg-001,seg-002"


# ── parse_segments ────────────────────────────────────────────────────────────


def test_parse_segments_none() -> None:
    """None input returns empty list."""
    assert parse_segments(None) == []


def test_parse_segments_empty_string() -> None:
    """Empty string returns empty list."""
    assert parse_segments("") == []


def test_parse_segments_single() -> None:
    """Single segment ID parsed correctly."""
    assert parse_segments("seg-001") == ["seg-001"]


def test_parse_segments_multiple() -> None:
    """Comma-separated IDs split and stripped."""
    expected = ["seg-001", "seg-002", "seg-003"]
    assert parse_segments("seg-001,seg-002,seg-003") == expected


def test_parse_segments_trims_whitespace() -> None:
    """Whitespace around IDs is stripped."""
    assert parse_segments(" seg-001 , seg-002 ") == ["seg-001", "seg-002"]


def test_parse_segments_ignores_empty_parts() -> None:
    """Trailing/leading commas produce no empty entries."""
    assert parse_segments(",seg-001,,seg-002,") == ["seg-001", "seg-002"]


# ── run ───────────────────────────────────────────────────────────────────────


def test_run_returns_zero(tmp_path: Path) -> None:
    """Stub run() returns 0."""
    args = argparse.Namespace(
        source_id="srt-stub",
        from_="structure",
        segments=None,
        state_dir=tmp_path,
    )
    assert run(args) == 0
