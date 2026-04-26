"""End-to-end tests for generate-reading / approve-reading / regenerate-reading.

The OpenRouter HTTP layer is mocked so the pipeline runs deterministically.
"""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml

from auto_lorebook import plan_yaml
from auto_lorebook.commands import (
    approve_reading_cmd,
    generate_reading_cmd,
    regenerate_reading_cmd,
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
    # One bullet per segment; anchor inside the segment
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
                "resolution": "existing",
                "matched_entity": "Aldara",
                "rationale": "Direct mention.",
            },
        ],
        "new_entities": [
            {"name": "Second Age", "category": "events"},
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
                        "entity_state": "existing",
                        "proposed_section": "founding",
                        "rationale": "Founding fact.",
                    },
                    {
                        "entity": "Second Age",
                        "entity_state": "new",
                        "proposed_section": "events-in-era",
                        "proposed_category": "events",
                        "rationale": "Dates the founding.",
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


def _wire_client_responses(client_mock: MagicMock) -> None:
    """Route client.complete by message content (structure / 1b / planner)."""

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
            # stage 1b: find which segment id is in user_text
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
    # Ingest a fake source
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


def _write_user_config(
    home: Path, wiki: Path, model: str = "anthropic/claude-sonnet-4-5"
) -> None:
    cfg_path = home / "config.yaml"
    cfg_path.write_text(
        f"""schema_version: 1
wiki_repo_path: {wiki}
openrouter:
  api_key_env: FAKE_OR_KEY
models:
  primary: {model}
""",
        encoding="utf-8",
    )


def _args(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


class TestGenerateReading:
    def test_happy_path(
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
            rc = generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Draft reading" in out

        pending = tmp_home / "pending" / "yt-abc12345678" / "reading"
        assert (pending / "structure.yaml").exists()
        assert (pending / "bullets.yaml").exists()
        assert (pending / "reading.md").exists()
        md = (pending / "reading.md").read_text(encoding="utf-8")
        assert "reading_status: draft" in md
        assert "King Theron founded Aldara" in md

    def test_missing_api_key_errors(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        # FAKE_OR_KEY is not set
        rc = generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
        assert rc == 1

    def test_missing_source_errors(self, tmp_home: Path, ingested_wiki: Path) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        rc = generate_reading_cmd.run(_args(source_id="yt-not-ingested"))
        assert rc == 1


class TestApproveReading:
    def test_flips_and_copies(
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
            rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678"))

        assert rc == 0
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert approved.exists()
        assert "reading_status: approved" in approved.read_text(encoding="utf-8")
        out = capsys.readouterr().out
        assert "Approved" in out

    def test_writes_plan_yaml_with_multi_target_claim(
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
            rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678"))

        assert rc == 0
        plan_path = tmp_home / "pending" / "yt-abc12345678" / "plan.yaml"
        assert plan_path.exists()
        first_line = next(ln for ln in plan_path.read_text().splitlines() if ln.strip())
        assert first_line.startswith("schema_version:")

        plan = plan_yaml.read(plan_path)
        # Exit-criterion: at least one claim routes to multiple targets
        assert any(len(c.targets) > 1 for c in plan.planned_claims)

        out = capsys.readouterr().out
        assert "Plan:" in out

    def test_no_draft_errors(self, tmp_home: Path, ingested_wiki: Path) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678"))
        assert rc == 1

    def test_writes_proposals_via_stage3(
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
            rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678"))

        assert rc == 0
        proposals_dir = tmp_home / "pending" / "yt-abc12345678" / "proposals"
        assert proposals_dir.is_dir()
        # multi-target plan claim → two proposal files (Aldara, Second Age)
        files = sorted(proposals_dir.glob("*.yaml"))
        assert len(files) == 2
        names = {f.name for f in files}
        assert "aldara-f001.yaml" in names
        assert "second-age-f001.yaml" in names
        out = capsys.readouterr().out
        assert "Extracted 2 proposal" in out
        assert "(0 flagged)" in out


class TestRegenerateReading:
    def test_rejects_segments_with_from_structure(
        self, tmp_home: Path, ingested_wiki: Path
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        rc = regenerate_reading_cmd.run(
            _args(
                source_id="yt-abc12345678",
                from_stage="structure",
                segments="seg-001",
            )
        )
        assert rc == 1

    def test_summarize_only_preserves_structure(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_client_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
            # count structure calls after first generate
            first_complete_count = client.complete.call_count

            # now regenerate --from=summarize --segments seg-002
            rc = regenerate_reading_cmd.run(
                _args(
                    source_id="yt-abc12345678",
                    from_stage="summarize",
                    segments="seg-002",
                )
            )
        assert rc == 0
        # second run should NOT re-call stage 1a (structure), only 1 seg of 1b
        added = client.complete.call_count - first_complete_count
        # one call for seg-002
        assert added == 1
