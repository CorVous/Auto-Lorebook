"""Tests for commands.generate_reading."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from auto_lorebook.cli import create_parser
from auto_lorebook.commands.generate_reading import run

if TYPE_CHECKING:
    from pathlib import Path


def test_parser_registered() -> None:
    """generate-reading subcommand registered in main CLI."""
    parser = create_parser()
    args = parser.parse_args(["generate-reading", "srt-abc123"])
    assert args.command == "generate-reading"
    assert args.source_id == "srt-abc123"


def test_run_returns_int(tmp_path: Path) -> None:
    """run() returns an integer exit code."""
    args = argparse.Namespace(source_id="srt-test", state_dir=tmp_path)
    result = run(args)
    assert isinstance(result, int)


def test_run_returns_zero(tmp_path: Path) -> None:
    """Stub run() returns 0 (pipeline not yet implemented)."""
    args = argparse.Namespace(source_id="srt-stub", state_dir=tmp_path)
    assert run(args) == 0
