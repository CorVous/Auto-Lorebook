"""Tests for reading_review.py — reading review engine + Reviewer protocol."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from auto_lorebook import config as cfg_mod
from auto_lorebook import reading_pipeline as pipeline
from auto_lorebook import structure_store as structure_store_mod
from auto_lorebook.commands.approve_reading import AutoAcceptReviewer
from auto_lorebook.openrouter import OpenRouterResponse
from auto_lorebook.reading_review import (
    AcceptDecision,
    CommitDecision,
    RegenBatch,
    RegenerateAgainDecision,
    SegmentView,
    SkipBulletsDecision,
    UndoDecision,
    run,
)
from auto_lorebook.wiki_registry import WikiEntry
from tests._reading_fixtures import _info, _seed_ingest_in_db
from tests.test_reading_commands import _SRT, _wire_client_responses

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_SOURCE_ID = "yt-abc12345678"
_EMPTY_MARKER = "_No claims extracted from this segment._"

# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def _write_info(wiki: Path) -> None:
    src = wiki / "sources" / _SOURCE_ID
    src.mkdir(parents=True, exist_ok=True)
    info = _info()
    (src / "info.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "source_id": info.source_id,
                "source_type": info.source_type,
                "source_url": info.source_url,
                "title": info.title,
                "duration_seconds": info.duration_seconds,
                "fetched_at": info.fetched_at,
                "session_date": None,
                "transcript_filename": "transcript.en.srt",
                "caption_type": "manual",
                "context": {
                    "perspective": None,
                    "source_nature": None,
                    "setting": None,
                    "speakers": [],
                    "notes": None,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _seed_db(wiki: Path, sid: str = _SOURCE_ID) -> None:
    """Seed DB with sources + ingests + structure + bullets rows."""
    from auto_lorebook import db as db_mod  # noqa: PLC0415
    from auto_lorebook import wiki_state  # noqa: PLC0415

    conn = db_mod.open(wiki_state.wiki_db_path(wiki))
    try:
        _seed_ingest_in_db(conn, sid)
    finally:
        conn.close()


def _write_config(home: Path, wiki: Path) -> None:
    (home / "config.yaml").write_text(
        "schema_version: 2\nactive_wiki: test\nwikis:\n"
        f"- nickname: test\n  path: {wiki}\n",
        encoding="utf-8",
    )


@pytest.fixture
def env(
    tmp_path: Path,
    tmp_wiki: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[cfg_mod.Config, Path]:
    """Write config + info.yaml + DB rows; return (cfg, wiki)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    _write_config(home, tmp_wiki)
    cfg = cfg_mod.Config(wikis=[WikiEntry("test", tmp_wiki)], active_wiki="test")
    _write_info(tmp_wiki)
    _seed_db(tmp_wiki)
    return cfg, tmp_wiki


# ---------------------------------------------------------------------------
# Helpers to read DB state in tests
# ---------------------------------------------------------------------------


def _get_segment_status(wiki: Path, sid: str, segment_id: str) -> str:
    """Return segment_status from DB for the given segment_id."""
    from auto_lorebook import db as db_mod  # noqa: PLC0415
    from auto_lorebook import wiki_state  # noqa: PLC0415

    conn = db_mod.open(wiki_state.wiki_db_path(wiki))
    try:
        seg = structure_store_mod.get_segment(conn, sid, segment_id)
        return seg.segment_status if seg else "missing"
    finally:
        conn.close()


def _get_all_statuses(wiki: Path, sid: str) -> dict[str, str]:
    """Return {segment_id: segment_status} for all segments."""
    from auto_lorebook import db as db_mod  # noqa: PLC0415
    from auto_lorebook import wiki_state  # noqa: PLC0415

    conn = db_mod.open(wiki_state.wiki_db_path(wiki))
    try:
        rows = structure_store_mod.list_segments(conn, sid)
        return {r.segment_id: r.segment_status for r in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Scripted reviewer
# ---------------------------------------------------------------------------

_DEFAULT_COMMIT = CommitDecision()

_DecisionList = list[
    AcceptDecision | SkipBulletsDecision | RegenerateAgainDecision | UndoDecision
]


class ScriptedReviewer:
    """Feeds a pre-defined list of decisions; commit is configurable."""

    by_label = "scripted"

    def __init__(
        self,
        decisions: _DecisionList,
        *,
        abort: bool = False,
    ) -> None:
        self._decisions = list(decisions)
        self._commit: CommitDecision | None = None if abort else _DEFAULT_COMMIT
        self._idx = 0

    def decide_segment(
        self,
        view: SegmentView,  # noqa: ARG002
    ) -> AcceptDecision | SkipBulletsDecision | RegenerateAgainDecision | UndoDecision:
        decision = self._decisions[self._idx]
        self._idx += 1
        return decision

    def decide_quit(
        self,
        pending: tuple[SegmentView, ...],  # noqa: ARG002
    ) -> CommitDecision | None:
        return self._commit


def _scripted(
    decisions: _DecisionList,
    *,
    abort: bool = False,
) -> ScriptedReviewer:
    """Build a ScriptedReviewer; pass abort=True to make decide_quit return None."""
    return ScriptedReviewer(decisions, abort=abort)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAcceptAll:
    def test_fires_gate_and_writes_wiki_reading(
        self, env: tuple[cfg_mod.Config, Path]
    ) -> None:
        cfg, wiki = env
        reviewer = _scripted([AcceptDecision(), AcceptDecision(), AcceptDecision()])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        assert result.gate_fired is True
        assert result.wiki_reading_path is not None
        assert result.wiki_reading_path.exists()
        assert result.accepted == 3
        assert result.skipped == 0
        assert result.regenerating == 0
        # DB shows all accepted
        statuses = _get_all_statuses(wiki, _SOURCE_ID)
        assert all(s == "accepted" for s in statuses.values())
        # wiki reading.md has expected content
        text = result.wiki_reading_path.read_text(encoding="utf-8")
        assert "# Reading: Session 3" in text
        assert "reading_status" not in text


class TestSkipBullets:
    def test_empty_marker_rendered_in_assembly(
        self, env: tuple[cfg_mod.Config, Path]
    ) -> None:
        cfg, wiki = env
        reviewer = _scripted([
            AcceptDecision(),
            SkipBulletsDecision(),
            AcceptDecision(),
        ])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        assert result.gate_fired is True
        assert result.skipped == 1
        assert result.accepted == 2

        # seg-002 DB status: skipped
        assert _get_segment_status(wiki, _SOURCE_ID, "seg-002") == "skipped"

        # assembled wiki reading contains the empty marker for seg-002
        assert result.wiki_reading_path is not None
        text = result.wiki_reading_path.read_text(encoding="utf-8")
        assert _EMPTY_MARKER in text


class TestRegeneratingBlocksGate:
    def test_gate_does_not_fire(self, env: tuple[cfg_mod.Config, Path]) -> None:
        cfg, wiki = env
        reviewer = _scripted([
            AcceptDecision(),
            RegenerateAgainDecision(),
            AcceptDecision(),
        ])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        assert result.gate_fired is False
        assert result.wiki_reading_path is None
        assert result.regenerating == 1

        assert _get_segment_status(wiki, _SOURCE_ID, "seg-002") == "regenerating"
        assert _get_segment_status(wiki, _SOURCE_ID, "seg-001") == "accepted"


class TestUndoSegmentScoped:
    def test_undo_clears_one_segment_only(
        self, env: tuple[cfg_mod.Config, Path]
    ) -> None:
        cfg, wiki = env
        # UndoDecision for seg-002's slot — its pending mark is cleared
        reviewer = _scripted([AcceptDecision(), UndoDecision(), AcceptDecision()])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        # seg-002 stays draft (no pending mark → unchanged)
        assert _get_segment_status(wiki, _SOURCE_ID, "seg-002") == "draft"

        # gate does not fire — seg-002 still draft
        assert result.gate_fired is False
        assert result.accepted == 2
        assert result.unchanged == 1

        for seg_id in ("seg-001", "seg-003"):
            assert _get_segment_status(wiki, _SOURCE_ID, seg_id) == "accepted"


class TestEngineWritesNothingUntilCommit:
    def test_db_unchanged_during_walk(self, env: tuple[cfg_mod.Config, Path]) -> None:
        cfg, wiki = env
        statuses_before = _get_all_statuses(wiki, _SOURCE_ID)
        statuses_mid: dict[str, str] = {}
        captured = False

        class SnapshotReviewer:
            by_label = "snapshot"

            def decide_segment(
                self,
                view: SegmentView,  # noqa: ARG002
            ) -> AcceptDecision:
                return AcceptDecision()

            def decide_quit(
                self,
                pending: tuple[SegmentView, ...],  # noqa: ARG002
            ) -> CommitDecision:
                nonlocal statuses_mid, captured
                statuses_mid = _get_all_statuses(wiki, _SOURCE_ID)
                captured = True
                return CommitDecision()

        run(cfg=cfg, source_id=_SOURCE_ID, reviewer=SnapshotReviewer())
        assert captured
        # During the walk (before decide_quit returns), DB is still draft
        assert statuses_mid == statuses_before


class TestAbortOnNoneCommit:
    def test_no_files_written_when_quit_returns_none(
        self, env: tuple[cfg_mod.Config, Path]
    ) -> None:
        cfg, tmp_wiki = env
        reviewer = _scripted(
            [AcceptDecision(), AcceptDecision(), AcceptDecision()], abort=True
        )
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        assert result.gate_fired is False
        assert result.wiki_reading_path is None
        assert not (tmp_wiki / "sources" / _SOURCE_ID / "reading.md").exists()
        # DB statuses still draft (no commit happened)
        statuses = _get_all_statuses(tmp_wiki, _SOURCE_ID)
        assert all(s == "draft" for s in statuses.values())


class TestYesShortcut:
    def test_auto_accept_reviewer_fires_gate(
        self, env: tuple[cfg_mod.Config, Path]
    ) -> None:
        cfg, _ = env
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=AutoAcceptReviewer())

        assert result.gate_fired is True
        assert result.wiki_reading_path is not None
        assert result.wiki_reading_path.exists()
        text = result.wiki_reading_path.read_text(encoding="utf-8")
        assert "# Reading: Session 3" in text
        assert "reading_status" not in text

    def test_pipeline_approve_uses_auto_accept(
        self,
        tmp_path: Path,
        tmp_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """reading_pipeline.approve → AutoAcceptReviewer chain e2e."""
        from auto_lorebook.commands import (  # noqa: PLC0415
            approve_reading as approve_reading_cmd,
        )
        from auto_lorebook.commands import (  # noqa: PLC0415
            generate_reading as generate_reading_cmd,
        )

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
        (home / "config.yaml").write_text(
            f"schema_version: 2\nactive_wiki: main\n"
            f"wikis:\n- nickname: main\n  path: {tmp_wiki}\n"
            "openrouter:\n  api_key_env: FAKE_OR_KEY\n"
            "models:\n  primary: anthropic/claude-sonnet-4-5\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        src_dir = tmp_wiki / "sources" / _SOURCE_ID
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "transcript.en.srt").write_text(_SRT, encoding="utf-8")
        _write_info(tmp_wiki)

        client = MagicMock()
        _wire_client_responses(client)

        def _args(**kwargs: object) -> argparse.Namespace:
            return argparse.Namespace(**kwargs)

        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id=_SOURCE_ID))
            rc = approve_reading_cmd.run(_args(source_id=_SOURCE_ID, yes=True))

        assert rc == 0
        approved = tmp_wiki / "sources" / _SOURCE_ID / "reading.md"
        assert approved.exists()
        text = approved.read_text(encoding="utf-8")
        assert "# Reading:" in text
        assert "reading_status" not in text


class TestPureNoInputOrPrint:
    def test_engine_module_has_no_input_or_print(self) -> None:
        """reading_review.py must have no input() or print() calls."""
        import ast  # noqa: PLC0415

        src = (
            Path(__file__).resolve().parent.parent
            / "src"
            / "auto_lorebook"
            / "reading_review.py"
        )
        tree = ast.parse(src.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in {"input", "print"}:
                    msg = (
                        f"reading_review.py calls {func.id}() at line {node.lineno}"
                        " — engine must be pure-logic (no I/O)"
                    )
                    raise AssertionError(msg)


class TestRegenBatchAtCommit:
    """Engine emits RegenBatch when at least one segment is regenerating."""

    def test_regen_decision_produces_regen_batch(
        self, env: tuple[cfg_mod.Config, Path]
    ) -> None:
        cfg, _ = env
        # seg-001 accepted, seg-002 regen, seg-003 accepted
        reviewer = _scripted([
            AcceptDecision(),
            RegenerateAgainDecision(),
            AcceptDecision(),
        ])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        assert result.gate_fired is False
        assert result.wiki_reading_path is None
        assert result.regen_batch is not None
        assert isinstance(result.regen_batch, RegenBatch)
        assert result.regen_batch.regen_segment_ids == ("seg-002",)
        assert result.regen_batch.source_id == _SOURCE_ID

    def test_regen_batch_accepted_context_includes_in_quit_commits(
        self, env: tuple[cfg_mod.Config, Path]
    ) -> None:
        cfg, _ = env
        # seg-001 accepted, seg-002 regen, seg-003 accepted
        reviewer = _scripted([
            AcceptDecision(),
            RegenerateAgainDecision(),
            AcceptDecision(),
        ])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        assert result.regen_batch is not None
        ctx = result.regen_batch.accepted_context
        # only accepted segments in context
        ids = tuple(e.segment_id for e in ctx)
        assert ids == ("seg-001", "seg-003")
        # bodies present
        for entry in ctx:
            assert entry.bullets_body

    def test_regen_batch_accepted_context_excludes_skipped(
        self, env: tuple[cfg_mod.Config, Path]
    ) -> None:
        cfg, _ = env
        # seg-001 skipped, seg-002 regen, seg-003 accepted
        reviewer = _scripted([
            SkipBulletsDecision(),
            RegenerateAgainDecision(),
            AcceptDecision(),
        ])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        assert result.regen_batch is not None
        ids = tuple(e.segment_id for e in result.regen_batch.accepted_context)
        # seg-001 is skipped → not in accepted_context; seg-003 is accepted
        assert ids == ("seg-003",)

    def test_regen_batch_none_when_no_regenerating_segments(
        self, env: tuple[cfg_mod.Config, Path]
    ) -> None:
        cfg, _ = env
        reviewer = _scripted([
            AcceptDecision(),
            AcceptDecision(),
            AcceptDecision(),
        ])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        assert result.regen_batch is None


class TestRegenAfterReviewIntegrates:
    """End-to-end: generate → review → regen → back to draft."""

    def test_regenerated_segment_returns_to_draft(
        self,
        tmp_path: Path,
        tmp_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from auto_lorebook.commands import (  # noqa: PLC0415
            generate_reading as generate_reading_cmd,
        )

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
        (home / "config.yaml").write_text(
            f"schema_version: 2\nactive_wiki: main\n"
            f"wikis:\n- nickname: main\n  path: {tmp_wiki}\n"
            "openrouter:\n  api_key_env: FAKE_OR_KEY\n"
            "models:\n  primary: anthropic/claude-sonnet-4-5\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        source_id = "yt-abc12345678"
        src_dir = tmp_wiki / "sources" / source_id
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "transcript.en.srt").write_text(_SRT, encoding="utf-8")
        _write_info(tmp_wiki)

        client = MagicMock()
        _wire_client_responses(client)

        def _args(**kwargs: object) -> argparse.Namespace:
            return argparse.Namespace(**kwargs)

        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id=source_id))

        # Mark seg-001 accepted, seg-002 regenerating via review engine
        from auto_lorebook import db as db_mod  # noqa: PLC0415
        from auto_lorebook import wiki_state  # noqa: PLC0415

        conn = db_mod.open(wiki_state.wiki_db_path(tmp_wiki))
        try:
            structure_store_mod.set_segment_status(
                conn, source_id, "seg-001", "accepted"
            )
            structure_store_mod.set_segment_status(
                conn, source_id, "seg-002", "regenerating"
            )
            conn.commit()
        finally:
            conn.close()

        # Build regen batch + call regenerate_after_review
        from auto_lorebook import reading_review as rr_mod  # noqa: PLC0415
        from auto_lorebook.stage1b import AcceptedContextEntry  # noqa: PLC0415

        batch = rr_mod.RegenBatch(
            source_id=source_id,
            regen_segment_ids=("seg-002",),
            accepted_context=(
                AcceptedContextEntry(
                    segment_id="seg-001",
                    start=0.0,
                    end=120.0,
                    title="Intro",
                    speaker="DM",
                    bullets_body="_No claims extracted from this segment._\n",
                ),
            ),
        )

        cfg = cfg_mod.load_config()

        regen_client = MagicMock()
        regen_client.complete.return_value = OpenRouterResponse(
            text=json.dumps({
                "bullets": [{"text": "Regen bullet", "anchor": "0:02:30"}]
            }),
            model="m",
            tokens_in=0,
            tokens_out=0,
        )

        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=regen_client
        ):
            pipeline.regenerate_after_review(cfg, batch)

        # seg-002 should be draft after regen
        conn2 = db_mod.open(wiki_state.wiki_db_path(tmp_wiki))
        try:
            seg002 = structure_store_mod.get_segment(conn2, source_id, "seg-002")
            assert seg002 is not None
            assert seg002.segment_status == "draft"
            # bullets rewritten
            bullets_map = structure_store_mod.read_bullets(conn2, source_id)
            seg002_bullets = bullets_map.segments.get("seg-002", [])
            assert any("Regen bullet" in b.text for b in seg002_bullets)
        finally:
            conn2.close()

        # no wiki reading.md written (gate not fired)
        wiki_reading = tmp_wiki / "sources" / source_id / "reading.md"
        assert not wiki_reading.exists()
