"""Tests for commands.ingest."""

from __future__ import annotations

import argparse
import io
import sys
from typing import TYPE_CHECKING

import yaml

from auto_lorebook.cli import create_parser
from auto_lorebook.commands.ingest import run

if TYPE_CHECKING:
    from pathlib import Path

_SRT = "1\n00:00:00,000 --> 00:01:00,000\nHello world\n"


def _make_args(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    """Build a Namespace for ingest with sane test defaults."""
    srt = tmp_path / "session.srt"
    srt.write_text(_SRT, encoding="utf-8")
    args = argparse.Namespace(
        url_or_path=str(srt),
        source_url=None,
        title=None,
        session_date=None,
        perspective=None,
        source_nature=None,
        setting=None,
        no_interactive=True,
        state_dir=tmp_path / "state",
    )
    for k, v in overrides.items():
        setattr(args, k, v)
    return args


# ── argument parsing ──────────────────────────────────────────────────────────


def test_parser_registered() -> None:
    """Ingest subcommand is registered in the main CLI."""
    parser = create_parser()
    args = parser.parse_args(["ingest", "some/path.srt"])
    assert args.command == "ingest"
    assert args.url_or_path == "some/path.srt"


def test_source_url_arg() -> None:
    """--source-url stored in args.source_url."""
    parser = create_parser()
    args = parser.parse_args(["ingest", "f.srt", "--source-url", "https://example.com"])
    assert args.source_url == "https://example.com"


def test_no_interactive_flag() -> None:
    """--no-interactive sets args.no_interactive."""
    parser = create_parser()
    args = parser.parse_args(["ingest", "f.srt", "--no-interactive"])
    assert args.no_interactive is True


def test_session_date_arg() -> None:
    """--session-date stored as session_date."""
    parser = create_parser()
    args = parser.parse_args(["ingest", "f.srt", "--session-date", "2025-01-01"])
    assert args.session_date == "2025-01-01"


# ── full flow ─────────────────────────────────────────────────────────────────


def test_ingest_creates_info_yaml(tmp_path: Path) -> None:
    """Successful ingest writes info.yaml to sources dir."""
    args = _make_args(tmp_path)
    rc = run(args)
    assert rc == 0
    sources_dir = tmp_path / "state" / "sources"
    info_files = list(sources_dir.glob("*/info.yaml"))
    assert len(info_files) == 1


def test_ingest_info_yaml_content(tmp_path: Path) -> None:
    """Written info.yaml has correct schema_version and source_type."""
    args = _make_args(tmp_path)
    run(args)
    info_path = next((tmp_path / "state" / "sources").glob("*/info.yaml"))
    data = yaml.safe_load(info_path.read_text())
    assert data["schema_version"] == 1
    assert data["source_type"] == "srt"
    assert data["caption_type"] == "n/a"


def test_ingest_title_from_stem(tmp_path: Path) -> None:
    """Title defaults to filename stem when not overridden."""
    args = _make_args(tmp_path)
    run(args)
    info_path = next((tmp_path / "state" / "sources").glob("*/info.yaml"))
    data = yaml.safe_load(info_path.read_text())
    assert data["title"] == "session"


def test_ingest_title_override(tmp_path: Path) -> None:
    """--title flag overrides the default stem title."""
    args = _make_args(tmp_path, title="My Campaign Session")
    run(args)
    info_path = next((tmp_path / "state" / "sources").glob("*/info.yaml"))
    data = yaml.safe_load(info_path.read_text())
    assert data["title"] == "My Campaign Session"


# ── transcript cache path ─────────────────────────────────────────────────────


def test_transcript_cache_proceeds_without_info_yaml(tmp_path: Path) -> None:
    """Cache hit (transcript exists, no info.yaml) → proceeds, writes info.yaml."""
    args = _make_args(tmp_path)
    rc = run(args)
    assert rc == 0
    info_path = next((tmp_path / "state" / "sources").glob("*/info.yaml"))

    # Simulate interrupted ingest: remove info.yaml, keep transcript
    info_path.unlink()

    rc2 = run(args)
    assert rc2 == 0
    assert info_path.exists()


# ── full duplicate guard ──────────────────────────────────────────────────────


def test_full_duplicate_refused(tmp_path: Path) -> None:
    """Second ingest of same source (info.yaml exists) returns 1."""
    args = _make_args(tmp_path)
    run(args)
    rc = run(args)
    assert rc == 1


def test_full_duplicate_message_actionable(tmp_path: Path) -> None:
    """Duplicate refusal message mentions configure-context and generate-reading."""
    args = _make_args(tmp_path)
    run(args)
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        run(args)
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    assert "configure-context" in out
    assert "generate-reading" in out


# ── error cases ───────────────────────────────────────────────────────────────


def test_file_not_found_returns_1(tmp_path: Path) -> None:
    """Nonexistent local path returns exit code 1."""
    args = _make_args(tmp_path, url_or_path=str(tmp_path / "missing.srt"))
    rc = run(args)
    assert rc == 1


def test_youtube_url_returns_1(tmp_path: Path) -> None:
    """YouTube URL returns 1 (not yet wired)."""
    args = _make_args(tmp_path, url_or_path="https://www.youtube.com/watch?v=abc")
    rc = run(args)
    assert rc == 1
