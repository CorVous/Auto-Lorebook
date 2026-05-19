"""Tests for corrections.py (DB-backed)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    import pytest

from auto_lorebook.corrections import add, add_also_seen_in, read


def _seed_source(conn: sqlite3.Connection, source_id: str = "yt-abc123") -> None:
    """Insert a minimal sources row so FK constraints pass."""
    conn.execute(
        "INSERT OR IGNORE INTO sources(source_id, source_type, fetched_at)"
        " VALUES (?, 'youtube', '2026-01-01T00:00:00+00:00')",
        (source_id,),
    )


# ---------------------------------------------------------------------------
# read — empty DB / missing YAML
# ---------------------------------------------------------------------------


def test_read_empty_db_no_yaml(db_conn: sqlite3.Connection) -> None:
    cors = read(db_conn)
    assert cors.corrections == []


def test_read_empty_db_yaml_absent(db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    cors = read(db_conn, wiki_repo=tmp_path)
    assert cors.corrections == []


# ---------------------------------------------------------------------------
# add / add_also_seen_in
# ---------------------------------------------------------------------------


def test_add_inserts_correction(db_conn: sqlite3.Connection) -> None:
    _seed_source(db_conn)
    cor = add(db_conn, wrong="Aldera", right="Aldara", first_seen_in="yt-abc123")
    assert cor.wrong == "Aldera"
    assert cor.right == "Aldara"
    assert cor.first_seen_in == "yt-abc123"


def test_add_idempotent(db_conn: sqlite3.Connection) -> None:
    _seed_source(db_conn)
    c1 = add(db_conn, wrong="Aldera", right="Aldara", first_seen_in="yt-abc123")
    c2 = add(db_conn, wrong="Aldera", right="Aldara", first_seen_in="yt-abc123")
    assert c1.wrong == c2.wrong


def test_add_also_seen_in(db_conn: sqlite3.Connection) -> None:
    _seed_source(db_conn)
    _seed_source(db_conn, "yt-def456")
    add(db_conn, wrong="Aldera", right="Aldara", first_seen_in="yt-abc123")
    add_also_seen_in(db_conn, wrong="Aldera", right="Aldara", source_id="yt-def456")
    cors = read(db_conn)
    assert "yt-def456" in cors.corrections[0].also_seen_in


def test_add_also_seen_in_idempotent(db_conn: sqlite3.Connection) -> None:
    _seed_source(db_conn)
    _seed_source(db_conn, "yt-def456")
    add(db_conn, wrong="X", right="Y", first_seen_in="yt-abc123")
    add_also_seen_in(db_conn, wrong="X", right="Y", source_id="yt-def456")
    add_also_seen_in(db_conn, wrong="X", right="Y", source_id="yt-def456")
    cors = read(db_conn)
    assert cors.corrections[0].also_seen_in.count("yt-def456") == 1


# ---------------------------------------------------------------------------
# read — returns corrections sorted by (from_text, to_text)
# ---------------------------------------------------------------------------


def test_read_returns_sorted(db_conn: sqlite3.Connection) -> None:
    _seed_source(db_conn)
    add(db_conn, wrong="Zebra", right="Zara", first_seen_in="yt-abc123")
    add(db_conn, wrong="Alpha", right="Alfa", first_seen_in="yt-abc123")
    cors = read(db_conn)
    wrongs = [c.wrong for c in cors.corrections]
    assert wrongs == sorted(wrongs)


# ---------------------------------------------------------------------------
# backfill from YAML
# ---------------------------------------------------------------------------


def test_backfill_from_yaml_success(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    _seed_source(db_conn)
    (tmp_path / ".transcription-corrections.yaml").write_text(
        "schema_version: 1\n"
        "corrections:\n"
        "  - wrong: Aldera\n"
        "    right: Aldara\n"
        "    first_seen_in: yt-abc123\n",
        encoding="utf-8",
    )
    cors = read(db_conn, wiki_repo=tmp_path)
    assert len(cors.corrections) == 1
    assert cors.corrections[0].wrong == "Aldera"


def test_backfill_skips_bad_fk(
    db_conn: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / ".transcription-corrections.yaml").write_text(
        "schema_version: 1\n"
        "corrections:\n"
        "  - wrong: Aldera\n"
        "    right: Aldara\n"
        "    first_seen_in: yt-nonexistent\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="auto_lorebook.corrections"):
        cors = read(db_conn, wiki_repo=tmp_path)
    assert cors.corrections == []
    assert "skipping" in caplog.text


def test_backfill_skips_null_first_seen_in(
    db_conn: sqlite3.Connection, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / ".transcription-corrections.yaml").write_text(
        "schema_version: 1\ncorrections:\n  - wrong: Aldera\n    right: Aldara\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="auto_lorebook.corrections"):
        cors = read(db_conn, wiki_repo=tmp_path)
    assert cors.corrections == []


def test_backfill_yaml_malformed_returns_empty(
    db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    (tmp_path / ".transcription-corrections.yaml").write_text(
        ":::invalid:::\n", encoding="utf-8"
    )
    cors = read(db_conn, wiki_repo=tmp_path)
    assert cors.corrections == []


def test_backfill_runs_only_once(db_conn: sqlite3.Connection, tmp_path: Path) -> None:
    """DB non-empty → backfill doesn't run again."""
    _seed_source(db_conn)
    add(db_conn, wrong="A", right="B", first_seen_in="yt-abc123")
    # even if yaml has more corrections, they're not added because DB has rows
    (tmp_path / ".transcription-corrections.yaml").write_text(
        "schema_version: 1\ncorrections:\n  - wrong: C\n    right: D\n"
        "    first_seen_in: yt-abc123\n",
        encoding="utf-8",
    )
    cors = read(db_conn, wiki_repo=tmp_path)
    # DB already had data so no backfill
    wrongs = {c.wrong for c in cors.corrections}
    assert "C" not in wrongs
