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
from auto_lorebook import db as db_mod
from auto_lorebook import entities as entities_mod
from auto_lorebook import facts as facts_mod
from auto_lorebook import proposal_yaml
from auto_lorebook import review as review_mod
from auto_lorebook import wiki_state as wiki_state_mod
from auto_lorebook.commands import (
    approve_reading_cmd,
    extract_cmd,
    generate_reading_cmd,
    plan_cmd,
    review_cmd,
)
from auto_lorebook.commands.review import InteractiveReviewer
from auto_lorebook.openrouter import OpenRouterResponse
from auto_lorebook.review import (
    ApproveDecision,
    BundleDecision,
    BundleView,
    RejectDecision,
    TargetView,
)

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path


def _open_db(wiki: Path) -> sqlite3.Connection:
    return db_mod.open(wiki_state_mod.wiki_db_path(wiki))


def _db_facts(wiki: Path, category: str, slug: str) -> list[facts_mod.FactRow]:
    conn = _open_db(wiki)
    try:
        return facts_mod.list_facts_by_entity(conn, category, slug)
    finally:
        conn.close()


def _db_entity(wiki: Path, category: str, slug: str) -> entities_mod.EntityRow | None:
    conn = _open_db(wiki)
    try:
        return entities_mod.get_entity(conn, category, slug)
    finally:
        conn.close()


def _db_proposal_count(wiki: Path, source_id: str) -> int:
    conn = _open_db(wiki)
    try:
        return proposal_yaml.count_proposals(conn, source_id)
    finally:
        conn.close()


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
            assert (
                approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=True))
                == 0
            )
            plan_cmd.run(_args(source_id="yt-abc12345678"))
            extract_cmd.run(_args(source_id="yt-abc12345678"))
            rc = review_cmd.run(_args(source_id="yt-abc12345678", auto_approve=True))
        assert rc == 0
        # Stub created — DB has entity + .md written
        ent = _db_entity(ingested_wiki, "locations", "aldara")
        assert ent is not None
        assert ent.canonical_name == "Aldara"
        assert ent.created_by_ingest == "yt-abc12345678"
        db_facts = _db_facts(ingested_wiki, "locations", "aldara")
        assert len(db_facts) == 1
        assert db_facts[0].id == "aldara-f001"
        assert (ingested_wiki / "locations" / "aldara.md").exists()
        # No proposals remain in DB
        assert _db_proposal_count(ingested_wiki, "yt-abc12345678") == 0
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
            approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=True))
            plan_cmd.run(_args(source_id="yt-abc12345678"))
            extract_cmd.run(_args(source_id="yt-abc12345678"))
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
            assert (
                approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=True))
                == 0
            )
            plan_cmd.run(_args(source_id="yt-abc12345678"))
            extract_cmd.run(_args(source_id="yt-abc12345678"))
            rc = review_cmd.run(_args(source_id="yt-abc12345678", auto_approve=True))
        assert rc == 0

        # All three stubs created in DB + .md written.
        for slug, cat in (
            ("aldara", "locations"),
            ("theron", "characters"),
            ("second-age", "events"),
        ):
            ent = _db_entity(ingested_wiki, cat, slug)
            assert ent is not None, f"{slug} entity missing from DB"
            assert ent.created_by_ingest == "yt-abc12345678"
            assert (ingested_wiki / cat / f"{slug}.md").exists(), f"{slug}.md missing"
            db_facts = _db_facts(ingested_wiki, cat, slug)
            assert len(db_facts) == 1

        # No proposals remain in DB.
        assert _db_proposal_count(ingested_wiki, "yt-abc12345678") == 0

        # Count: approved=1 (one proposal, three fact_targets).
        out = capsys.readouterr().out
        assert "approved=1" in out

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
            assert (
                approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=True))
                == 0
            )
            plan_cmd.run(_args(source_id="yt-abc12345678"))
            extract_cmd.run(_args(source_id="yt-abc12345678"))

        result = review_mod.run(
            cfg=cfg_mod.load_config(),
            source_id="yt-abc12345678",
            reviewer=_DropFirstRouteReviewer(),
        )

        # Theron and Second Age stubs exist; Aldara's proposal was dropped.
        assert _db_entity(ingested_wiki, "locations", "aldara") is None, (
            "Aldara should not have been created"
        )
        assert not (ingested_wiki / "locations" / "aldara.md").exists()
        assert _db_entity(ingested_wiki, "characters", "theron") is not None, (
            "Theron stub missing"
        )
        assert _db_entity(ingested_wiki, "events", "second-age") is not None, (
            "Second Age stub missing"
        )

        # one proposal approved (with 2/3 targets selected); no proposals rejected
        assert result.approved == 1
        assert result.rejected == 0

        # No proposals remain in DB.
        assert _db_proposal_count(ingested_wiki, "yt-abc12345678") == 0
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
            assert (
                approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=True))
                == 0
            )
            plan_cmd.run(_args(source_id="yt-abc12345678"))
            extract_cmd.run(_args(source_id="yt-abc12345678"))

        result = review_mod.run(
            cfg=cfg_mod.load_config(),
            source_id="yt-abc12345678",
            reviewer=_RejectAllReviewer(),
        )

        # one proposal rejected
        assert result.rejected == 1
        assert result.approved == 0
        assert _db_entity(ingested_wiki, "locations", "aldara") is None
        assert _db_entity(ingested_wiki, "characters", "theron") is None
        assert _db_entity(ingested_wiki, "events", "second-age") is None
        assert not (ingested_wiki / "locations" / "aldara.md").exists()
        assert not (ingested_wiki / "characters" / "theron.md").exists()
        assert not (ingested_wiki / "events" / "second-age.md").exists()
        # No proposals remain in DB.
        assert _db_proposal_count(ingested_wiki, "yt-abc12345678") == 0
        capsys.readouterr()


# ---------------------------------------------------------------------------
# InteractiveReviewer [u]ndo
# ---------------------------------------------------------------------------


def _make_proposal_for_view(
    *, target: str, proposed_id: str, section: str = "founding"
) -> proposal_yaml.Proposal:
    from auto_lorebook.proposal_yaml import ProposalTarget  # noqa: PLC0415

    return proposal_yaml.Proposal(
        proposed_id=proposed_id,
        claim_group_id="cg-001",
        targets=[
            ProposalTarget(
                entity=target,
                section=section,
                speaker="DM",
                proposal_type="new_entity_with_facts",
            ),
        ],
        text=f"{target} was founded in the Second Age.",
        raw_transcript_span=f"{target} was founded in the Second Age.",
        text_corrects_transcript=False,
        corrections_applied=[],
        source_id="yt-x",
        locator="0:00:08-0:00:18",
        reading_section="[0:00:00-0:00:30] Founding",
        reading_bullet_index=0,
        status="authoritative",
        session_date="2026-04-15",
        context_before="",
        context_after="",
    )


def _two_target_view() -> BundleView:
    p_aldara = _make_proposal_for_view(target="Aldara", proposed_id="aldara-f001")
    p_theron = _make_proposal_for_view(
        target="Theron", proposed_id="theron-f001", section="lineage"
    )
    targets = (
        TargetView(
            proposal=p_aldara,
            target=p_aldara.targets[0],
            is_new_entity=True,
            new_entity_category="locations",
            created_earlier_in_session=False,
            suggested_aliases=(),
            matched_via=None,
        ),
        TargetView(
            proposal=p_theron,
            target=p_theron.targets[0],
            is_new_entity=True,
            new_entity_category="characters",
            created_earlier_in_session=False,
            suggested_aliases=(),
            matched_via=None,
        ),
    )
    return BundleView(
        bundle_index=1,
        bundle_total=1,
        claim_group_id="cg-001",
        targets=targets,
        source_url=None,
        source_title="Test source",
    )


def _scripted_input(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    """Patch builtins.input to feed `lines` in order."""
    it = iter(lines)
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(it))


class TestInteractiveReviewerUndo:
    """[u]ndo resets bundle edits, target selection, and per-target overrides."""

    def test_undo_clears_bundle_edits(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        view = _two_target_view()
        _scripted_input(
            monkeypatch,
            [
                "e",  # enter edit
                "Edited text.",  # bundle text override
                "",  # status (keep)
                "",  # status_reason (keep)
                "u",  # undo
                "a",  # approve
            ],
        )
        decision = InteractiveReviewer().decide_bundle(view)
        capsys.readouterr()
        assert isinstance(decision.decision, ApproveDecision)
        assert decision.selected_indices == (0, 1)
        assert decision.per_target_overrides == {}

    def test_undo_re_checks_toggled_off_routes(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        view = _two_target_view()
        _scripted_input(
            monkeypatch,
            [
                "t",  # enter targets sub-prompt
                "toggle 1",  # uncheck Aldara (route 0)
                "done",
                "u",  # undo re-checks all routes
                "a",  # approve
            ],
        )
        decision = InteractiveReviewer().decide_bundle(view)
        capsys.readouterr()
        assert decision.selected_indices == (0, 1)

    def test_undo_clears_per_target_override(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        view = _two_target_view()
        _scripted_input(
            monkeypatch,
            [
                "t",  # enter targets sub-prompt
                "edit 2",  # override Theron (route 1)
                "ancestry",  # new section
                "Player-Thorin",  # new speaker
                "done",
                "u",  # undo
                "a",  # approve
            ],
        )
        decision = InteractiveReviewer().decide_bundle(view)
        capsys.readouterr()
        assert decision.per_target_overrides == {}
        assert isinstance(decision.decision, ApproveDecision)

    def test_undo_after_no_changes_is_safe_noop(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        view = _two_target_view()
        _scripted_input(monkeypatch, ["u", "a"])
        decision = InteractiveReviewer().decide_bundle(view)
        capsys.readouterr()
        assert isinstance(decision.decision, ApproveDecision)
        assert decision.selected_indices == (0, 1)

    def test_unknown_choice_still_reprompts(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Unknown-choice path lists [u]ndo alongside the other actions."""
        view = _two_target_view()
        _scripted_input(monkeypatch, ["x", "a"])
        decision = InteractiveReviewer().decide_bundle(view)
        out = capsys.readouterr().out
        assert "unknown choice" in out
        assert "u" in out  # undo advertised in the hint
        assert isinstance(decision.decision, ApproveDecision)


# ---------------------------------------------------------------------------
# End-to-end: undo through the full pipeline
# ---------------------------------------------------------------------------


class TestInteractiveReviewerUndoIntegration:
    """End-to-end stdin-driven review with [u]ndo through generate→approve→review."""

    def test_undo_reverses_toggle_and_edit_through_pipeline(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Toggle off route 2, enter a bundle edit, [u]ndo, [a]pprove.

        After undo, all three stubs should be created and every fact's
        text should match the original (untouched) proposal text.
        """
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        monkeypatch.setattr(
            "auto_lorebook.commands.review._is_interactive", lambda: True
        )
        scripted = iter([
            "t",  # enter targets sub-prompt
            "toggle 2",  # uncheck Theron
            "done",
            "e",  # enter bundle edit
            "Tampered text.",  # bundle text override
            "",  # status (keep)
            "",  # status_reason (keep)
            "u",  # undo: re-checks Theron AND clears edits
            "a",  # approve all three routes
        ])
        monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(scripted))

        client = MagicMock()
        _wire_multi_target_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
            assert (
                approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=True))
                == 0
            )
            plan_cmd.run(_args(source_id="yt-abc12345678"))
            extract_cmd.run(_args(source_id="yt-abc12345678"))
            rc = review_cmd.run(_args(source_id="yt-abc12345678", auto_approve=False))
        assert rc == 0

        # All three stubs created — undo re-checked the toggled-off route.
        original_text = "King Theron founded Aldara in the Second Age."
        for slug, cat in (
            ("aldara", "locations"),
            ("theron", "characters"),
            ("second-age", "events"),
        ):
            ent = _db_entity(ingested_wiki, cat, slug)
            assert ent is not None, f"{slug} stub missing; undo didn't re-check route"
            assert (ingested_wiki / cat / f"{slug}.md").exists()
            db_facts = _db_facts(ingested_wiki, cat, slug)
            assert len(db_facts) == 1
            # Undo dropped the bundle edit; original text preserved.
            assert db_facts[0].text == original_text
            assert db_facts[0].text_source is None
            assert db_facts[0].edited_by_human is False

        # No proposals remain in DB.
        assert _db_proposal_count(ingested_wiki, "yt-abc12345678") == 0
        out = capsys.readouterr().out
        assert "approved=1" in out

    def test_undo_then_reject_still_rejects_whole_bundle(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """[u]ndo doesn't block a subsequent [r]eject — rejection still wins."""
        _write_user_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        monkeypatch.setattr(
            "auto_lorebook.commands.review._is_interactive", lambda: True
        )
        scripted = iter([
            "e",  # enter edit
            "Tampered text.",
            "",
            "",
            "u",  # undo edit
            "r",  # reject the whole bundle
        ])
        monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(scripted))

        client = MagicMock()
        _wire_multi_target_responses(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id="yt-abc12345678"))
            assert (
                approve_reading_cmd.run(_args(source_id="yt-abc12345678", yes=True))
                == 0
            )
            plan_cmd.run(_args(source_id="yt-abc12345678"))
            extract_cmd.run(_args(source_id="yt-abc12345678"))
            rc = review_cmd.run(_args(source_id="yt-abc12345678", auto_approve=False))
        assert rc == 0

        for slug, cat in (
            ("aldara", "locations"),
            ("theron", "characters"),
            ("second-age", "events"),
        ):
            assert _db_entity(ingested_wiki, cat, slug) is None
            assert not (ingested_wiki / cat / f"{slug}.md").exists()

        # No proposals remain in DB.
        assert _db_proposal_count(ingested_wiki, "yt-abc12345678") == 0
        out = capsys.readouterr().out
        assert "rejected=1" in out
