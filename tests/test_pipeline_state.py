"""Tests for pipeline_state.first_missing_stage."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from auto_lorebook.pipeline_state import Stage, first_missing_stage

if TYPE_CHECKING:
    from pathlib import Path


def _cfg(wiki: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.resolve_active_wiki.return_value = wiki
    return cfg


# ---------------------------------------------------------------------------
# Helpers to create detector artifacts
# ---------------------------------------------------------------------------


def _mk_info_yaml(wiki: Path, sid: str) -> None:
    d = wiki / "sources" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "info.yaml").write_text(
        "schema_version: 1\n"
        f"source_id: {sid}\n"
        "source_type: text\n"
        "fetched_at: '2026-01-01T00:00:00Z'\n",
        encoding="utf-8",
    )


def _mk_reading_sidecar(wiki: Path, sid: str) -> None:
    """Seed bare ingests row in wiki DB — mirrors what `ingest` writes.

    `ingest` creates this row via `record_in_db`; it does NOT mean the
    reading has been generated. Generate-reading is detected by segments.
    """
    from auto_lorebook import db as db_mod  # noqa: PLC0415
    from auto_lorebook import wiki_state  # noqa: PLC0415

    conn = db_mod.open(wiki_state.wiki_db_path(wiki))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sources "
            "(source_id, source_type, fetched_at, context_json) VALUES (?,?,?,?)",
            (sid, "text", "2026-01-01T00:00:00Z", "{}"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ingests "
            "(ingest_id, source_id, started_at, state, default_speaker, "
            " name_corrections_json, session_date) "
            "VALUES (?,?,?,'reading',NULL,'{}',NULL)",
            (sid, sid, "2026-01-01T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()


def _mk_structure(wiki: Path, sid: str) -> None:
    """Seed a segments row (Stage 1a output) — generate-reading done."""
    from auto_lorebook import db as db_mod  # noqa: PLC0415
    from auto_lorebook import wiki_state  # noqa: PLC0415

    _mk_reading_sidecar(wiki, sid)
    conn = db_mod.open(wiki_state.wiki_db_path(wiki))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO segments "
            "(ingest_id, segment_id, start, end, title) VALUES (?,?,?,?,?)",
            (sid, "seg-001", "0:00:00", "0:01:00", "Opening"),
        )
        conn.commit()
    finally:
        conn.close()


def _mk_wiki_reading(wiki: Path, sid: str) -> None:
    """Create wiki-side sources/<sid>/reading.md."""
    d = wiki / "sources" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "reading.md").write_text("# reading\n", encoding="utf-8")


def _mk_plan_yaml(wiki: Path, sid: str) -> None:
    """Create pending/<sid>/plan.yaml."""
    d = wiki / ".wiki-state" / "pending" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.yaml").write_text("schema_version: 1\n", encoding="utf-8")


def _mk_proposal(wiki: Path, sid: str, name: str = "prop-001") -> None:
    """Create a proposal file in pending/<sid>/proposals/."""
    d = wiki / ".wiki-state" / "pending" / sid / "proposals"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text("schema_version: 1\n", encoding="utf-8")


def _mk_proposals_dir_empty(wiki: Path, sid: str) -> None:
    """Create pending/<sid>/proposals/ (empty)."""
    d = wiki / ".wiki-state" / "pending" / sid / "proposals"
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Parameterised detector matrix
# ---------------------------------------------------------------------------

SID = "yt-abc12345678"


@pytest.mark.parametrize(
    ("setup_fns", "expected"),
    [
        # nothing present → INGEST
        ([], Stage.INGEST),
        # info.yaml only → GENERATE_READING
        ([_mk_info_yaml], Stage.GENERATE_READING),
        # info.yaml + bare ingests row (post-ingest) → still GENERATE_READING
        ([_mk_info_yaml, _mk_reading_sidecar], Stage.GENERATE_READING),
        # info.yaml + segments (reading generated) → APPROVE_READING
        ([_mk_info_yaml, _mk_structure], Stage.APPROVE_READING),
        # info.yaml + segments + wiki reading.md → PLAN
        ([_mk_info_yaml, _mk_structure, _mk_wiki_reading], Stage.PLAN),
        # info.yaml + wiki reading.md + plan.yaml + absent proposals dir → EXTRACT
        (
            [_mk_info_yaml, _mk_wiki_reading, _mk_plan_yaml],
            Stage.EXTRACT,
        ),
        # info.yaml + wiki reading.md + plan.yaml + non-empty proposals → REVIEW
        (
            [_mk_info_yaml, _mk_wiki_reading, _mk_plan_yaml, _mk_proposal],
            Stage.REVIEW,
        ),
        # info.yaml + wiki reading.md + plan.yaml + empty proposals dir → None
        (
            [_mk_info_yaml, _mk_wiki_reading, _mk_plan_yaml, _mk_proposals_dir_empty],
            None,
        ),
    ],
    ids=[
        "empty→INGEST",
        "info.yaml→GENERATE_READING",
        "bare-ingests→GENERATE_READING",
        "structure→APPROVE_READING",
        "structure+wiki-reading→PLAN",
        "plan+absent-proposals→EXTRACT",
        "plan+proposals→REVIEW",
        "plan+empty-proposals→None",
    ],
)
def test_first_missing_stage(
    tmp_wiki: Path,
    setup_fns: list,
    expected: Stage,
) -> None:
    for fn in setup_fns:
        fn(tmp_wiki, SID)
    cfg = _cfg(tmp_wiki)
    result = first_missing_stage(cfg, SID, wiki_override=None)
    assert result == expected


def test_all_done_returns_none(tmp_wiki: Path) -> None:
    """plan.yaml + empty proposals dir → None (review complete)."""
    _mk_info_yaml(tmp_wiki, SID)
    _mk_reading_sidecar(tmp_wiki, SID)
    _mk_wiki_reading(tmp_wiki, SID)
    _mk_plan_yaml(tmp_wiki, SID)
    _mk_proposals_dir_empty(tmp_wiki, SID)
    cfg = _cfg(tmp_wiki)
    result = first_missing_stage(cfg, SID, wiki_override=None)
    assert result is None


def test_none_when_fully_complete(tmp_wiki: Path) -> None:
    """After review empties proposals dir, pipeline returns None."""
    _mk_info_yaml(tmp_wiki, SID)
    _mk_reading_sidecar(tmp_wiki, SID)
    _mk_wiki_reading(tmp_wiki, SID)
    _mk_plan_yaml(tmp_wiki, SID)
    _mk_proposals_dir_empty(tmp_wiki, SID)
    cfg = _cfg(tmp_wiki)
    result = first_missing_stage(cfg, SID, wiki_override=None)
    assert result is None


def test_wiki_override_forwarded(tmp_path: Path) -> None:
    """wiki_override is passed to cfg.resolve_active_wiki."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    cfg = MagicMock()
    cfg.resolve_active_wiki.return_value = wiki
    first_missing_stage(cfg, SID, wiki_override="alt")
    cfg.resolve_active_wiki.assert_called_once_with("alt")
