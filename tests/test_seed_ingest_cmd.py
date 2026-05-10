"""Tests for the `seed-ingest` QA subcommand."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from auto_lorebook import (
    info_yaml,
    plan_yaml,
    proposal_yaml,
    reading,
    stage1b,
)
from auto_lorebook import reading_pipeline as pipeline
from auto_lorebook import reading_sidecar as reading_sidecar_mod
from auto_lorebook import segment_file as segment_file_mod
from auto_lorebook import (
    structure as structure_mod,
)
from auto_lorebook.commands import seed_ingest_cmd
from tests.test_reading_commands import _wire_client_responses

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    return home


def _write_user_config(home: Path, wiki: Path) -> None:
    (home / "config.yaml").write_text(
        f"""schema_version: 2
active_wiki: main
wikis:
- nickname: main
  path: {wiki}
openrouter:
  api_key_env: FAKE_OR_KEY
models:
  primary: anthropic/claude-sonnet-4-5
""",
        encoding="utf-8",
    )


def _args(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _seeded_sid_from(stdout: str) -> str:
    # "Seeded source qa-XXXXXXXX at stage 'plan' from fixture 'tiny-aldara'."
    first = stdout.splitlines()[0]
    return first.split()[2]


class TestSeedIngestPerStage:
    """Each --at value seeds the right files and they load via their parsers."""

    @pytest.mark.parametrize(
        ("at", "expected_wiki", "expected_pending"),
        [
            (
                "structure",
                {"transcript.en.srt", "info.yaml"},
                set(),
            ),
            (
                "summarize",
                {"transcript.en.srt", "info.yaml"},
                {"structure.yaml"},
            ),
            (
                "approve",
                {"transcript.en.srt", "info.yaml"},
                {"structure.yaml", "bullets.yaml", "reading.yaml", "segments"},
            ),
            (
                "plan",
                {"transcript.en.srt", "info.yaml", "reading.md"},
                {"structure.yaml", "bullets.yaml", "reading.yaml", "segments"},
            ),
        ],
    )
    def test_files_land_at_expected_paths(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
        capsys: pytest.CaptureFixture[str],
        at: str,
        expected_wiki: set[str],
        expected_pending: set[str],
    ) -> None:
        _write_user_config(tmp_home, tmp_wiki)
        rc = seed_ingest_cmd.run(_args(at=at, fixture="tiny-aldara", source_id=None))
        assert rc == 0
        sid = _seeded_sid_from(capsys.readouterr().out)
        assert sid.startswith("qa-")

        wiki_src = tmp_wiki / "sources" / sid
        pending_reading = tmp_home / "pending" / sid / "reading"

        assert {p.name for p in wiki_src.iterdir()} == expected_wiki
        if expected_pending:
            assert {p.name for p in pending_reading.iterdir()} == expected_pending
        else:
            assert not pending_reading.exists()

        # no old-style pending reading.md
        if pending_reading.exists():
            assert not (pending_reading / "reading.md").exists()

    def test_substitutes_source_id_in_artifacts(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_user_config(tmp_home, tmp_wiki)
        rc = seed_ingest_cmd.run(
            _args(at="plan", fixture="tiny-aldara", source_id=None)
        )
        assert rc == 0
        sid = _seeded_sid_from(capsys.readouterr().out)

        info = info_yaml.read(tmp_wiki / "sources" / sid / "info.yaml")
        assert info.source_id == sid

        struct = structure_mod.read(
            tmp_home / "pending" / sid / "reading" / "structure.yaml"
        )
        assert struct.source_id == sid

        bullets = stage1b.read_bullets(
            tmp_home / "pending" / sid / "reading" / "bullets.yaml"
        )
        assert bullets.source_id == sid

        approved_path = tmp_wiki / "sources" / sid / "reading.md"
        fm = reading.read_frontmatter(approved_path)
        assert fm["source_id"] == sid
        assert "reading_status" not in fm

        sidecar = reading_sidecar_mod.read(
            tmp_home / "pending" / sid / "reading" / "reading.yaml"
        )
        assert sidecar.default_speaker == "DM"

        # placeholder must not survive anywhere on disk
        for path in (
            tmp_wiki / "sources" / sid / "info.yaml",
            tmp_home / "pending" / sid / "reading" / "structure.yaml",
            tmp_home / "pending" / sid / "reading" / "bullets.yaml",
            tmp_home / "pending" / sid / "reading" / "reading.yaml",
            tmp_home / "pending" / sid / "reading" / "segments" / "seg-001.md",
            tmp_home / "pending" / sid / "reading" / "segments" / "seg-002.md",
            approved_path,
        ):
            assert "__QA_SOURCE_ID__" not in path.read_text(encoding="utf-8")

    def test_approve_writes_draft_status(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_user_config(tmp_home, tmp_wiki)
        rc = seed_ingest_cmd.run(
            _args(at="approve", fixture="tiny-aldara", source_id=None)
        )
        assert rc == 0
        sid = _seeded_sid_from(capsys.readouterr().out)

        seg001 = segment_file_mod.read(
            tmp_home / "pending" / sid / "reading" / "segments" / "seg-001.md"
        )
        assert seg001.frontmatter.segment_status == "draft"
        seg002 = segment_file_mod.read(
            tmp_home / "pending" / sid / "reading" / "segments" / "seg-002.md"
        )
        assert seg002.frontmatter.segment_status == "draft"

    def test_explicit_source_id_used_verbatim(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
    ) -> None:
        _write_user_config(tmp_home, tmp_wiki)
        rc = seed_ingest_cmd.run(
            _args(at="structure", fixture="tiny-aldara", source_id="qa-pinned")
        )
        assert rc == 0
        assert (tmp_wiki / "sources" / "qa-pinned" / "info.yaml").exists()


class TestNextStageRunsOnSeededInputs:
    """Stage outputs run on seeded inputs validate via their loaders."""

    def test_plan_then_extract_produces_valid_proposals(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_user_config(tmp_home, tmp_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        rc = seed_ingest_cmd.run(
            _args(at="plan", fixture="tiny-aldara", source_id=None)
        )
        assert rc == 0
        sid = _seeded_sid_from(capsys.readouterr().out)

        from auto_lorebook import config as cfg_mod  # noqa: PLC0415

        cfg = cfg_mod.load_config()
        client = MagicMock()
        _wire_client_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            plan_result = pipeline.plan(cfg, sid)
            extract_result = pipeline.extract(cfg, sid)

        loaded_plan = plan_yaml.read(plan_result.plan_path)
        assert loaded_plan.source_id == sid
        assert loaded_plan.planned_claims

        proposal_files = sorted(extract_result.proposals_dir.glob("*.yaml"))
        assert proposal_files
        for p in proposal_files:
            proposal_yaml.read(p)


class TestSeedIngestNoClobber:
    def test_repeat_seed_with_same_id_errors(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
    ) -> None:
        _write_user_config(tmp_home, tmp_wiki)
        first = seed_ingest_cmd.run(
            _args(at="structure", fixture="tiny-aldara", source_id="qa-fixed")
        )
        assert first == 0
        info_path = tmp_wiki / "sources" / "qa-fixed" / "info.yaml"
        original_text = info_path.read_text(encoding="utf-8")

        second = seed_ingest_cmd.run(
            _args(at="structure", fixture="tiny-aldara", source_id="qa-fixed")
        )
        assert second == 1
        # first seed left intact
        assert info_path.read_text(encoding="utf-8") == original_text


class TestUnknownFixture:
    def test_missing_fixture_errors(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
    ) -> None:
        _write_user_config(tmp_home, tmp_wiki)
        rc = seed_ingest_cmd.run(
            _args(at="structure", fixture="does-not-exist", source_id=None)
        )
        assert rc == 1
