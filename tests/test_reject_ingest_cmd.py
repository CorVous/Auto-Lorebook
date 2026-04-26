"""End-to-end tests for the reject-ingest CLI command."""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml

from auto_lorebook import entity_yaml, reading_pipeline
from auto_lorebook.commands import (
    approve_reading_cmd,
    generate_reading_cmd,
    reject_ingest_cmd,
    review_cmd,
)
from auto_lorebook.openrouter import OpenRouterResponse

if TYPE_CHECKING:
    from pathlib import Path


_SRT = (
    "1\n"
    "00:00:00,000 --> 00:00:30,000\n"
    "Welcome to the session.\n"
    "\n"
    "2\n"
    "00:00:30,000 --> 00:02:00,000\n"
    "Today we talk about Aldara.\n"
    "\n"
    "3\n"
    "00:02:00,000 --> 00:05:00,000\n"
    "King Theron founded Aldara in the Second Age.\n"
)


def _stub_structure_payload() -> str:
    return json.dumps({
        "default_speaker": "DM",
        "segments": [
            {
                "id": "seg-001",
                "start": "0:00:00",
                "end": "0:02:00",
                "title": "Intro",
                "speaker": "DM",
            },
            {
                "id": "seg-002",
                "start": "0:02:00",
                "end": "0:05:00",
                "title": "Founding of Aldara",
                "speaker": "DM",
            },
        ],
        "uncertainty_flags": [],
    })


def _seg_bullets_payload(segment_id: str) -> str:
    per_seg = {
        "seg-001": [{"text": "Intro bullet", "anchor": "0:00:15"}],
        "seg-002": [{"text": "King Theron founded Aldara", "anchor": "0:02:30"}],
    }
    return json.dumps({"bullets": per_seg.get(segment_id, [])})


def _stub_plan_payload() -> str:
    return json.dumps({
        "entity_resolutions": [
            {
                "mention": "Aldara",
                "mention_locations": ["[0:02:00-0:05:00] founding"],
                "resolution": "new",
                "proposed_entity_name": "Aldara",
                "proposed_category": "locations",
                "rationale": "Subject of this lore segment.",
            },
        ],
        "new_entities": [{"name": "Aldara", "category": "locations"}],
        "planned_claims": [
            {
                "claim_group_id": "cg-001",
                "reading_section": "[0:02:00-0:05:00] Founding of Aldara",
                "reading_bullet_index": 0,
                "locator": "0:02:30",
                "locator_hint": "0:02:00-0:02:30",
                "proposed_speaker": "DM",
                "proposed_status": "authoritative",
                "proposed_status_reason": None,
                "targets": [
                    {
                        "entity": "Aldara",
                        "entity_state": "new",
                        "proposed_section": "founding",
                        "proposed_category": "locations",
                        "rationale": "Founding fact.",
                    },
                ],
            }
        ],
        "unresolved": [],
    })


def _stub_extractor_payload() -> str:
    return json.dumps({
        "text": "King Theron founded Aldara in the Second Age.",
        "raw_transcript_span": "King Theron founded Aldara in the Second Age.",
        "text_corrects_transcript": False,
        "corrections_applied": [],
    })


def _wire_client(client_mock: MagicMock) -> None:
    def side_effect(
        messages: list[dict[str, str]], **_kw: object
    ) -> OpenRouterResponse:
        system = next((m for m in messages if m["role"] == "system"), {}).get(
            "content", ""
        )
        user = next((m for m in messages if m["role"] == "user"), {}).get("content", "")
        if "segmenting" in system:
            text = _stub_structure_payload()
        elif "routing claim bullets" in system:
            text = _stub_plan_payload()
        elif "locate the verbatim transcript span" in system:
            text = _stub_extractor_payload()
        else:
            for seg_id in ("seg-001", "seg-002"):
                if seg_id in user:
                    text = _seg_bullets_payload(seg_id)
                    break
            else:
                text = json.dumps({"bullets": []})
        return OpenRouterResponse(text=text, model="m", tokens_in=0, tokens_out=0)

    client_mock.complete.side_effect = side_effect


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    return home


@pytest.fixture
def ingested_wiki(tmp_wiki: Path, tmp_home: Path) -> Path:  # noqa: ARG001
    source_id = "yt-abc12345678"
    src_dir = tmp_wiki / "sources" / source_id
    src_dir.mkdir(parents=True)
    (src_dir / "transcript.en.srt").write_text(_SRT, encoding="utf-8")
    info = {
        "schema_version": 1,
        "source_id": source_id,
        "source_type": "youtube",
        "source_url": "https://youtube.com/watch?v=abc",
        "title": "Session 3",
        "duration_seconds": 300,
        "caption_type": "manual",
        "fetched_at": "2026-04-20T14:35:12Z",
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
    (src_dir / "info.yaml").write_text(
        yaml.safe_dump(info, sort_keys=False), encoding="utf-8"
    )
    return tmp_wiki


def _write_user_config(home: Path, wiki: Path) -> None:
    cfg_path = home / "config.yaml"
    cfg_path.write_text(
        f"""schema_version: 1
wiki_repo_path: {wiki}
openrouter:
  api_key_env: FAKE_OR_KEY
models:
  primary: anthropic/claude-sonnet-4-5
""",
        encoding="utf-8",
    )


def _args(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


SOURCE_ID = "yt-abc12345678"


def _approve_one(
    tmp_home: Path,
    wiki: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the pipeline to a single approved fact under SOURCE_ID."""
    _write_user_config(tmp_home, wiki)
    monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
    client = MagicMock()
    _wire_client(client)
    with patch("auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client):
        generate_reading_cmd.run(_args(source_id=SOURCE_ID))
        approve_reading_cmd.run(_args(source_id=SOURCE_ID))
        review_cmd.run(_args(source_id=SOURCE_ID, auto_approve=True))


class TestRejectIngest:
    def test_yes_skips_prompt(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        aldara_path = ingested_wiki / "locations" / "aldara.yaml"
        assert aldara_path.exists()
        rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Rejected ingest" in out
        assert not aldara_path.exists()

    def test_tty_guard_refuses_without_yes(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        with patch(
            "auto_lorebook.commands.reject_ingest._is_interactive",
            return_value=False,
        ):
            rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=False))
        assert rc == 1
        # Entity still in place — guard prevented the destructive op.
        assert (ingested_wiki / "locations" / "aldara.yaml").exists()

    def test_confirmation_y_runs(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        aldara_path = ingested_wiki / "locations" / "aldara.yaml"
        with (
            patch(
                "auto_lorebook.commands.reject_ingest._is_interactive",
                return_value=True,
            ),
            patch("builtins.input", side_effect=["y"]),
        ):
            rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=False))
        assert rc == 0
        assert not aldara_path.exists()

    def test_confirmation_n_keeps_state(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        aldara_path = ingested_wiki / "locations" / "aldara.yaml"
        with (
            patch(
                "auto_lorebook.commands.reject_ingest._is_interactive",
                return_value=True,
            ),
            patch("builtins.input", side_effect=[""]),  # blank == no
        ):
            rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=False))
        assert rc == 0
        assert aldara_path.exists()
        out = capsys.readouterr().out
        assert "Cancelled" in out

    def test_end_to_end_cleanup(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        # Pre-conditions: stub exists, pending plan + (drained) proposals dir
        aldara_path = ingested_wiki / "locations" / "aldara.yaml"
        plan_path = reading_pipeline.pending_plan_path(SOURCE_ID)
        proposals_dir = reading_pipeline.pending_proposals_dir(SOURCE_ID)
        sources_dir = ingested_wiki / "sources" / SOURCE_ID
        assert aldara_path.exists()
        assert plan_path.exists()
        # proposals dir exists but is empty (auto-approve drained it)
        assert proposals_dir.is_dir()
        assert sources_dir.is_dir()

        rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=True))
        assert rc == 0
        assert not aldara_path.exists()
        assert not plan_path.exists()
        assert not proposals_dir.exists()
        # sources/ untouched
        assert (sources_dir / "info.yaml").exists()
        assert (sources_dir / "transcript.en.srt").exists()
        # reading-stage pending artifacts survive
        assert reading_pipeline.pending_structure_path(SOURCE_ID).exists()

    def test_nothing_to_reject(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        # No ingest has run; nothing to reject.
        rc = reject_ingest_cmd.run(_args(source_id="ingest-that-never-was", yes=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Nothing to reject" in out

    def test_proper_entity_yaml_round_trip(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After yes-rejection, no entity YAMLs survive that referenced this ingest."""
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=True))
        assert rc == 0
        # Walk every category dir; assert nothing refers to SOURCE_ID
        for cat in entity_yaml.CATEGORIES:
            for path in (ingested_wiki / cat).glob("*.yaml"):
                e = entity_yaml.read(path)
                assert e.created_by_ingest != SOURCE_ID
                for f in e.facts:
                    assert f.get("created_by_ingest") != SOURCE_ID
                for a in e.aliases:
                    assert a.added_by_ingest != SOURCE_ID
