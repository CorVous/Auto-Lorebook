"""Tests for info_yaml.py (DB-backed)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

from auto_lorebook.info_yaml import (
    Info,
    InfoError,
    SourceContext,
    exists,
    list_source_ids,
    read,
    transcript_filename_for,
    write,
    write_yaml,
)


def _make_info(source_id: str = "txt-abc1234567") -> Info:
    return Info(
        source_id=source_id,
        source_type="text",
        fetched_at="2026-04-24T12:00:00Z",
        title="My Notes",
        context=SourceContext(perspective="test", source_nature="notes"),
    )


# ---------------------------------------------------------------------------
# transcript_filename_for
# ---------------------------------------------------------------------------


def test_transcript_filename_for_youtube() -> None:
    assert transcript_filename_for("youtube") == "transcript.en.srt"


def test_transcript_filename_for_srt() -> None:
    assert transcript_filename_for("srt") == "transcript.en.srt"


def test_transcript_filename_for_text() -> None:
    assert transcript_filename_for("text") == "transcript.txt"


def test_transcript_filename_for_markdown() -> None:
    assert transcript_filename_for("markdown") == "transcript.md"


# ---------------------------------------------------------------------------
# DB write and read
# ---------------------------------------------------------------------------


def test_write_then_read(db_conn: sqlite3.Connection) -> None:
    info = _make_info()
    write(db_conn, info)
    loaded = read(db_conn, info.source_id)
    assert loaded.source_id == info.source_id
    assert loaded.source_type == "text"
    assert loaded.title == "My Notes"


def test_write_upserts(db_conn: sqlite3.Connection) -> None:
    info = _make_info()
    write(db_conn, info)
    info.title = "Updated"
    write(db_conn, info)
    loaded = read(db_conn, info.source_id)
    assert loaded.title == "Updated"


def test_write_coerces_auto_caption_type(db_conn: sqlite3.Connection) -> None:
    info = _make_info()
    info.caption_type = "auto"
    write(db_conn, info)
    loaded = read(db_conn, info.source_id)
    assert loaded.caption_type == "auto-generated"


def test_write_preserves_context_fields(db_conn: sqlite3.Connection) -> None:
    info = _make_info()
    info.context.setting = "Aether"
    info.context.speakers = [{"name": "Cor", "role": "player"}]
    write(db_conn, info)
    loaded = read(db_conn, info.source_id)
    assert loaded.context.setting == "Aether"
    assert loaded.context.speakers[0]["name"] == "Cor"


def test_round_trip_null_fields(db_conn: sqlite3.Connection) -> None:
    info = Info(
        source_id="txt-1234567890",
        source_type="text",
        fetched_at="2026-04-24T00:00:00Z",
    )
    write(db_conn, info)
    loaded = read(db_conn, info.source_id)
    assert loaded.source_url is None
    assert loaded.session_date is None
    assert loaded.context.perspective is None


def test_read_missing_raises(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(InfoError, match="not found"):
        read(db_conn, "nonexistent-id")


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------


def test_exists_returns_true_when_present(db_conn: sqlite3.Connection) -> None:
    write(db_conn, _make_info())
    assert exists(db_conn, "txt-abc1234567") is True


def test_exists_returns_false_when_absent(db_conn: sqlite3.Connection) -> None:
    assert exists(db_conn, "txt-abc1234567") is False


def test_exists_backfills_from_yaml(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    src_dir = tmp_path / "sources" / "txt-abc1234567"
    src_dir.mkdir(parents=True)
    write_yaml(
        _make_info(),
        src_dir / "info.yaml",
    )
    assert exists(db_conn, "txt-abc1234567", wiki_repo=tmp_path) is True


# ---------------------------------------------------------------------------
# list_source_ids
# ---------------------------------------------------------------------------


def test_list_source_ids_empty(db_conn: sqlite3.Connection) -> None:
    assert list_source_ids(db_conn) == []


def test_list_source_ids_sorted(db_conn: sqlite3.Connection) -> None:
    write(db_conn, _make_info("yt-bbb"))
    write(db_conn, _make_info("txt-aaa"))
    assert list_source_ids(db_conn) == ["txt-aaa", "yt-bbb"]


def test_list_source_ids_backfills_when_empty(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    src_dir = tmp_path / "sources" / "txt-abc1234567"
    src_dir.mkdir(parents=True)
    write_yaml(_make_info(), src_dir / "info.yaml")
    ids = list_source_ids(db_conn, wiki_repo=tmp_path)
    assert "txt-abc1234567" in ids


# ---------------------------------------------------------------------------
# Lazy backfill from YAML on read()
# ---------------------------------------------------------------------------


def test_read_backfills_from_yaml(db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    src_dir = tmp_path / "sources" / "txt-abc1234567"
    src_dir.mkdir(parents=True)
    write_yaml(_make_info(), src_dir / "info.yaml")
    loaded = read(db_conn, "txt-abc1234567", wiki_repo=tmp_path)
    assert loaded.title == "My Notes"


def test_read_raises_if_yaml_also_missing(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    with pytest.raises(InfoError):
        read(db_conn, "txt-missing", wiki_repo=tmp_path)


def test_read_backfill_logs_warning_on_bad_yaml(
    db_conn: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    src_dir = tmp_path / "sources" / "txt-abc1234567"
    src_dir.mkdir(parents=True)
    (src_dir / "info.yaml").write_text("schema_version: 99\n", encoding="utf-8")
    with (
        caplog.at_level(logging.WARNING, logger="auto_lorebook.info_yaml"),
        pytest.raises(InfoError),
    ):
        read(db_conn, "txt-abc1234567", wiki_repo=tmp_path)
    assert "backfill skipped" in caplog.text


# ---------------------------------------------------------------------------
# write_yaml
# ---------------------------------------------------------------------------


def test_write_yaml_creates_file(tmp_path: Path) -> None:
    info = _make_info()
    path = tmp_path / "info.yaml"
    write_yaml(info, path)
    assert path.exists()


def test_write_yaml_schema_version_first(tmp_path: Path) -> None:
    path = tmp_path / "info.yaml"
    write_yaml(_make_info(), path)
    first_line = path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith("schema_version:")


def test_write_yaml_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "info.yaml"
    write_yaml(_make_info(), path)
    assert path.exists()
