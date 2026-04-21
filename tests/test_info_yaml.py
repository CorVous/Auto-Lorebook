"""Tests for info.yaml reader/writer."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook.schema import SchemaVersionError
from auto_lorebook.sources.info_yaml import (
    ContextBlock,
    InfoYaml,
    make_info_yaml,
    read_info_yaml,
    write_info_yaml,
)

if TYPE_CHECKING:
    from pathlib import Path


def _default_info(**kwargs: object) -> InfoYaml:
    defaults: dict[str, object] = {
        "source_id": "srt-abc1234567",
        "source_type": "srt",
        "source_url": None,
        "title": "Session 1",
        "duration_seconds": 3600.0,
        "caption_type": "n/a",
    }
    defaults.update(kwargs)
    return make_info_yaml(**defaults)  # ty: ignore[invalid-argument-type]


def test_make_info_yaml_schema_version() -> None:
    """make_info_yaml sets schema_version: 1."""
    info = _default_info()
    assert info["schema_version"] == 1


def test_make_info_yaml_source_id_preserved() -> None:
    """source_id passed through unchanged."""
    info = _default_info(source_id="yt-abc12345")
    assert info["source_id"] == "yt-abc12345"


def test_make_info_yaml_session_date_null() -> None:
    """session_date defaults to null."""
    info = _default_info()
    assert info["session_date"] is None


def test_make_info_yaml_context_defaults() -> None:
    """Context fields default to None/empty."""
    info = _default_info()
    ctx = info["context"]
    assert ctx["perspective"] is None
    assert ctx["speakers"] == []


def test_write_and_read_roundtrip(tmp_path: Path) -> None:
    """write_info_yaml / read_info_yaml roundtrip preserves fields."""
    path = tmp_path / "sources" / "srt-abc123" / "info.yaml"
    info = _default_info(
        source_id="srt-abc1234567",
        source_url="https://example.com",
        title="My Session",
        duration_seconds=7200.0,
        caption_type="n/a",
    )
    write_info_yaml(path, info)
    loaded = read_info_yaml(path)
    assert loaded["source_id"] == info["source_id"]
    assert loaded["source_url"] == "https://example.com"
    assert loaded["title"] == "My Session"
    assert loaded["duration_seconds"] == pytest.approx(7200.0)
    assert loaded["caption_type"] == "n/a"


def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    """write_info_yaml creates parent directories."""
    path = tmp_path / "deep" / "path" / "info.yaml"
    write_info_yaml(path, _default_info())
    assert path.exists()


def test_written_file_has_schema_version(tmp_path: Path) -> None:
    """Written YAML contains schema_version: 1."""
    path = tmp_path / "info.yaml"
    write_info_yaml(path, _default_info())
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1


def test_read_info_yaml_schema_version_too_new_raises(tmp_path: Path) -> None:
    """Future schema_version raises SchemaVersionError."""
    path = tmp_path / "info.yaml"
    path.write_text("schema_version: 999\nsource_id: x\n", encoding="utf-8")
    with pytest.raises(SchemaVersionError):
        read_info_yaml(path)


def test_context_block_roundtrip(tmp_path: Path) -> None:
    """Context sub-fields survive write/read."""
    path = tmp_path / "info.yaml"
    info = _default_info()
    info["context"] = ContextBlock(
        perspective="player",
        source_nature="actual-play",
        setting="The Continent",
        speakers=["Alice", "Bob"],
        notes="important session",
    )
    write_info_yaml(path, info)
    loaded = read_info_yaml(path)
    ctx = loaded["context"]
    assert ctx["perspective"] == "player"
    assert ctx["speakers"] == ["Alice", "Bob"]
    assert ctx["notes"] == "important session"


def test_null_source_url_roundtrip(tmp_path: Path) -> None:
    """source_url: null preserved through write/read."""
    path = tmp_path / "info.yaml"
    info = _default_info(source_url=None)
    write_info_yaml(path, info)
    loaded = read_info_yaml(path)
    assert loaded["source_url"] is None


def test_fetched_at_rfc3339_format(tmp_path: Path) -> None:
    """fetched_at matches RFC 3339 UTC pattern."""
    import re  # noqa: PLC0415

    info = _default_info()
    path = tmp_path / "info.yaml"
    write_info_yaml(path, info)
    loaded = read_info_yaml(path)
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", loaded["fetched_at"])
