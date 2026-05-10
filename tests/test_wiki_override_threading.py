"""Tests proving --wiki override threads into every affected command.

Each test patches cfg.resolve_active_wiki and asserts the override
(not None) is passed through when wiki_override is set.
"""

from __future__ import annotations

import argparse
import contextlib
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml

from auto_lorebook import config as cfg_mod
from auto_lorebook import ingest_cleanup, reading_pipeline, reading_review, review
from auto_lorebook.commands import (
    approve_reading_cmd,
    generate_reading_cmd,
    plan_cmd,
    regenerate_reading_cmd,
    reject_ingest_cmd,
    replan_cmd,
    review_cmd,
    seed_ingest_cmd,
)
from auto_lorebook.ingest_cleanup import RejectResult
from auto_lorebook.review import ReviewResult

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    return home


def _make_two_wiki_config(home: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Write config with 'main' and 'alt' wikis; return (wiki1, wiki2) paths."""
    wiki1 = tmp_path / "wiki1"
    wiki2 = tmp_path / "wiki2"
    for wiki in (wiki1, wiki2):
        wiki.mkdir()
        for cat in (
            "characters",
            "locations",
            "factions",
            "events",
            "items",
            "concepts",
        ):
            (wiki / cat).mkdir()
        (wiki / ".wiki-context.yaml").write_text(
            "schema_version: 1\n", encoding="utf-8"
        )
        (wiki / ".transcription-corrections.yaml").write_text(
            "schema_version: 1\n", encoding="utf-8"
        )
    data = {
        "schema_version": 2,
        "active_wiki": "main",
        "wikis": [
            {"nickname": "main", "path": str(wiki1)},
            {"nickname": "alt", "path": str(wiki2)},
        ],
        "openrouter": {"api_key_env": "FAKE_OR_KEY"},
        "models": {"primary": "anthropic/claude-sonnet-4-5"},
    }
    (home / "config.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    return wiki1, wiki2


def _args(**kwargs: object) -> argparse.Namespace:
    defaults: dict[str, object] = {"wiki": None}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _plant_source(wiki: Path, source_id: str) -> None:
    """Plant minimal source fixtures so pipeline helpers reach resolve_active_wiki."""
    src = wiki / "sources" / source_id
    src.mkdir(parents=True, exist_ok=True)
    info = {
        "schema_version": 1,
        "source_id": source_id,
        "source_type": "youtube",
        "source_url": "https://youtube.com/watch?v=abc12345678",
        "title": "Test",
        "duration_seconds": 300,
        "caption_type": "manual",
        "fetched_at": "2026-01-01T00:00:00Z",
        "session_date": None,
        "transcript_filename": "transcript.en.srt",
        "context": {
            "perspective": None,
            "source_nature": None,
            "setting": None,
            "speakers": [],
            "notes": None,
        },
    }
    (src / "info.yaml").write_text(
        yaml.safe_dump(info, sort_keys=False), encoding="utf-8"
    )
    (src / "transcript.en.srt").write_text(
        "1\n00:00:00,000 --> 00:01:00,000\nHello world.\n", encoding="utf-8"
    )


def _recording_resolve(calls: list[str | None]) -> object:
    """Patched resolve_active_wiki that records the override arg."""
    orig = cfg_mod.Config.resolve_active_wiki

    def _record(self: cfg_mod.Config, override: str | None) -> object:
        calls.append(override)
        return orig(self, override)

    return _record


def _patch_resolve(calls: list[str | None]) -> AbstractContextManager[object]:
    return patch.object(
        cfg_mod.Config, "resolve_active_wiki", _recording_resolve(calls)
    )


# ---------------------------------------------------------------------------
# reading_pipeline helpers
# ---------------------------------------------------------------------------


class TestPipelineWikiOverride:
    """plan/extract/assemble_draft/_load_context respect wiki_override."""

    def test_plan_uses_override(self, tmp_home: Path, tmp_path: Path) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        calls: list[str | None] = []
        with _patch_resolve(calls):
            cfg = cfg_mod.load_config()
            with contextlib.suppress(reading_pipeline.ReadingPipelineError):
                reading_pipeline.plan(cfg, "yt-test", wiki_override="alt")
        assert "alt" in calls, f"override 'alt' never passed; calls={calls}"

    def test_extract_uses_override(self, tmp_home: Path, tmp_path: Path) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        calls: list[str | None] = []
        with _patch_resolve(calls):
            cfg = cfg_mod.load_config()
            with contextlib.suppress(reading_pipeline.ReadingPipelineError):
                reading_pipeline.extract(cfg, "yt-test", wiki_override="alt")
        assert "alt" in calls, f"override 'alt' never passed; calls={calls}"

    def test_assemble_draft_uses_override(self, tmp_home: Path, tmp_path: Path) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        calls: list[str | None] = []
        # plant a fake sidecar so assemble_draft gets past the early guard
        sidecar_path = reading_pipeline.pending_sidecar_path("yt-test")
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(
            "schema_version: 2\ndefault_speaker: DM\nname_corrections: {}\n"
            "segment_statuses: {}\ngap_warnings: []\nsession_date: null\n",
            encoding="utf-8",
        )
        with _patch_resolve(calls):
            cfg = cfg_mod.load_config()
            with contextlib.suppress(reading_pipeline.ReadingPipelineError):
                reading_pipeline.assemble_draft(cfg, "yt-test", wiki_override="alt")
        assert "alt" in calls, f"override 'alt' never passed; calls={calls}"

    def test_generate_uses_override(
        self, tmp_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, wiki2 = _make_two_wiki_config(tmp_home, tmp_path)
        _plant_source(wiki2, "yt-test")
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        calls: list[str | None] = []
        client = MagicMock()
        client.complete.side_effect = Exception("stop")
        with (
            _patch_resolve(calls),
            patch(
                "auto_lorebook.reading_pipeline.OpenRouterClient",
                return_value=client,
            ),
        ):
            cfg = cfg_mod.load_config()
            with contextlib.suppress(Exception):
                reading_pipeline.generate(cfg, "yt-test", wiki_override="alt")
        assert "alt" in calls, f"override 'alt' never passed; calls={calls}"

    def test_regenerate_uses_override(
        self, tmp_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, wiki2 = _make_two_wiki_config(tmp_home, tmp_path)
        _plant_source(wiki2, "yt-test")
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        calls: list[str | None] = []
        client = MagicMock()
        client.complete.side_effect = Exception("stop")
        with (
            _patch_resolve(calls),
            patch(
                "auto_lorebook.reading_pipeline.OpenRouterClient",
                return_value=client,
            ),
        ):
            cfg = cfg_mod.load_config()
            with contextlib.suppress(Exception):
                reading_pipeline.regenerate(
                    cfg, "yt-test", from_stage="structure", wiki_override="alt"
                )
        assert "alt" in calls, f"override 'alt' never passed; calls={calls}"


# ---------------------------------------------------------------------------
# reading_review.run
# ---------------------------------------------------------------------------


class TestReadingReviewWikiOverride:
    def test_run_uses_override(self, tmp_home: Path, tmp_path: Path) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        calls: list[str | None] = []
        with _patch_resolve(calls):
            cfg = cfg_mod.load_config()
            with contextlib.suppress(reading_review.ReadingReviewError):
                reading_review.run(
                    cfg=cfg,
                    source_id="yt-test",
                    reviewer=MagicMock(),
                    wiki_override="alt",
                )
        assert "alt" in calls, f"override 'alt' never passed; calls={calls}"


# ---------------------------------------------------------------------------
# review.run
# ---------------------------------------------------------------------------


class TestReviewWikiOverride:
    def test_run_uses_override(self, tmp_home: Path, tmp_path: Path) -> None:
        _, wiki2 = _make_two_wiki_config(tmp_home, tmp_path)
        _plant_source(wiki2, "yt-test")
        calls: list[str | None] = []
        with _patch_resolve(calls):
            cfg = cfg_mod.load_config()
            with contextlib.suppress(Exception):
                review.run(
                    cfg=cfg,
                    source_id="yt-test",
                    reviewer=MagicMock(),
                    wiki_override="alt",
                )
        assert "alt" in calls, f"override 'alt' never passed; calls={calls}"


# ---------------------------------------------------------------------------
# ingest_cleanup — preview + reject_ingest
# ---------------------------------------------------------------------------


class TestIngestCleanupWikiOverride:
    def test_preview_uses_override(self, tmp_home: Path, tmp_path: Path) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        calls: list[str | None] = []
        with _patch_resolve(calls):
            cfg = cfg_mod.load_config()
            ingest_cleanup.preview(cfg, "yt-test", wiki_override="alt")
        assert "alt" in calls, f"override 'alt' never passed; calls={calls}"

    def test_reject_ingest_uses_override(self, tmp_home: Path, tmp_path: Path) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        calls: list[str | None] = []
        with _patch_resolve(calls):
            cfg = cfg_mod.load_config()
            ingest_cleanup.reject_ingest(cfg, "yt-test", wiki_override="alt")
        assert "alt" in calls, f"override 'alt' never passed; calls={calls}"


# ---------------------------------------------------------------------------
# Command run() functions — each passes args.wiki down to helpers
# ---------------------------------------------------------------------------


class TestCommandRunWikiOverride:
    """Each command run() resolves the override from args.wiki."""

    def test_plan_cmd_passes_override(self, tmp_home: Path, tmp_path: Path) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        captured: list[str | None] = []

        def fake_plan(
            _cfg: object, _sid: str, wiki_override: str | None = None
        ) -> object:
            captured.append(wiki_override)
            raise reading_pipeline.ReadingPipelineError("stop")

        with patch("auto_lorebook.commands.plan_cmd.pipeline.plan", fake_plan):
            rc = plan_cmd.run(_args(source_id="yt-test", wiki="alt"))
        assert rc != 0
        assert captured == ["alt"]

    def test_replan_cmd_passes_override(self, tmp_home: Path, tmp_path: Path) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        captured: list[str | None] = []

        def fake_plan(
            _cfg: object, _sid: str, wiki_override: str | None = None
        ) -> object:
            captured.append(wiki_override)
            raise reading_pipeline.ReadingPipelineError("stop")

        with patch("auto_lorebook.commands.replan_cmd.pipeline.plan", fake_plan):
            rc = replan_cmd.run(_args(source_id="yt-test", wiki="alt"))
        assert rc != 0
        assert captured == ["alt"]

    def test_generate_reading_cmd_passes_override(
        self, tmp_home: Path, tmp_path: Path
    ) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        captured: list[str | None] = []

        def fake_generate(
            _cfg: object, _sid: str, wiki_override: str | None = None
        ) -> object:
            captured.append(wiki_override)
            raise reading_pipeline.ReadingPipelineError("stop")

        with patch(
            "auto_lorebook.commands.generate_reading_cmd.pipeline.generate",
            fake_generate,
        ):
            rc = generate_reading_cmd.run(_args(source_id="yt-test", wiki="alt"))
        assert rc != 0
        assert captured == ["alt"]

    def test_regenerate_reading_cmd_passes_override(
        self, tmp_home: Path, tmp_path: Path
    ) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        captured: list[str | None] = []

        def fake_regenerate(
            _cfg: object,
            _sid: str,
            *,
            from_stage: str,  # noqa: ARG001
            segment_ids: list[str] | None = None,  # noqa: ARG001
            wiki_override: str | None = None,
        ) -> object:
            captured.append(wiki_override)
            raise reading_pipeline.ReadingPipelineError("stop")

        with patch(
            "auto_lorebook.commands.regenerate_reading_cmd.pipeline.regenerate",
            fake_regenerate,
        ):
            rc = regenerate_reading_cmd.run(
                _args(
                    source_id="yt-test",
                    wiki="alt",
                    from_stage="structure",
                    segments=None,
                )
            )
        assert rc != 0
        assert captured == ["alt"]

    def test_reject_ingest_cmd_passes_override(
        self, tmp_home: Path, tmp_path: Path
    ) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        captured_preview: list[str | None] = []
        captured_reject: list[str | None] = []

        def fake_preview(
            _cfg: object, _sid: str, wiki_override: str | None = None
        ) -> RejectResult:
            captured_preview.append(wiki_override)
            return RejectResult()

        def fake_reject(
            _cfg: object, _sid: str, wiki_override: str | None = None
        ) -> RejectResult:
            captured_reject.append(wiki_override)
            return RejectResult()

        with (
            patch(
                "auto_lorebook.commands.reject_ingest_cmd.ingest_cleanup.preview",
                fake_preview,
            ),
            patch(
                "auto_lorebook.commands.reject_ingest_cmd.ingest_cleanup.reject_ingest",
                fake_reject,
            ),
        ):
            rc = reject_ingest_cmd.run(_args(source_id="yt-test", wiki="alt", yes=True))
        assert rc == 0
        assert captured_preview == ["alt"]
        assert captured_reject == ["alt"]

    def test_review_cmd_passes_override(self, tmp_home: Path, tmp_path: Path) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        captured: list[str | None] = []

        def fake_run(
            *,
            cfg: object,  # noqa: ARG001
            source_id: str,  # noqa: ARG001
            reviewer: object,  # noqa: ARG001
            wiki_override: str | None = None,
        ) -> ReviewResult:
            captured.append(wiki_override)
            return ReviewResult(approved=0, edited=0, rejected=0, remaining=0)

        with patch("auto_lorebook.commands.review_cmd.review_mod.run", fake_run):
            rc = review_cmd.run(
                _args(source_id="yt-test", wiki="alt", auto_approve=True)
            )
        assert rc == 0
        assert captured == ["alt"]

    def test_approve_reading_cmd_passes_override(
        self, tmp_home: Path, tmp_path: Path
    ) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        captured: list[str | None] = []

        def fake_approve(
            _cfg: object, _sid: str, wiki_override: str | None = None
        ) -> object:
            captured.append(wiki_override)
            raise reading_pipeline.ReadingPipelineError("stop")

        with patch(
            "auto_lorebook.commands.approve_reading_cmd.pipeline.approve", fake_approve
        ):
            rc = approve_reading_cmd.run(
                _args(source_id="yt-test", wiki="alt", yes=True)
            )
        assert rc != 0
        assert captured == ["alt"]

    def test_seed_ingest_cmd_passes_override(
        self, tmp_home: Path, tmp_path: Path
    ) -> None:
        _make_two_wiki_config(tmp_home, tmp_path)
        captured: list[str | None] = []

        def fake_seed(
            _cfg: object,
            _sid: str,
            _at: str,
            _fixture: str,
            wiki_override: str | None = None,
        ) -> None:
            captured.append(wiki_override)
            raise seed_ingest_cmd.SeedIngestError("stop")

        with patch("auto_lorebook.commands.seed_ingest_cmd._seed", fake_seed):
            rc = seed_ingest_cmd.run(
                _args(
                    at="plan",
                    fixture="tiny-aldara",
                    source_id="qa-test",
                    wiki="alt",
                )
            )
        assert rc != 0
        assert captured == ["alt"]
