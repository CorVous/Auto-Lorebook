"""End-to-end tests for the review CLI command.

Drives the full pipeline (generate-reading → approve-reading → review)
with a mocked OpenRouter client so the proposals exist on disk before
review runs.
"""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml

from auto_lorebook import config as cfg_mod
from auto_lorebook import entity_yaml
from auto_lorebook import review as review_mod
from auto_lorebook.commands import (
    approve_reading_cmd,
    generate_reading_cmd,
    review_cmd,
)
from auto_lorebook.openrouter import OpenRouterResponse
from auto_lorebook.review import (
    ApproveDecision,
    BundleDecision,
    BundleView,
    RejectDecision,
)

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
    "\n"
    "4\n"
    "00:05:00,000 --> 00:10:00,000\n"
    "The war followed. Many died.\n"
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
                "end": "0:10:00",
                "title": "Founding of Aldara",
                "speaker": "DM",
            },
        ],
        "uncertainty_flags": [],
    })


def _seg_bullets_payload(segment_id: str) -> str:
    per_seg = {
        "seg-001": [{"text": "Intro bullet", "anchor": "0:00:15"}],
        "seg-002": [
            {"text": "King Theron founded Aldara", "anchor": "0:02:30"},
        ],
    }
    return json.dumps({"bullets": per_seg.get(segment_id, [])})


def _stub_plan_payload() -> str:
    return json.dumps({
        "entity_resolutions": [
            {
                "mention": "Aldara",
                "mention_locations": ["[0:02:00-0:10:00] founding"],
                "resolution": "new",
                "proposed_entity_name": "Aldara",
                "proposed_category": "locations",
                "rationale": "Subject of this lore segment.",
            },
        ],
        "new_entities": [
            {"name": "Aldara", "category": "locations"},
        ],
        "planned_claims": [
            {
                "claim_group_id": "cg-001",
                "reading_section": "[0:02:00-0:10:00] Founding of Aldara",
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


def _stub_multi_target_plan_payload() -> str:
    """Plan routing cg-001 to three new entities sharing one claim."""
    return json.dumps({
        "entity_resolutions": [
            {
                "mention": "Aldara",
                "mention_locations": ["[0:02:00-0:10:00] founding"],
                "resolution": "new",
                "proposed_entity_name": "Aldara",
                "proposed_category": "locations",
                "rationale": "Location of founding.",
            },
            {
                "mention": "King Theron",
                "mention_locations": ["[0:02:00-0:10:00] founding"],
                "resolution": "new",
                "proposed_entity_name": "Theron",
                "proposed_category": "characters",
                "rationale": "King who founded Aldara.",
            },
            {
                "mention": "Second Age",
                "mention_locations": ["[0:02:00-0:10:00] founding"],
                "resolution": "new",
                "proposed_entity_name": "Second Age",
                "proposed_category": "events",
                "rationale": "Era during which Aldara was founded.",
            },
        ],
        "new_entities": [
            {"name": "Aldara", "category": "locations", "aliases_suggested": []},
            {"name": "Theron", "category": "characters", "aliases_suggested": []},
            {"name": "Second Age", "category": "events", "aliases_suggested": []},
        ],
        "planned_claims": [
            {
                "claim_group_id": "cg-001",
                "reading_section": "[0:02:00-0:10:00] Founding of Aldara",
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
                    {
                        "entity": "Theron",
                        "entity_state": "new",
                        "proposed_section": "biography",
                        "proposed_category": "characters",
                        "rationale": "Established as founder.",
                    },
                    {
                        "entity": "Second Age",
                        "entity_state": "new",
                        "proposed_section": "timeline",
                        "proposed_category": "events",
                        "rationale": "Founding dates to this era.",
                    },
                ],
            }
        ],
        "unresolved": [],
    })


def _wire_client_responses(client_mock: MagicMock) -> None:
    def side_effect(
        messages: list[dict[str, str]], **_kwargs: object
    ) -> OpenRouterResponse:
        system = next((m for m in messages if m["role"] == "system"), {})
        user = next((m for m in messages if m["role"] == "user"), {})
        system_text = system.get("content", "")
        user_text = user.get("content", "")

        if "segmenting" in system_text:
            text = _stub_structure_payload()
        elif "routing claim bullets" in system_text:
            text = _stub_plan_payload()
        elif "locate the verbatim transcript span" in system_text:
            text = _stub_extractor_payload()
        else:
            for seg_id in ("seg-001", "seg-002"):
                if seg_id in user_text:
                    text = _seg_bullets_payload(seg_id)
                    break
            else:
                text = json.dumps({"bullets": []})
        return OpenRouterResponse(text=text, model="m", tokens_in=0, tokens_out=0)

    client_mock.complete.side_effect = side_effect


def _wire_multi_target_responses(client_mock: MagicMock) -> None:
    """Like `_wire_client_responses` but uses the 3-target plan payload."""

    def side_effect(
        messages: list[dict[str, str]], **_kwargs: object
    ) -> OpenRouterResponse:
        system = next((m for m in messages if m["role"] == "system"), {})
        user = next((m for m in messages if m["role"] == "user"), {})
        system_text = system.get("content", "")
        user_text = user.get("content", "")

        if "segmenting" in system_text:
            text = _stub_structure_payload()
        elif "routing claim bullets" in system_text:
            text = _stub_multi_target_plan_payload()
        elif "locate the verbatim transcript span" in system_text:
            text = _stub_extractor_payload()
        else:
            for seg_id in ("seg-001", "seg-002"):
                if seg_id in user_text:
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
        "source_url": "https://youtube.com/watch?v=abc12345678",
        "title": "Session 3",
        "duration_seconds": 600,
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


class TestAutoApprove:
    def test_full_pipeline_lands_a_fact_in_entity_yaml(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_client_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
            assert approve_reading_cmd.run(_args(source_id="yt-abc12345678")) == 0
            rc = review_cmd.run(_args(source_id="yt-abc12345678", auto_approve=True))
        assert rc == 0
        # Stub created
        aldara_path = ingested_wiki / "locations" / "aldara.yaml"
        assert aldara_path.exists()
        e = entity_yaml.read(aldara_path)
        assert e.entity == "Aldara"
        assert e.created_by_ingest == "yt-abc12345678"
        assert len(e.facts) == 1
        assert e.facts[0]["id"] == "aldara-f001"
        # Proposals dir is empty
        proposals_dir = tmp_home / "pending" / "yt-abc12345678" / "proposals"
        assert list(proposals_dir.glob("*.yaml")) == []
        out = capsys.readouterr().out
        assert "approved=1" in out


class TestTTYGuard:
    def test_refuses_non_interactive_without_auto_approve(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        with patch("auto_lorebook.commands.review._is_interactive", return_value=False):
            rc = review_cmd.run(_args(source_id="yt-abc12345678", auto_approve=False))
        assert rc == 1


class TestEmptyDir:
    def test_says_nothing_to_review(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_client_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
            approve_reading_cmd.run(_args(source_id="yt-abc12345678"))
            # Drain via auto-approve, then run again — should be empty.
            review_cmd.run(_args(source_id="yt-abc12345678", auto_approve=True))
            capsys.readouterr()
            rc = review_cmd.run(_args(source_id="yt-abc12345678", auto_approve=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Nothing to review" in out


# ---------------------------------------------------------------------------
# Multi-target bundle integration tests
# ---------------------------------------------------------------------------


class _DropFirstRouteReviewer:
    """Scripted reviewer: approves bundles with target 0 deselected."""

    by_label = "scripted"

    def decide_bundle(self, view: BundleView) -> BundleDecision:
        # Keep all routes except index 0.
        selected = tuple(i for i in range(len(view.targets)) if i != 0)
        if not selected:
            return BundleDecision(decision=ApproveDecision(), selected_indices=(0,))
        return BundleDecision(decision=ApproveDecision(), selected_indices=selected)

    def confirm_alias(self, entity: str, mention: str) -> bool:  # noqa: ARG002
        return False


class _RejectAllReviewer:
    """Scripted reviewer: rejects every bundle."""

    by_label = "scripted"

    def decide_bundle(self, view: BundleView) -> BundleDecision:  # noqa: ARG002
        return BundleDecision(decision=RejectDecision(), selected_indices=())

    def confirm_alias(self, entity: str, mention: str) -> bool:  # noqa: ARG002
        return False


class TestMultiTargetBundle:
    def test_auto_approve_lands_three_facts_from_one_bundle(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Three-target cg-001 → three stubs, one decide_bundle call."""
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_multi_target_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
            assert approve_reading_cmd.run(_args(source_id="yt-abc12345678")) == 0
            rc = review_cmd.run(_args(source_id="yt-abc12345678", auto_approve=True))
        assert rc == 0

        # All three stubs created.
        aldara_path = ingested_wiki / "locations" / "aldara.yaml"
        theron_path = ingested_wiki / "characters" / "theron.yaml"
        second_age_path = ingested_wiki / "events" / "second-age.yaml"
        assert aldara_path.exists(), "Aldara stub missing"
        assert theron_path.exists(), "Theron stub missing"
        assert second_age_path.exists(), "Second Age stub missing"

        for path in (aldara_path, theron_path, second_age_path):
            e = entity_yaml.read(path)
            assert len(e.facts) == 1
            assert e.created_by_ingest == "yt-abc12345678"
            assert e.facts[0]["claim_group_id"] == "cg-001"

        # Proposals dir is empty — all three consumed.
        proposals_dir = tmp_home / "pending" / "yt-abc12345678" / "proposals"
        assert list(proposals_dir.glob("*.yaml")) == []

        # Count: approved=3 (one bundle, three routes).
        out = capsys.readouterr().out
        assert "approved=3" in out

    def test_drop_one_route_writes_two_facts_and_deletes_proposal(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Drop route 0 (Aldara) → only Theron + Second Age stubs created."""
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_multi_target_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
            assert approve_reading_cmd.run(_args(source_id="yt-abc12345678")) == 0

        result = review_mod.run(
            cfg=cfg_mod.load_config(),
            source_id="yt-abc12345678",
            reviewer=_DropFirstRouteReviewer(),
        )

        # Theron and Second Age stubs exist; Aldara's proposal was dropped.
        aldara_path = ingested_wiki / "locations" / "aldara.yaml"
        theron_path = ingested_wiki / "characters" / "theron.yaml"
        second_age_path = ingested_wiki / "events" / "second-age.yaml"
        assert not aldara_path.exists(), "Aldara should not have been created"
        assert theron_path.exists(), "Theron stub missing"
        assert second_age_path.exists(), "Second Age stub missing"

        assert result.approved == 2
        assert result.rejected == 1

        # All proposal files consumed.
        proposals_dir = tmp_home / "pending" / "yt-abc12345678" / "proposals"
        assert list(proposals_dir.glob("*.yaml")) == []
        capsys.readouterr()

    def test_reject_whole_bundle_leaves_no_stubs(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Reject the 3-target bundle → no entity YAMLs created."""
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_multi_target_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
            assert approve_reading_cmd.run(_args(source_id="yt-abc12345678")) == 0

        result = review_mod.run(
            cfg=cfg_mod.load_config(),
            source_id="yt-abc12345678",
            reviewer=_RejectAllReviewer(),
        )

        assert result.rejected == 3
        assert result.approved == 0
        assert not (ingested_wiki / "locations" / "aldara.yaml").exists()
        assert not (ingested_wiki / "characters" / "theron.yaml").exists()
        assert not (ingested_wiki / "events" / "second-age.yaml").exists()
        proposals_dir = tmp_home / "pending" / "yt-abc12345678" / "proposals"
        assert list(proposals_dir.glob("*.yaml")) == []
        capsys.readouterr()
