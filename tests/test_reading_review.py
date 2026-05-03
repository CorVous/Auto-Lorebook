"""Tests for reading_review.py — reading review engine + Reviewer protocol."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from auto_lorebook import config as cfg_mod
from auto_lorebook import reading_pipeline as pipeline
from auto_lorebook import segment_file as segment_file_mod
from auto_lorebook.commands.approve_reading import AutoAcceptReviewer
from auto_lorebook.reading_review import (
    AcceptDecision,
    CommitDecision,
    RegenerateAgainDecision,
    SegmentView,
    SkipBulletsDecision,
    UndoDecision,
    run,
)
from tests._reading_fixtures import _info, _segment_files, _sidecar
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
                "transcript_filename": info.transcript_filename,
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


def _write_pending() -> None:
    """Write sidecar + three segment files under pending."""
    sc = _sidecar()
    sidecar_path = pipeline.pending_sidecar_path(_SOURCE_ID)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "default_speaker": sc.default_speaker,
                "name_corrections": sc.name_corrections,
                "session_date": sc.session_date,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    segs_dir = pipeline.pending_segments_dir(_SOURCE_ID)
    segs_dir.mkdir(parents=True, exist_ok=True)
    for sf in _segment_files():
        segment_file_mod.write(sf, segs_dir / f"{sf.frontmatter.segment_id}.md")


@pytest.fixture
def env(
    tmp_path: Path,
    tmp_wiki: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[cfg_mod.Config, Path]:
    """Write info.yaml and pending segment files; return (cfg, wiki)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    cfg = cfg_mod.Config(wiki_repo_path=tmp_wiki)
    _write_info(tmp_wiki)
    _write_pending()
    return cfg, tmp_wiki


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
        cfg, _ = env
        reviewer = _scripted([AcceptDecision(), AcceptDecision(), AcceptDecision()])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        assert result.gate_fired is True
        assert result.wiki_reading_path is not None
        assert result.wiki_reading_path.exists()
        assert result.accepted == 3
        assert result.skipped == 0
        assert result.regenerating == 0
        # segment files on disk show accepted
        segs_dir = pipeline.pending_segments_dir(_SOURCE_ID)
        for sf_path in sorted(segs_dir.glob("*.md")):
            sf = segment_file_mod.read(sf_path)
            assert sf.frontmatter.segment_status == "accepted"
        # wiki reading.md has expected content
        text = result.wiki_reading_path.read_text(encoding="utf-8")
        assert "# Reading: Session 3" in text
        assert "reading_status" not in text


class TestSkipBullets:
    def test_empty_marker_rendered_in_assembly(
        self, env: tuple[cfg_mod.Config, Path]
    ) -> None:
        cfg, _ = env
        reviewer = _scripted([
            AcceptDecision(),
            SkipBulletsDecision(),
            AcceptDecision(),
        ])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        assert result.gate_fired is True
        assert result.skipped == 1
        assert result.accepted == 2

        # seg-002 on disk: body is the empty-bullets marker
        seg002_path = pipeline.pending_segment_path(_SOURCE_ID, "seg-002")
        sf002 = segment_file_mod.read(seg002_path)
        assert sf002.frontmatter.segment_status == "skipped"
        assert _EMPTY_MARKER in sf002.body

        # assembled wiki reading contains the marker for seg-002
        assert result.wiki_reading_path is not None
        text = result.wiki_reading_path.read_text(encoding="utf-8")
        assert _EMPTY_MARKER in text


class TestRegeneratingBlocksGate:
    def test_gate_does_not_fire(self, env: tuple[cfg_mod.Config, Path]) -> None:
        cfg, _ = env
        reviewer = _scripted([
            AcceptDecision(),
            RegenerateAgainDecision(),
            AcceptDecision(),
        ])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        assert result.gate_fired is False
        assert result.wiki_reading_path is None
        assert result.regenerating == 1

        seg002_path = pipeline.pending_segment_path(_SOURCE_ID, "seg-002")
        sf002 = segment_file_mod.read(seg002_path)
        assert sf002.frontmatter.segment_status == "regenerating"

        seg001_path = pipeline.pending_segment_path(_SOURCE_ID, "seg-001")
        sf001 = segment_file_mod.read(seg001_path)
        assert sf001.frontmatter.segment_status == "accepted"


class TestUndoSegmentScoped:
    def test_undo_clears_one_segment_only(
        self, env: tuple[cfg_mod.Config, Path]
    ) -> None:
        cfg, _ = env
        # UndoDecision for seg-002's slot — its pending mark is cleared
        reviewer = _scripted([AcceptDecision(), UndoDecision(), AcceptDecision()])
        result = run(cfg=cfg, source_id=_SOURCE_ID, reviewer=reviewer)

        # seg-002 stays draft (no pending mark → unchanged → disk not touched)
        seg002_path = pipeline.pending_segment_path(_SOURCE_ID, "seg-002")
        sf002 = segment_file_mod.read(seg002_path)
        assert sf002.frontmatter.segment_status == "draft"

        # gate does not fire — seg-002 still draft
        assert result.gate_fired is False
        assert result.accepted == 2
        assert result.unchanged == 1

        for sid in ("seg-001", "seg-003"):
            sf = segment_file_mod.read(pipeline.pending_segment_path(_SOURCE_ID, sid))
            assert sf.frontmatter.segment_status == "accepted"


class TestEngineWritesNothingUntilCommit:
    def test_disk_unchanged_during_walk(self, env: tuple[cfg_mod.Config, Path]) -> None:
        cfg, _ = env
        segs_dir = pipeline.pending_segments_dir(_SOURCE_ID)

        snapshots_before = {p: p.read_bytes() for p in sorted(segs_dir.glob("*.md"))}
        snapshots_mid: dict[Path, bytes] = {}
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
                nonlocal snapshots_mid, captured
                snapshots_mid = {
                    p: p.read_bytes() for p in sorted(segs_dir.glob("*.md"))
                }
                captured = True
                return CommitDecision()

        run(cfg=cfg, source_id=_SOURCE_ID, reviewer=SnapshotReviewer())
        assert captured
        assert snapshots_mid == snapshots_before


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
        segs_dir = pipeline.pending_segments_dir(_SOURCE_ID)
        for sf_path in sorted(segs_dir.glob("*.md")):
            sf = segment_file_mod.read(sf_path)
            assert sf.frontmatter.segment_status == "draft"


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
            f"schema_version: 1\nwiki_repo_path: {tmp_wiki}\n"
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
