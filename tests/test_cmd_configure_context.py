"""Tests for commands.configure_context."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import yaml

from auto_lorebook.cli import create_parser
from auto_lorebook.commands.configure_context import run
from auto_lorebook.context.gather import ContextInputs, save_last_context
from auto_lorebook.sources.info_yaml import make_info_yaml, write_info_yaml

if TYPE_CHECKING:
    from pathlib import Path


def _seed_info(sources_dir: Path, source_id: str) -> None:
    """Write a minimal info.yaml for testing."""
    info = make_info_yaml(
        source_id=source_id,
        source_type="srt",
        source_url=None,
        title="Test Session",
        duration_seconds=60.0,
        caption_type="n/a",
    )
    write_info_yaml(sources_dir / source_id / "info.yaml", info)


def _make_args(
    tmp_path: Path, source_id: str = "srt-test1234", **overrides: object
) -> argparse.Namespace:
    args = argparse.Namespace(
        source_id=source_id,
        no_interactive=True,
        state_dir=tmp_path / "state",
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


# ── argument parsing ──────────────────────────────────────────────────────────


def test_parser_registered() -> None:
    """configure-context subcommand registered in main CLI."""
    parser = create_parser()
    args = parser.parse_args(["configure-context", "srt-abc123"])
    assert args.command == "configure-context"
    assert args.source_id == "srt-abc123"


def test_no_interactive_flag() -> None:
    """--no-interactive flag parsed correctly."""
    parser = create_parser()
    args = parser.parse_args(["configure-context", "srt-x", "--no-interactive"])
    assert args.no_interactive is True


# ── source not found ──────────────────────────────────────────────────────────


def test_source_not_found_returns_1(tmp_path: Path) -> None:
    """Missing info.yaml returns exit code 1."""
    args = _make_args(tmp_path, source_id="srt-missing")
    rc = run(args)
    assert rc == 1


# ── context update ────────────────────────────────────────────────────────────


def test_updates_info_yaml(tmp_path: Path) -> None:
    """run() returns 0 and info.yaml exists after update."""
    state_dir = tmp_path / "state"
    sources_dir = state_dir / "sources"
    _seed_info(sources_dir, "srt-test1234")
    args = _make_args(tmp_path)
    rc = run(args)
    assert rc == 0
    assert (sources_dir / "srt-test1234" / "info.yaml").exists()


def test_updates_context_perspective(tmp_path: Path) -> None:
    """Perspective stored in info.yaml after configure-context."""
    state_dir = tmp_path / "state"
    sources_dir = state_dir / "sources"
    _seed_info(sources_dir, "srt-test1234")
    save_last_context(state_dir, ContextInputs(perspective="GM"))
    args = _make_args(tmp_path)
    run(args)
    data = yaml.safe_load((sources_dir / "srt-test1234" / "info.yaml").read_text())
    assert data["context"]["perspective"] == "GM"


def test_updates_session_date(tmp_path: Path) -> None:
    """session_date persisted when provided via last-context."""
    state_dir = tmp_path / "state"
    sources_dir = state_dir / "sources"
    _seed_info(sources_dir, "srt-test1234")
    save_last_context(state_dir, ContextInputs(session_date="2025-04-01"))
    args = _make_args(tmp_path)
    run(args)
    data = yaml.safe_load((sources_dir / "srt-test1234" / "info.yaml").read_text())
    assert data["session_date"] == "2025-04-01"
