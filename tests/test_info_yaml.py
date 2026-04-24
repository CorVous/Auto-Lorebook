"""Tests for info_yaml.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path
import yaml

from auto_lorebook.info_yaml import Info, InfoError, SourceContext, read, write


def _make_info(source_id: str = "txt-abc1234567") -> Info:
    return Info(
        source_id=source_id,
        source_type="text",
        fetched_at="2026-04-24T12:00:00Z",
        title="My Notes",
        context=SourceContext(perspective="test", source_nature="notes"),
    )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def test_write_creates_file(tmp_path: Path) -> None:
    info = _make_info()
    path = tmp_path / "info.yaml"
    write(info, path)
    assert path.exists()


def test_write_schema_version_is_first_key(tmp_path: Path) -> None:
    info = _make_info()
    path = tmp_path / "info.yaml"
    write(info, path)
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("schema_version:")


def test_write_sort_keys_false(tmp_path: Path) -> None:
    """schema_version must appear before source_id."""
    info = _make_info()
    path = tmp_path / "info.yaml"
    write(info, path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    keys = list(raw.keys())
    assert keys[0] == "schema_version"


def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    info = _make_info()
    path = tmp_path / "nested" / "dir" / "info.yaml"
    write(info, path)
    assert path.exists()


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip(tmp_path: Path) -> None:
    info = _make_info("srt-deadbeef01")
    info.source_url = "https://youtu.be/abc123defgh"
    info.context.setting = "Aether Chronicles"
    info.context.speakers = [{"name": "Finn", "role": "guest-player"}]
    path = tmp_path / "info.yaml"
    write(info, path)
    loaded = read(path)
    assert loaded.source_id == "srt-deadbeef01"
    assert loaded.source_url == "https://youtu.be/abc123defgh"
    assert loaded.context.setting == "Aether Chronicles"
    assert loaded.context.speakers == [{"name": "Finn", "role": "guest-player"}]


def test_round_trip_null_fields(tmp_path: Path) -> None:
    info = Info(
        source_id="txt-1234567890",
        source_type="text",
        fetched_at="2026-04-24T00:00:00Z",
    )
    path = tmp_path / "info.yaml"
    write(info, path)
    loaded = read(path)
    assert loaded.source_url is None
    assert loaded.session_date is None
    assert loaded.context.perspective is None


# ---------------------------------------------------------------------------
# Read errors
# ---------------------------------------------------------------------------


def test_read_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(InfoError, match="not found"):
        read(tmp_path / "info.yaml")


def test_read_missing_schema_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "info.yaml"
    path.write_text(
        yaml.safe_dump({"source_id": "x", "source_type": "text", "fetched_at": "now"}),
        encoding="utf-8",
    )
    with pytest.raises(InfoError, match="missing schema_version"):
        read(path)


def test_read_future_schema_version_raises(tmp_path: Path) -> None:
    path = tmp_path / "info.yaml"
    path.write_text(
        yaml.safe_dump({
            "schema_version": 99,
            "source_id": "x",
            "source_type": "text",
            "fetched_at": "now",
        }),
        encoding="utf-8",
    )
    with pytest.raises(InfoError, match="exceeds max supported"):
        read(path)
