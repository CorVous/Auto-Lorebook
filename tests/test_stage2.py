"""Tests for stage2.py — Stage 2 planner."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from auto_lorebook.openrouter import OpenRouterResponse
from auto_lorebook.stage1b import Bullet, ReadingBullets
from auto_lorebook.stage2 import Stage2Error, run
from auto_lorebook.structure import Segment, Structure, UncertaintyFlag


def _mock_client(text: str) -> MagicMock:
    client = MagicMock()
    client.complete.return_value = OpenRouterResponse(
        text=text, model="m/one", tokens_in=10, tokens_out=20
    )
    return client


def _structure() -> Structure:
    return Structure(
        source_id="yt-x",
        generated_at="2026-04-20T00:00:00Z",
        default_speaker="DM",
        segments=[
            Segment(
                id="seg-001",
                start=0.0,
                end=60.0,
                title="Founding of Aldara",
                speaker="DM",
            ),
            Segment(
                id="seg-002",
                start=60.0,
                end=120.0,
                title="The War of the Dusk",
                speaker="DM",
            ),
        ],
        uncertainty_flags=[
            UncertaintyFlag(
                locator=70.0,
                span="elven sorceress",
                kind="name",
                note="unnamed",
            )
        ],
    )


def _bullets() -> ReadingBullets:
    return ReadingBullets(
        source_id="yt-x",
        generated_at="2026-04-20T00:00:00Z",
        segments={
            "seg-001": [
                Bullet(
                    text=(
                        "Aldara was founded in the Second Age by Theron's grandfather."
                    ),
                    anchor=30.0,
                    locator_hint_start=20.0,
                    locator_hint_end=45.0,
                ),
            ],
            "seg-002": [
                Bullet(
                    text="The War of the Dusk lasted seven years.",
                    anchor=80.0,
                    locator_hint_start=70.0,
                    locator_hint_end=95.0,
                ),
            ],
        },
    )


def _well_formed_payload() -> str:
    return json.dumps({
        "entity_resolutions": [
            {
                "mention": "Aldara",
                "mention_locations": ["[0:00:00-0:01:00] founding"],
                "resolution": "existing",
                "matched_entity": "Aldara",
                "rationale": "Direct mention.",
            },
            {
                "mention": "the War of the Dusk",
                "mention_locations": ["[0:01:00-0:02:00] war"],
                "resolution": "new",
                "proposed_entity_name": "War of the Dusk",
                "proposed_category": "events",
                "rationale": "No existing match.",
            },
            {
                "mention": "the elven sorceress",
                "mention_locations": ["[0:01:10] hearsay"],
                "resolution": "ambiguous",
                "rationale": "Unnamed referent.",
                "human_review_needed": True,
            },
        ],
        "new_entities": [
            {"name": "War of the Dusk", "category": "events"},
        ],
        "planned_claims": [
            {
                "claim_group_id": "cg-001",
                "reading_section": "[0:00:00-0:01:00] Founding of Aldara",
                "reading_bullet_index": 0,
                "locator": "0:00:30",
                "locator_hint": "0:00:20-0:00:45",
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
                        "entity": "Theron",
                        "entity_state": "existing",
                        "proposed_section": "lineage",
                        "rationale": "Names grandfather as founder.",
                    },
                    {
                        "entity": "Second Age",
                        "entity_state": "new",
                        "proposed_section": "events-in-era",
                        "proposed_category": "events",
                        "rationale": "Dates the founding.",
                    },
                ],
            },
            {
                "claim_group_id": "cg-002",
                "reading_section": "[0:01:00-0:02:00] The War of the Dusk",
                "reading_bullet_index": 0,
                "locator": "0:01:20",
                "locator_hint": "0:01:10-0:01:35",
                "proposed_speaker": "DM",
                "proposed_status": "authoritative",
                "proposed_status_reason": None,
                "targets": [
                    {
                        "entity": "War of the Dusk",
                        "entity_state": "new",
                        "proposed_section": "overview",
                        "proposed_category": "events",
                        "rationale": "Duration of the war.",
                    },
                ],
            },
        ],
        "unresolved": [
            {
                "reading_section": "[0:01:00-0:02:00] The War of the Dusk",
                "locator": "0:01:10",
                "issue": "Reading flagged uncertain name.",
            }
        ],
    })


class TestRun:
    def test_happy_path(self) -> None:
        client = _mock_client(_well_formed_payload())
        plan = run(
            reading_text="# Reading\n\n## Segment\n\n- claim",
            structure=_structure(),
            bullets=_bullets(),
            preamble_text="## Setting\n(none)",
            source_id="yt-x",
            client=client,
            model="m/one",
        )
        assert plan.source_id == "yt-x"
        assert plan.planned_at  # set by stage2 (ISO now)
        assert len(plan.entity_resolutions) == 3
        assert len(plan.new_entities) == 1
        assert plan.new_entities[0].name == "War of the Dusk"
        assert len(plan.planned_claims) == 2
        assert len(plan.unresolved) == 1

    def test_routes_one_claim_to_multiple_targets(self) -> None:
        client = _mock_client(_well_formed_payload())
        plan = run(
            reading_text="# Reading",
            structure=_structure(),
            bullets=_bullets(),
            preamble_text="",
            source_id="yt-x",
            client=client,
            model="m/one",
        )
        first = plan.planned_claims[0]
        assert len(first.targets) == 3
        assert {t.entity for t in first.targets} == {
            "Aldara",
            "Theron",
            "Second Age",
        }
        # The new-entity target carries proposed_category
        new_targets = [t for t in first.targets if t.entity_state == "new"]
        assert len(new_targets) == 1
        assert new_targets[0].proposed_category == "events"

    def test_sends_preamble_and_bullets(self) -> None:
        client = _mock_client(_well_formed_payload())
        run(
            reading_text="# Reading body",
            structure=_structure(),
            bullets=_bullets(),
            preamble_text="## Setting\n(none)",
            source_id="yt-x",
            client=client,
            model="m/seven",
        )
        client.complete.assert_called_once()
        call = client.complete.call_args
        messages = call.args[0]
        kwargs = call.kwargs
        assert kwargs["model"] == "m/seven"
        assert kwargs["response_format"] == {"type": "json_object"}
        assert any(
            m["role"] == "system" and "Setting" in m["content"] for m in messages
        )
        user_msg = next(m["content"] for m in messages if m["role"] == "user")
        assert "Reading body" in user_msg
        # bullet text + segment ids surfaced for the model
        assert "seg-001" in user_msg
        assert "Aldara was founded" in user_msg

    def test_invalid_json_raises(self) -> None:
        client = _mock_client("not valid json at all")
        with pytest.raises(Stage2Error, match="JSON"):
            run(
                reading_text="",
                structure=_structure(),
                bullets=_bullets(),
                preamble_text="",
                source_id="yt-x",
                client=client,
                model="m/one",
            )

    def test_unknown_resolution_raises(self) -> None:
        bad = json.dumps({
            "entity_resolutions": [
                {"mention": "X", "resolution": "wat"},
            ],
        })
        client = _mock_client(bad)
        with pytest.raises(Stage2Error, match="resolution"):
            run(
                reading_text="",
                structure=_structure(),
                bullets=_bullets(),
                preamble_text="",
                source_id="yt-x",
                client=client,
                model="m/one",
            )

    def test_empty_plan_is_valid(self) -> None:
        client = _mock_client(json.dumps({}))
        plan = run(
            reading_text="",
            structure=_structure(),
            bullets=_bullets(),
            preamble_text="",
            source_id="yt-x",
            client=client,
            model="m/one",
        )
        assert plan.entity_resolutions == []
        assert plan.new_entities == []
        assert plan.planned_claims == []
        assert plan.unresolved == []
