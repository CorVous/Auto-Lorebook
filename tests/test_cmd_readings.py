"""Tests for commands.readings."""

from __future__ import annotations

import argparse
import io
import sys
from typing import TYPE_CHECKING

from auto_lorebook.cli import create_parser
from auto_lorebook.commands.readings import run_list, run_show
from auto_lorebook.reading.frontmatter import join_frontmatter
from auto_lorebook.sources.info_yaml import make_info_yaml, write_info_yaml

if TYPE_CHECKING:
    from pathlib import Path

_SOURCE_ID = "srt-readtest1"


def _seed_source(state_dir: Path, source_id: str, title: str = "Test Session") -> None:
    """Write info.yaml for a source."""
    info = make_info_yaml(
        source_id=source_id,
        source_type="srt",
        source_url=None,
        title=title,
        duration_seconds=60.0,
        caption_type="n/a",
    )
    write_info_yaml(state_dir / "sources" / source_id / "info.yaml", info)


def _seed_reading(state_dir: Path, source_id: str, status: str = "draft") -> None:
    """Write a reading.md in pending dir."""
    ingest_dir = state_dir / "pending" / "ingest-2025-02-01-a"
    (ingest_dir / "reading").mkdir(parents=True, exist_ok=True)
    fm: dict[str, object] = {
        "schema_version": 1,
        "source_id": source_id,
        "reading_status": status,
    }
    content = join_frontmatter(fm, "# Body\n")
    (ingest_dir / "reading" / "reading.md").write_text(content, encoding="utf-8")


def _list_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(state_dir=tmp_path / "state")


def _show_args(tmp_path: Path, source_id: str) -> argparse.Namespace:
    return argparse.Namespace(source_id=source_id, state_dir=tmp_path / "state")


# ── argument parsing ──────────────────────────────────────────────────────────


def test_readings_list_parser() -> None:
    """Readings list subcommand registered in main CLI."""
    parser = create_parser()
    args = parser.parse_args(["readings", "list"])
    assert args.command == "readings"
    assert args.readings_command == "list"


def test_readings_show_parser() -> None:
    """Readings show subcommand registered with source_id."""
    parser = create_parser()
    args = parser.parse_args(["readings", "show", _SOURCE_ID])
    assert args.readings_command == "show"
    assert args.source_id == _SOURCE_ID


# ── readings list ─────────────────────────────────────────────────────────────


def test_list_empty_returns_0(tmp_path: Path) -> None:
    """No sources → returns 0."""
    args = _list_args(tmp_path)
    rc = run_list(args)
    assert rc == 0


def test_list_shows_source(tmp_path: Path) -> None:
    """Ingested source appears in list output."""
    state_dir = tmp_path / "state"
    _seed_source(state_dir, _SOURCE_ID, title="Campaign Log")

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        run_list(_list_args(tmp_path))
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert _SOURCE_ID in out
    assert "Campaign Log" in out


def test_list_shows_reading_status(tmp_path: Path) -> None:
    """Reading status shown when reading.md exists."""
    state_dir = tmp_path / "state"
    _seed_source(state_dir, _SOURCE_ID)
    _seed_reading(state_dir, _SOURCE_ID, status="draft")

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        run_list(_list_args(tmp_path))
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert "draft" in out


# ── readings show ─────────────────────────────────────────────────────────────


def test_show_found_returns_0(tmp_path: Path) -> None:
    """Show returns 0 when reading found."""
    state_dir = tmp_path / "state"
    _seed_reading(state_dir, _SOURCE_ID)
    rc = run_show(_show_args(tmp_path, _SOURCE_ID))
    assert rc == 0


def test_show_not_found_returns_1(tmp_path: Path) -> None:
    """Show returns 1 when no reading found."""
    rc = run_show(_show_args(tmp_path, "srt-missing"))
    assert rc == 1


def test_show_prints_content(tmp_path: Path) -> None:
    """Show prints the reading.md content."""
    state_dir = tmp_path / "state"
    _seed_reading(state_dir, _SOURCE_ID, status="draft")

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        run_show(_show_args(tmp_path, _SOURCE_ID))
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout

    assert _SOURCE_ID in out
    assert "draft" in out
