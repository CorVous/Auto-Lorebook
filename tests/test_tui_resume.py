"""Unit tests for tui.resume.detect_stage — all artifact/tombstone permutations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook.tui.resume import detect_stage
from auto_lorebook.tui.state import Stage

if TYPE_CHECKING:
    from pathlib import Path


_SOURCE_ID = "yt-abc12345678"
_SOURCE_URL = "https://www.youtube.com/watch?v=abc12345678"

_MINIMAL_READING_DRAFT = """\
---
reading_status: draft
source_id: yt-abc12345678
---

# Reading: Test
"""

_MINIMAL_READING_APPROVED = """\
---
reading_status: approved
source_id: yt-abc12345678
---

# Reading: Test
"""


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(h))
    return h


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    w = tmp_path / "wiki"
    w.mkdir()
    (w / ".wiki-context.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    for cat in ("characters", "locations", "factions", "events", "items", "concepts"):
        (w / cat).mkdir()
    return w


def _src_dir(wiki: Path) -> Path:
    d = wiki / "sources" / _SOURCE_ID
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pending_root(home: Path) -> Path:
    return home / "pending" / _SOURCE_ID


def _write_info(wiki: Path, source_url: str | None = _SOURCE_URL) -> None:
    src_dir = _src_dir(wiki)
    info: dict = {
        "schema_version": 1,
        "source_id": _SOURCE_ID,
        "source_type": "youtube",
        "source_url": source_url,
        "title": "Test",
        "fetched_at": "2026-01-01T00:00:00Z",
        "transcript_filename": "transcript.en.srt",
        "context": {
            "perspective": None,
            "source_nature": None,
            "setting": None,
            "speakers": [],
            "notes": None,
        },
    }
    (src_dir / "info.yaml").write_text(yaml.safe_dump(info), encoding="utf-8")


def _write_transcript(wiki: Path) -> None:
    _src_dir(wiki)
    (wiki / "sources" / _SOURCE_ID / "transcript.en.srt").write_text(
        "1\n00:00:00,000 --> 00:00:05,000\nHello.\n", encoding="utf-8"
    )


def _write_context_set(home: Path) -> None:
    root = _pending_root(home)
    root.mkdir(parents=True, exist_ok=True)
    (root / "context.set").touch()


def _write_reading(home: Path, *, approved: bool = False) -> None:
    reading_dir = _pending_root(home) / "reading"
    reading_dir.mkdir(parents=True, exist_ok=True)
    text = _MINIMAL_READING_APPROVED if approved else _MINIMAL_READING_DRAFT
    (reading_dir / "reading.md").write_text(text, encoding="utf-8")


def _write_plan(home: Path) -> None:
    root = _pending_root(home)
    root.mkdir(parents=True, exist_ok=True)
    (root / "plan.yaml").write_text("schema_version: 1\n", encoding="utf-8")


def _write_proposals_dir(home: Path, *, empty: bool = False) -> None:
    proposals = _pending_root(home) / "proposals"
    proposals.mkdir(parents=True, exist_ok=True)
    if not empty:
        (proposals / "test-f001.yaml").write_text(
            "schema_version: 1\n", encoding="utf-8"
        )


def _write_review_done(home: Path) -> None:
    root = _pending_root(home)
    root.mkdir(parents=True, exist_ok=True)
    (root / "review.done").touch()


# --- Stage detection tests ---------------------------------------------------


class TestDetectStage:
    @pytest.mark.usefixtures("home")
    def test_no_transcript_returns_ingest(self, wiki: Path) -> None:
        # nothing on disk
        assert detect_stage(_SOURCE_ID, wiki) == Stage.INGEST

    @pytest.mark.usefixtures("home")
    def test_transcript_present_no_context_set_returns_context(
        self, wiki: Path
    ) -> None:
        _write_transcript(wiki)
        assert detect_stage(_SOURCE_ID, wiki) == Stage.CONTEXT

    def test_context_set_no_reading_returns_reading_gen(
        self, home: Path, wiki: Path
    ) -> None:
        _write_transcript(wiki)
        _write_context_set(home)
        assert detect_stage(_SOURCE_ID, wiki) == Stage.READING_GEN

    def test_draft_reading_returns_reading_gate(self, home: Path, wiki: Path) -> None:
        _write_transcript(wiki)
        _write_context_set(home)
        _write_reading(home, approved=False)
        assert detect_stage(_SOURCE_ID, wiki) == Stage.READING_GATE

    def test_approved_reading_no_plan_returns_plan(
        self, home: Path, wiki: Path
    ) -> None:
        _write_transcript(wiki)
        _write_context_set(home)
        _write_reading(home, approved=True)
        # Need wiki reading.md approved too (plan() checks wiki path)
        _write_info(wiki)
        wiki_reading = wiki / "sources" / _SOURCE_ID / "reading.md"
        wiki_reading.write_text(_MINIMAL_READING_APPROVED, encoding="utf-8")
        assert detect_stage(_SOURCE_ID, wiki) == Stage.PLAN

    def test_plan_no_proposals_returns_extract(self, home: Path, wiki: Path) -> None:
        _write_transcript(wiki)
        _write_context_set(home)
        _write_reading(home, approved=True)
        _write_plan(home)
        assert detect_stage(_SOURCE_ID, wiki) == Stage.EXTRACT

    def test_proposals_dir_exists_no_review_done_returns_review_gate(
        self, home: Path, wiki: Path
    ) -> None:
        _write_transcript(wiki)
        _write_context_set(home)
        _write_reading(home, approved=True)
        _write_plan(home)
        _write_proposals_dir(home)
        assert detect_stage(_SOURCE_ID, wiki) == Stage.REVIEW_GATE

    def test_review_done_tombstone_returns_done(self, home: Path, wiki: Path) -> None:
        _write_transcript(wiki)
        _write_context_set(home)
        _write_reading(home, approved=True)
        _write_plan(home)
        _write_proposals_dir(home)
        _write_review_done(home)
        assert detect_stage(_SOURCE_ID, wiki) == Stage.DONE

    def test_empty_proposals_dir_routes_to_review_gate(
        self, home: Path, wiki: Path
    ) -> None:
        # extract ran and produced 0 proposals → REVIEW_GATE (review.run short-circuits)
        _write_transcript(wiki)
        _write_context_set(home)
        _write_reading(home, approved=True)
        _write_plan(home)
        _write_proposals_dir(home, empty=True)
        assert detect_stage(_SOURCE_ID, wiki) == Stage.REVIEW_GATE

    @pytest.mark.usefixtures("home")
    def test_context_already_populated_still_enters_context_once(
        self, wiki: Path
    ) -> None:
        # ingest --no-interactive wrote info.yaml but context.set tombstone absent
        _write_transcript(wiki)
        _write_info(wiki)
        # No context.set → must still enter CONTEXT
        assert detect_stage(_SOURCE_ID, wiki) == Stage.CONTEXT
