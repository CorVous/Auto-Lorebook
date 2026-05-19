"""Tests for wiki_context.py (DB-backed)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    import pytest

from auto_lorebook.wiki_context import SettingContext, WikiContext, read, write

# ---------------------------------------------------------------------------
# read — empty / missing DB row
# ---------------------------------------------------------------------------


def test_read_returns_empty_wikicontent_when_no_yaml(
    db_conn: sqlite3.Connection,
) -> None:
    wc = read(db_conn)
    assert wc.setting.name is None
    assert wc.naming_conventions is None


def test_read_returns_empty_when_yaml_absent(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    wc = read(db_conn, wiki_repo=tmp_path)
    assert wc.setting.name is None


# ---------------------------------------------------------------------------
# write / round-trip
# ---------------------------------------------------------------------------


def test_write_then_read_round_trips(db_conn: sqlite3.Connection) -> None:
    ctx = WikiContext(
        setting=SettingContext(name="Aether Chronicles", description="High fantasy."),
        naming_conventions="Characters by first name",
        interpretation_defaults="DM is authoritative",
        recurring_speakers=[{"name": "Cor", "role": "player"}],
    )
    write(db_conn, ctx)
    loaded = read(db_conn)
    assert loaded.setting.name == "Aether Chronicles"
    assert loaded.setting.description == "High fantasy."
    assert loaded.naming_conventions == "Characters by first name"
    assert loaded.interpretation_defaults == "DM is authoritative"
    assert len(loaded.recurring_speakers) == 1
    assert loaded.recurring_speakers[0]["name"] == "Cor"


def test_write_overwrites_previous(db_conn: sqlite3.Connection) -> None:
    write(db_conn, WikiContext(setting=SettingContext(name="Old")))
    write(db_conn, WikiContext(setting=SettingContext(name="New")))
    assert read(db_conn).setting.name == "New"


def test_write_partial_fields(db_conn: sqlite3.Connection) -> None:
    write(db_conn, WikiContext(naming_conventions="By first name"))
    wc = read(db_conn)
    assert wc.setting.name is None
    assert wc.naming_conventions == "By first name"


# ---------------------------------------------------------------------------
# lazy backfill from YAML
# ---------------------------------------------------------------------------


def test_backfill_from_yaml_on_first_read(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / ".wiki-context.yaml").write_text(
        "schema_version: 1\nsetting:\n  name: Backfilled\n",
        encoding="utf-8",
    )
    wc = read(db_conn, wiki_repo=tmp_path)
    assert wc.setting.name == "Backfilled"


def test_backfill_runs_only_once(db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    (tmp_path / ".wiki-context.yaml").write_text(
        "schema_version: 1\nsetting:\n  name: First\n",
        encoding="utf-8",
    )
    read(db_conn, wiki_repo=tmp_path)
    # now mutate YAML — second read should NOT re-backfill (row is populated)
    (tmp_path / ".wiki-context.yaml").write_text(
        "schema_version: 1\nsetting:\n  name: Second\n",
        encoding="utf-8",
    )
    wc = read(db_conn, wiki_repo=tmp_path)
    assert wc.setting.name == "First"


def test_backfill_yaml_missing_schema_version_logs_warning(
    db_conn: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / ".wiki-context.yaml").write_text(
        "setting:\n  name: Aether Chronicles\n", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING):
        wc = read(db_conn, wiki_repo=tmp_path)
    assert wc.setting.name == "Aether Chronicles"
    assert "missing schema_version" in caplog.text


def test_backfill_malformed_yaml_returns_empty(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / ".wiki-context.yaml").write_text(":::invalid:::\n", encoding="utf-8")
    wc = read(db_conn, wiki_repo=tmp_path)
    assert wc.setting.name is None


def test_backfill_unknown_keys_ignored(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / ".wiki-context.yaml").write_text(
        "schema_version: 1\nfuture_field: something\n", encoding="utf-8"
    )
    wc = read(db_conn, wiki_repo=tmp_path)
    assert isinstance(wc, WikiContext)


def test_backfill_logs_info(
    db_conn: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / ".wiki-context.yaml").write_text(
        "schema_version: 1\nsetting:\n  name: Test\n", encoding="utf-8"
    )
    with caplog.at_level(logging.INFO, logger="auto_lorebook.wiki_context"):
        read(db_conn, wiki_repo=tmp_path)
    assert "backfilling" in caplog.text
