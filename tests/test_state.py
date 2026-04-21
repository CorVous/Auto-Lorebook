"""Tests for state directory layout."""

from __future__ import annotations

import re
import string
from datetime import UTC, datetime
from pathlib import Path

import pytest

from auto_lorebook.state import create_ingest_dir, generate_ingest_id, get_state_dir


def test_get_state_dir_returns_path() -> None:
    """get_state_dir returns a Path containing .auto-lorebook."""
    d = get_state_dir()
    assert isinstance(d, Path)
    assert ".auto-lorebook" in d.parts or ".auto-lorebook" in d.name


def test_generate_ingest_id_format(tmp_path: Path) -> None:
    """Ingest ID matches ingest-YYYY-MM-DD-<letter> pattern."""
    ingest_id = generate_ingest_id(tmp_path)
    assert re.match(r"^ingest-\d{4}-\d{2}-\d{2}-[a-z]$", ingest_id)


def test_generate_ingest_id_starts_with_a(tmp_path: Path) -> None:
    """First ingest ID of the day ends with letter 'a'."""
    ingest_id = generate_ingest_id(tmp_path)
    assert ingest_id.endswith("-a")


def test_generate_ingest_id_collision_avoidance(tmp_path: Path) -> None:
    """Second call returns a different ID when first slot is taken."""
    first = generate_ingest_id(tmp_path)
    (tmp_path / "pending" / first).mkdir(parents=True)
    second = generate_ingest_id(tmp_path)
    assert first != second
    assert second.endswith("-b")


def test_generate_ingest_id_exhausted_raises(tmp_path: Path) -> None:
    """RuntimeError raised when all 26 slots for today are taken."""
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    for letter in string.ascii_lowercase:
        (tmp_path / "pending" / f"ingest-{today}-{letter}").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="all 26"):
        generate_ingest_id(tmp_path)


def test_create_ingest_dir_creates_subdirs(tmp_path: Path) -> None:
    """create_ingest_dir creates reading/ and proposals/ subdirectories."""
    ingest_dir = create_ingest_dir(tmp_path, "ingest-2026-04-21-a")
    assert (ingest_dir / "reading").is_dir()
    assert (ingest_dir / "proposals").is_dir()


def test_create_ingest_dir_returns_root(tmp_path: Path) -> None:
    """create_ingest_dir return value is the ingest root path."""
    ingest_dir = create_ingest_dir(tmp_path, "ingest-2026-04-21-a")
    assert ingest_dir == tmp_path / "pending" / "ingest-2026-04-21-a"


def test_create_ingest_dir_idempotent(tmp_path: Path) -> None:
    """Calling create_ingest_dir twice does not raise."""
    create_ingest_dir(tmp_path, "ingest-2026-04-21-a")
    create_ingest_dir(tmp_path, "ingest-2026-04-21-a")
