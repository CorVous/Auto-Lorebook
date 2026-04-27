"""End-to-end tests for the replan CLI command."""

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
    replan_cmd,
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


class TestReplan:
    def test_preserves_approved_facts(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_client(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id=SOURCE_ID))
            approve_reading_cmd.run(_args(source_id=SOURCE_ID, yes=True))
            review_cmd.run(_args(source_id=SOURCE_ID, auto_approve=True))
            # Now the entity stub exists and the proposals dir is empty.
            aldara_path = ingested_wiki / "locations" / "aldara.yaml"
            facts_before = entity_yaml.read(aldara_path).facts
            assert len(facts_before) == 1
            rc = replan_cmd.run(_args(source_id=SOURCE_ID))
        assert rc == 0
        # Approved fact survives
        facts_after = entity_yaml.read(aldara_path).facts
        assert facts_after == facts_before
        # Proposals dir repopulated by the new extract — Aldara is now
        # `existing` to the planner so we won't get a fresh aldara-f001.
        # This mocked plan still proposes `new` though, so the test just
        # asserts proposals exist (count depends on prompt response). For
        # a stricter assertion, mock the planner to skip Aldara.
        proposals_dir = reading_pipeline.pending_proposals_dir(SOURCE_ID)
        assert proposals_dir.exists()

    def test_no_stale_proposal_files(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_client(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id=SOURCE_ID))
            approve_reading_cmd.run(_args(source_id=SOURCE_ID, yes=True))
            # Drop a stale file into the proposals dir as if from an earlier
            # run that we want overwritten.
            proposals_dir = reading_pipeline.pending_proposals_dir(SOURCE_ID)
            (proposals_dir / "stale-f999.yaml").write_text(
                "schema_version: 1\nproposal_type: new_fact\n",
                encoding="utf-8",
            )
            assert (proposals_dir / "stale-f999.yaml").exists()
            replan_cmd.run(_args(source_id=SOURCE_ID))
        # After replan the stale file is gone (extract rmtrees the dir).
        assert not (proposals_dir / "stale-f999.yaml").exists()

    def test_errors_when_reading_not_approved(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        # No generate-reading / approve-reading run.
        rc = replan_cmd.run(_args(source_id=SOURCE_ID))
        assert rc == 1

    def test_errors_when_structure_missing(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_client(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id=SOURCE_ID))
            approve_reading_cmd.run(_args(source_id=SOURCE_ID, yes=True))
            # Remove structure.yaml to break the prerequisite.
            reading_pipeline.pending_structure_path(SOURCE_ID).unlink()
            rc = replan_cmd.run(_args(source_id=SOURCE_ID))
        assert rc == 1
