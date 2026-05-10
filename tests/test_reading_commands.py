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
    extract_cmd,
    generate_reading_cmd,
    plan_cmd,
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
        f"""schema_version: 2
active_wiki: main
wikis:
- nickname: main
  path: {wiki}
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

        pending = (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "reading"
        )
        assert (pending / "structure.yaml").exists()
        assert (pending / "bullets.yaml").exists()
        assert (pending / "reading.yaml").exists()
        assert (pending / "segments" / "seg-001.md").exists()
        assert (pending / "segments" / "seg-002.md").exists()
        # no old-style reading.md under pending
        assert not (pending / "reading.md").exists()

        # reading.yaml has correct schema
        import yaml as _yaml  # noqa: PLC0415

        sidecar_data = _yaml.safe_load((pending / "reading.yaml").read_text())
        assert sidecar_data["schema_version"] == 2
        assert sidecar_data["default_speaker"] == "DM"

        # seg-002.md contains the Theron claim
        seg002 = (pending / "segments" / "seg-002.md").read_text(encoding="utf-8")
        assert "King Theron founded Aldara" in seg002

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


class TestGenerateReadingSidecarSchema:
    """Sidecar written by generate-reading has correct schema and gap_warnings."""

    def test_sidecar_schema_v2_and_gap_warnings_empty(
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
            rc = generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
        assert rc == 0

        pending = (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "reading"
        )
        sidecar_data = yaml.safe_load((pending / "reading.yaml").read_text())
        assert sidecar_data["schema_version"] == 2
        # fixture has only "Intro" + "Founding of Aldara" — no low-yield matches
        assert sidecar_data["gap_warnings"] == []

    def test_sidecar_gap_warnings_persisted_when_present(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        from auto_lorebook.gap_check import GapWarning  # noqa: PLC0415

        stub_warning = GapWarning(
            start=2050.0,
            end=2902.0,
            segment_ids=("seg-005",),
            segment_titles=("Pizza discussion",),
        )
        client = MagicMock()
        _wire_client_responses(client)
        with (
            patch(
                "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
            ),
            patch(
                "auto_lorebook.reading_pipeline.gap_check_mod.check",
                return_value=[stub_warning],
            ),
        ):
            rc = generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
        assert rc == 0

        from auto_lorebook import reading_sidecar as sidecar_mod  # noqa: PLC0415

        pending = (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "reading"
        )
        sc = sidecar_mod.read(pending / "reading.yaml")
        assert len(sc.gap_warnings) == 1
        assert sc.gap_warnings[0].start == pytest.approx(2050.0)
        assert sc.gap_warnings[0].segment_titles == ("Pizza discussion",)


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
            rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=True))

        assert rc == 0
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert approved.exists()
        assert "# Reading: Session 3" in approved.read_text(encoding="utf-8")
        assert "reading_status" not in approved.read_text(encoding="utf-8")
        # approve-reading must NOT cascade into plan/extract
        assert not (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "plan.yaml"
        ).exists()
        assert not (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "proposals"
        ).exists()
        out = capsys.readouterr().out
        assert "Approved" in out

    def test_no_draft_errors(self, tmp_home: Path, ingested_wiki: Path) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=True))
        assert rc == 1


class TestApproveReadingInteractive:
    """Hierarchical interactive approve-reading loop."""

    def _patch_inputs(
        self, monkeypatch: pytest.MonkeyPatch, answers: list[str]
    ) -> list[str]:
        """Feed scripted answers to input(); return mutable record of prompts seen."""
        prompts: list[str] = []
        it = iter(answers)

        def fake_input(prompt: str = "") -> str:
            prompts.append(prompt)
            try:
                return next(it)
            except StopIteration as e:
                raise EOFError from e

        monkeypatch.setattr("builtins.input", fake_input)
        return prompts

    def _force_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "auto_lorebook.commands.approve_reading._is_interactive",
            lambda: True,
        )

    def _generate(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """Run generate-reading; return mock client."""
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        client = MagicMock()
        _wire_client_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
        return client

    def test_non_tty_without_yes_errors(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setattr(
            "auto_lorebook.commands.approve_reading._is_interactive",
            lambda: False,
        )
        self._generate(monkeypatch)

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 1
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert not approved.exists()

    def test_outer_quit_without_marks_writes_nothing(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        self._patch_inputs(monkeypatch, ["q"])
        self._generate(monkeypatch)

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 0
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert not approved.exists()
        pending = (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "reading"
        )
        assert (pending / "reading.yaml").exists()
        assert not (pending / "reading.md").exists()
        # segments still draft
        import yaml as _yaml  # noqa: PLC0415

        fm = _yaml.safe_load(
            (pending / "segments" / "seg-001.md").read_text().split("---")[1]
        )
        assert fm["segment_status"] == "draft"

    def test_open_segment_then_accept_then_quit_one_segment_only(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Accept only seg-001; gate doesn't fire; output mentions undecided."""
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        # open seg 1, accept it, then quit
        self._patch_inputs(monkeypatch, ["1", "a", "q"])
        self._generate(monkeypatch)

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 0
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert not approved.exists()
        pending = (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "reading"
        )
        import yaml as _yaml  # noqa: PLC0415

        fm1 = _yaml.safe_load(
            (pending / "segments" / "seg-001.md").read_text().split("---")[1]
        )
        assert fm1["segment_status"] == "accepted"
        out = capsys.readouterr().out
        assert "Still" in out
        assert "undecided" in out

    def test_full_walkthrough_accept_all_fires_gate(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Accept both segments; gate fires; wiki reading.md written."""
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        self._patch_inputs(monkeypatch, ["1", "a", "2", "a", "q"])
        self._generate(monkeypatch)

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 0
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert approved.exists()
        assert "# Reading: Session 3" in approved.read_text(encoding="utf-8")
        assert "reading_status" not in approved.read_text(encoding="utf-8")
        out = capsys.readouterr().out
        assert "Approved" in out

    def test_n_jumps_to_next_draft(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """[n] jumps to next draft; accepting both fires gate."""
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        self._patch_inputs(monkeypatch, ["n", "a", "n", "a", "q"])
        self._generate(monkeypatch)

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 0
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert approved.exists()
        out = capsys.readouterr().out
        assert "Approved" in out

    def test_skip_bullets_in_per_segment(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[s] skips bullets; gate fires when both segments decided."""
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        self._patch_inputs(monkeypatch, ["1", "s", "2", "a", "q"])
        self._generate(monkeypatch)

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 0
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert approved.exists()
        pending = (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "reading"
        )
        import yaml as _yaml  # noqa: PLC0415

        fm1 = _yaml.safe_load(
            (pending / "segments" / "seg-001.md").read_text().split("---")[1]
        )
        assert fm1["segment_status"] == "skipped"

    def test_undo_clears_pending_mark(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[u] in seg prompt clears mark; [b] returns to outer; seg stays draft."""
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        # open 1, accept (returns to outer), re-open 1, undo (stays), back, quit
        self._patch_inputs(monkeypatch, ["1", "a", "1", "u", "b", "q"])
        self._generate(monkeypatch)

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 0
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert not approved.exists()
        pending = (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "reading"
        )
        import yaml as _yaml  # noqa: PLC0415

        fm1 = _yaml.safe_load(
            (pending / "segments" / "seg-001.md").read_text().split("---")[1]
        )
        assert fm1["segment_status"] == "draft"

    def test_back_returns_to_outer_without_committing(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[b] from seg prompt returns to outer; nothing committed."""
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        self._patch_inputs(monkeypatch, ["1", "b", "q"])
        self._generate(monkeypatch)

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 0
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert not approved.exists()

    def test_meta_opens_sidecar_in_editor(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[m] opens reading.yaml in $EDITOR."""
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        monkeypatch.setenv("EDITOR", "my-fake-editor")
        self._patch_inputs(monkeypatch, ["m", "q"])
        self._generate(monkeypatch)

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kwargs: object) -> object:
            calls.append(list(cmd))
            return MagicMock(returncode=0)

        monkeypatch.setattr(
            "auto_lorebook.commands.approve_reading.subprocess.run", fake_run
        )

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 0
        assert len(calls) == 1
        assert calls[0][0] == "my-fake-editor"
        assert calls[0][1].endswith("reading.yaml")

    def test_edit_opens_segment_md_in_editor(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """[e] opens seg-001.md in $EDITOR; status stays draft."""
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        monkeypatch.setenv("EDITOR", "my-fake-editor")
        # open seg 1, edit, back, quit
        self._patch_inputs(monkeypatch, ["1", "e", "b", "q"])
        self._generate(monkeypatch)

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kwargs: object) -> object:
            calls.append(list(cmd))
            return MagicMock(returncode=0)

        monkeypatch.setattr(
            "auto_lorebook.commands.approve_reading.subprocess.run", fake_run
        )

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 0
        assert len(calls) == 1
        assert calls[0][0] == "my-fake-editor"
        assert calls[0][1].endswith("seg-001.md")

        pending = (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "reading"
        )
        import yaml as _yaml  # noqa: PLC0415

        fm1 = _yaml.safe_load(
            (pending / "segments" / "seg-001.md").read_text().split("---")[1]
        )
        assert fm1["segment_status"] == "draft"

    def test_edit_then_accept_persists_body_changes_through_engine(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Editor mutation of seg-001 body reflected in wiki reading.md."""
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        monkeypatch.setenv("EDITOR", "my-fake-editor")
        # open 1, edit, accept; open 2, accept; quit
        self._patch_inputs(monkeypatch, ["1", "e", "a", "2", "a", "q"])
        self._generate(monkeypatch)

        pending = (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "reading"
        )
        seg001_path = pending / "segments" / "seg-001.md"

        def fake_run(cmd: list[str], **_kwargs: object) -> object:
            # editor side effect: append custom text to seg-001 body
            if cmd[1].endswith("seg-001.md"):
                current = seg001_path.read_text(encoding="utf-8")
                seg001_path.write_text(
                    current + "\n- CUSTOM EDITED BULLET\n", encoding="utf-8"
                )
            return MagicMock(returncode=0)

        monkeypatch.setattr(
            "auto_lorebook.commands.approve_reading.subprocess.run", fake_run
        )

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 0
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert approved.exists()
        content = approved.read_text(encoding="utf-8")
        assert "CUSTOM EDITED BULLET" in content
        import yaml as _yaml  # noqa: PLC0415

        fm1 = _yaml.safe_load(
            (pending / "segments" / "seg-001.md").read_text().split("---")[1]
        )
        assert fm1["segment_status"] == "accepted"
        out = capsys.readouterr().out
        assert "Approved" in out

    def test_ctrl_c_in_outer_writes_nothing(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ctrl-C at outer prompt returns 130; no commits."""
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        self._generate(monkeypatch)

        call_count = {"n": 0}

        def fake_input(_prompt: str = "") -> str:
            call_count["n"] += 1
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", fake_input)

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 130
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert not approved.exists()

    def test_ctrl_c_in_segment_writes_nothing(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ctrl-C inside per-segment prompt returns 130; no commits."""
        _write_user_config(tmp_home, ingested_wiki)
        self._force_tty(monkeypatch)
        self._generate(monkeypatch)

        call_count = {"n": 0}

        def fake_input(_prompt: str = "") -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return "1"  # outer: open segment 1
            raise KeyboardInterrupt  # inside seg prompt

        monkeypatch.setattr("builtins.input", fake_input)

        rc = approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=False))

        assert rc == 130
        approved = ingested_wiki / "sources" / "yt-abc12345678" / "reading.md"
        assert not approved.exists()


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

    def test_summarize_only_preserves_reading_yaml(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--from=summarize preserves reading.yaml (sidecar)."""
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_client_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
            pending = (
                ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "reading"
            )
            sidecar_before = (pending / "reading.yaml").read_bytes()

            regenerate_reading_cmd.run(
                _args(
                    source_id="yt-abc12345678",
                    from_stage="summarize",
                    segments="seg-002",
                )
            )

        # sidecar always rewritten but should be structurally identical
        import yaml as _yaml  # noqa: PLC0415

        sidecar_after = _yaml.safe_load((pending / "reading.yaml").read_text())
        sidecar_orig = _yaml.safe_load(sidecar_before)
        assert sidecar_after["default_speaker"] == sidecar_orig["default_speaker"]
        assert sidecar_after["name_corrections"] == sidecar_orig["name_corrections"]


class TestPlan:
    def test_refuses_without_wiki_reading(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Plan fails if wiki-side reading.md doesn't exist."""
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        rc = plan_cmd.run(_args(source_id="yt-abc12345678"))
        assert rc == 1

    def test_success_writes_plan_yaml(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Full chain: generate → approve → plan writes plan.yaml."""
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_client_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
            approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=True))
            rc = plan_cmd.run(_args(source_id="yt-abc12345678"))

        assert rc == 0
        plan_path = (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "plan.yaml"
        )
        assert plan_path.exists()
        first_line = next(ln for ln in plan_path.read_text().splitlines() if ln.strip())
        assert first_line.startswith("schema_version:")

        loaded = plan_yaml.read(plan_path)
        assert any(len(c.targets) > 1 for c in loaded.planned_claims)

        out = capsys.readouterr().out
        assert "Plan:" in out


class TestRenderOuterGapWarnings:
    """_render_outer renders gap warnings below segment list, above prompt."""

    def _make_summaries(self) -> list:
        from auto_lorebook.commands.approve_reading import (  # noqa: PLC0415
            _SegSummary,  # noqa: PLC2701
        )

        return [
            _SegSummary(
                segment_id="seg-001",
                title="Intro",
                start=0.0,
                end=120.0,
                speaker="DM",
                current_status="draft",
            )
        ]

    def test_empty_warnings_no_gap_block(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from auto_lorebook.commands.approve_reading import (  # noqa: PLC0415
            _render_outer,  # noqa: PLC2701
        )

        summaries = self._make_summaries()
        _render_outer("yt-test", summaries, {}, "Test Session", gap_warnings=[])
        out = capsys.readouterr().out
        assert "seg-001" in out or "Intro" in out
        assert "Possible coverage gap" not in out
        assert "⚠" not in out

    def test_nonempty_warnings_renders_blocks(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from auto_lorebook.commands.approve_reading import (  # noqa: PLC0415
            _render_outer,  # noqa: PLC2701
        )
        from auto_lorebook.gap_check import GapWarning  # noqa: PLC0415

        w1 = GapWarning(
            start=2050.0,
            end=2902.0,
            segment_ids=("seg-005", "seg-006", "seg-007"),
            segment_titles=("Pizza discussion", "Break", "Rules: initiative"),
        )
        w2 = GapWarning(
            start=5400.0,
            end=6100.0,
            segment_ids=("seg-012",),
            segment_titles=("Silence",),
        )
        summaries = self._make_summaries()
        _render_outer("yt-test", summaries, {}, "Test Session", gap_warnings=[w1, w2])
        out = capsys.readouterr().out
        assert "⚠ Possible coverage gap:" in out
        # w1 timestamps
        assert "0:34:10" in out
        assert "0:48:22" in out
        assert '"Pizza discussion"' in out
        assert '"Break"' in out
        assert '"Rules: initiative"' in out
        assert "If this stretch contained worldbuilding" in out
        # w2 timestamps
        assert "1:30:00" in out
        assert "1:41:40" in out
        # w1 before w2 in output
        idx1 = out.index("0:34:10")
        idx2 = out.index("1:30:00")
        assert idx1 < idx2


class TestExtract:
    def test_refuses_without_plan_yaml(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Extract fails if pending/<id>/plan.yaml doesn't exist."""
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        with caplog.at_level("ERROR"):
            rc = extract_cmd.run(_args(source_id="yt-abc12345678"))
        assert rc == 1
        assert "Run `plan" in caplog.text
        assert "approve-reading" not in caplog.text

    def test_success_writes_proposals(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Full chain: generate → approve → plan → extract writes proposals."""
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")

        client = MagicMock()
        _wire_client_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
            approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=True))
            plan_cmd.run(_args(source_id="yt-abc12345678"))
            rc = extract_cmd.run(_args(source_id="yt-abc12345678"))

        assert rc == 0
        proposals_dir = (
            ingested_wiki / ".wiki-state" / "pending" / "yt-abc12345678" / "proposals"
        )
        assert proposals_dir.is_dir()
        files = sorted(proposals_dir.glob("*.yaml"))
        assert len(files) == 2
        names = {f.name for f in files}
        assert "aldara-f001.yaml" in names
        assert "second-age-f001.yaml" in names

        out = capsys.readouterr().out
        assert "Extracted 2 proposal" in out
        assert "(0 flagged)" in out
