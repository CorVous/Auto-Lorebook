"""Tests for commands.approve_reading."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from auto_lorebook.cli import create_parser
from auto_lorebook.commands.approve_reading import find_reading_path, run
from auto_lorebook.reading.frontmatter import join_frontmatter, split_frontmatter

if TYPE_CHECKING:
    from pathlib import Path

_SOURCE_ID = "srt-testabc1"

_DRAFT_FM: dict[str, object] = {
    "schema_version": 1,
    "source_id": _SOURCE_ID,
    "reading_status": "draft",
}
_DRAFT_BODY = "# Segment 1\n\n- Bullet one\n"


def _write_reading(state_dir: Path, source_id: str, status: str = "draft") -> None:
    """Create a minimal reading.md in the pending dir."""
    ingest_dir = state_dir / "pending" / "ingest-2025-01-01-a"
    (ingest_dir / "reading").mkdir(parents=True, exist_ok=True)
    fm: dict[str, object] = {
        "schema_version": 1,
        "source_id": source_id,
        "reading_status": status,
    }
    content = join_frontmatter(fm, _DRAFT_BODY)
    (ingest_dir / "reading" / "reading.md").write_text(content, encoding="utf-8")


def _make_args(tmp_path: Path, source_id: str = _SOURCE_ID) -> argparse.Namespace:
    return argparse.Namespace(
        source_id=source_id,
        state_dir=tmp_path / "state",
    )


# ── argument parsing ──────────────────────────────────────────────────────────


def test_parser_registered() -> None:
    """approve-reading subcommand registered in main CLI."""
    parser = create_parser()
    args = parser.parse_args(["approve-reading", _SOURCE_ID])
    assert args.command == "approve-reading"
    assert args.source_id == _SOURCE_ID


# ── find_reading_path ─────────────────────────────────────────────────────────


def test_find_reading_path_exists(tmp_path: Path) -> None:
    """find_reading_path returns path when reading exists."""
    state_dir = tmp_path / "state"
    _write_reading(state_dir, _SOURCE_ID)
    result = find_reading_path(state_dir, _SOURCE_ID)
    assert result is not None
    assert result.exists()


def test_find_reading_path_missing(tmp_path: Path) -> None:
    """find_reading_path returns None when no matching reading."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    assert find_reading_path(state_dir, "srt-missing") is None


# ── approve ───────────────────────────────────────────────────────────────────


def test_approve_draft_returns_0(tmp_path: Path) -> None:
    """Approving a draft reading returns exit code 0."""
    state_dir = tmp_path / "state"
    _write_reading(state_dir, _SOURCE_ID)
    args = _make_args(tmp_path)
    rc = run(args)
    assert rc == 0


def test_approve_updates_status(tmp_path: Path) -> None:
    """reading_status changes from draft to approved."""
    state_dir = tmp_path / "state"
    _write_reading(state_dir, _SOURCE_ID)
    args = _make_args(tmp_path)
    run(args)
    reading_path = find_reading_path(state_dir, _SOURCE_ID)
    assert reading_path is not None
    fm, _ = split_frontmatter(reading_path.read_text(encoding="utf-8"))
    assert fm["reading_status"] == "approved"


def test_approve_already_approved_returns_0(tmp_path: Path) -> None:
    """Already-approved reading returns 0 without error."""
    state_dir = tmp_path / "state"
    _write_reading(state_dir, _SOURCE_ID, status="approved")
    args = _make_args(tmp_path)
    rc = run(args)
    assert rc == 0


def test_approve_no_reading_returns_1(tmp_path: Path) -> None:
    """No reading found returns exit code 1."""
    args = _make_args(tmp_path, source_id="srt-notfound")
    rc = run(args)
    assert rc == 1


def test_approve_body_preserved(tmp_path: Path) -> None:
    """Body content unchanged after approval."""
    state_dir = tmp_path / "state"
    _write_reading(state_dir, _SOURCE_ID)
    args = _make_args(tmp_path)
    run(args)
    reading_path = find_reading_path(state_dir, _SOURCE_ID)
    assert reading_path is not None
    _, body = split_frontmatter(reading_path.read_text(encoding="utf-8"))
    assert body == _DRAFT_BODY
