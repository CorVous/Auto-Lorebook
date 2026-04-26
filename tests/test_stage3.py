"""Tests for stage3.py — Stage 3 extractor."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from auto_lorebook import stage3
from auto_lorebook.info_yaml import Info, SourceContext
from auto_lorebook.openrouter import OpenRouterResponse
from auto_lorebook.plan_yaml import (
    ClaimTarget,
    Plan,
    PlannedClaim,
)
from auto_lorebook.srt import Cue
from auto_lorebook.stage3 import Stage3Error
from auto_lorebook.structure import Segment, Structure
from auto_lorebook.transcript import LoadedTranscript


def _info(session_date: str = "2026-01-15") -> Info:
    return Info(
        source_id="yt-x",
        source_type="youtube",
        fetched_at="2026-04-20T00:00:00Z",
        title="t",
        duration_seconds=120,
        session_date=session_date,
        context=SourceContext(),
    )


def _structure(*, segments: list[Segment]) -> Structure:
    return Structure(
        source_id="yt-x",
        generated_at="2026-04-20T00:00:00Z",
        default_speaker="DM",
        segments=segments,
    )


def _transcript(cues: list[Cue], duration: float = 120.0) -> LoadedTranscript:
    return LoadedTranscript(
        text_for_llm="\n".join(f"[{c.start:.0f}] {c.text}" for c in cues) + "\n",
        total_duration=duration,
        cues=tuple(cues),
    )


def _mock_client(text: str) -> MagicMock:
    client = MagicMock()
    client.complete.return_value = OpenRouterResponse(
        text=text, model="m/one", tokens_in=10, tokens_out=20
    )
    return client


def _client_seq(payloads: list[str]) -> MagicMock:
    client = MagicMock()
    client.complete.side_effect = [
        OpenRouterResponse(text=t, model="m/one", tokens_in=10, tokens_out=20)
        for t in payloads
    ]
    return client


def _claim(
    *,
    cg_id: str = "cg-001",
    targets: list[ClaimTarget] | None = None,
    locator_hint: str = "0:00:01-0:00:09",
    reading_section: str = "[0:00:00-0:00:30] Founding of Aldara",
    bullet_idx: int = 0,
) -> PlannedClaim:
    return PlannedClaim(
        claim_group_id=cg_id,
        reading_section=reading_section,
        reading_bullet_index=bullet_idx,
        locator="0:00:05",
        locator_hint=locator_hint,
        proposed_speaker="DM",
        proposed_status="authoritative",
        proposed_status_reason=None,
        targets=targets
        or [
            ClaimTarget(
                entity="Aldara",
                entity_state="existing",
                proposed_section="founding",
                rationale="r",
            ),
        ],
    )


def _aldara_payload() -> str:
    return json.dumps({
        "text": "King Theron rules Aldara.",
        "raw_transcript_span": "King Theron rules Aldara.",
        "text_corrects_transcript": False,
        "corrections_applied": [],
    })


def _default_setup() -> tuple[LoadedTranscript, Structure]:
    cues = [
        Cue(index=1, start=0.0, end=2.0, text="Long ago."),
        Cue(index=2, start=2.0, end=4.0, text="King Theron rules Aldara."),
        Cue(index=3, start=4.0, end=6.0, text="His son is heir."),
        Cue(index=4, start=6.0, end=8.0, text="The realm prospers."),
        Cue(index=5, start=30.0, end=32.0, text="Different topic."),
    ]
    structure = _structure(
        segments=[
            Segment(
                id="seg1",
                start=0.0,
                end=30.0,
                title="Founding of Aldara",
                speaker="DM",
            ),
            Segment(id="seg2", start=30.0, end=120.0, title="Other", speaker="DM"),
        ]
    )
    return _transcript(cues), structure


# ---------------------------------------------------------------------------
# Pre-allocation
# ---------------------------------------------------------------------------


class TestAllocateProposedIds:
    def test_distinct_sequential_ids_for_same_existing_target(self) -> None:
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[
                _claim(cg_id="cg-001"),
                _claim(cg_id="cg-002"),
            ],
        )
        allocations = stage3.allocate_proposed_ids(
            plan,
            existing_fact_counts={"Aldara": 4},
            existing_slugs={"Aldara": "aldara"},
        )
        assert allocations["cg-001"][0].proposed_id == "aldara-f005"
        assert allocations["cg-002"][0].proposed_id == "aldara-f006"

    def test_new_entity_starts_at_f001_and_uses_slugify(self) -> None:
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[
                _claim(
                    cg_id="cg-001",
                    targets=[
                        ClaimTarget(
                            entity="War of the Dusk",
                            entity_state="new",
                            proposed_category="events",
                            proposed_section="overview",
                            rationale="r",
                        ),
                    ],
                ),
            ],
        )
        allocations = stage3.allocate_proposed_ids(
            plan, existing_fact_counts={}, existing_slugs={}
        )
        assert allocations["cg-001"][0].proposed_id == "war-of-the-dusk-f001"

    def test_siblings_exclude_self(self) -> None:
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[
                _claim(
                    cg_id="cg-001",
                    targets=[
                        ClaimTarget(
                            entity="Aldara",
                            entity_state="existing",
                            proposed_section="founding",
                            rationale="r",
                        ),
                        ClaimTarget(
                            entity="Theron",
                            entity_state="existing",
                            proposed_section="lineage",
                            rationale="r",
                        ),
                    ],
                ),
            ],
        )
        allocations = stage3.allocate_proposed_ids(
            plan,
            existing_fact_counts={"Aldara": 0, "Theron": 0},
            existing_slugs={"Aldara": "aldara", "Theron": "theron"},
        )
        a = allocations["cg-001"][0]
        b = allocations["cg-001"][1]
        assert a.proposed_id == "aldara-f001"
        assert b.proposed_id == "theron-f001"
        assert [s.entity for s in a.siblings] == ["Theron"]
        assert [s.entity for s in b.siblings] == ["Aldara"]
        assert a.siblings[0].proposed_id == "theron-f001"


# ---------------------------------------------------------------------------
# Segment lookup
# ---------------------------------------------------------------------------


class TestSegmentLookup:
    def test_matches_segment_by_start_prefix(self) -> None:
        structure = _structure(
            segments=[
                Segment(
                    id="seg1", start=270.0, end=480.0, title="Founding", speaker="DM"
                ),
                Segment(id="seg2", start=480.0, end=720.0, title="War", speaker="DM"),
            ]
        )
        seg = stage3.find_segment_for_reading_section(
            structure, "[4:30-8:00] Founding of Aldara"
        )
        assert seg is not None
        assert seg.id == "seg1"

    def test_returns_none_when_no_prefix(self) -> None:
        structure = _structure(
            segments=[
                Segment(id="seg1", start=0.0, end=10.0, title="x", speaker="DM"),
            ]
        )
        assert (
            stage3.find_segment_for_reading_section(structure, "no prefix here") is None
        )

    def test_returns_none_when_no_match(self) -> None:
        structure = _structure(
            segments=[
                Segment(id="seg1", start=0.0, end=10.0, title="x", speaker="DM"),
            ]
        )
        assert (
            stage3.find_segment_for_reading_section(
                structure, "[1:00:00-1:01:00] Way later"
            )
            is None
        )


# ---------------------------------------------------------------------------
# Run / happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_single_target_emits_one_proposal(self) -> None:
        transcript, structure = _default_setup()
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[_claim()],
        )
        proposals = stage3.run(
            plan=plan,
            transcript=transcript,
            structure=structure,
            info=_info(),
            preamble_text="(preamble)",
            source_id="yt-x",
            client=_mock_client(_aldara_payload()),
            model="m/one",
            existing_fact_counts={"Aldara": 0},
            existing_slugs={"Aldara": "aldara"},
        )
        assert len(proposals) == 1
        p = proposals[0]
        assert p.target_entity == "Aldara"
        assert p.proposed_id == "aldara-f001"
        assert p.proposal_type == "new_fact"
        assert p.text == "King Theron rules Aldara."
        assert p.raw_transcript_span == "King Theron rules Aldara."
        assert p.locator == "0:00:02-0:00:04"
        assert p.context_before == "Long ago."
        assert p.context_after == "His son is heir."
        assert p.session_date == "2026-01-15"
        assert p.speaker == "DM"
        assert p.section == "founding"
        assert p.reading_section == "[0:00:00-0:00:30] Founding of Aldara"
        assert p.hint_widened is False
        assert p.extractor_flagged is False

    def test_corrections_passed_through(self) -> None:
        transcript, structure = _default_setup()
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[_claim()],
        )
        payload = json.dumps({
            "text": "King Theron rules Aldara.",
            "raw_transcript_span": "King Theron rules Aldara.",
            "text_corrects_transcript": True,
            "corrections_applied": [
                {
                    "from": "Fair-on",
                    "to": "Theron",
                    "source": "global-transcription-correction",
                },
            ],
        })
        proposals = stage3.run(
            plan=plan,
            transcript=transcript,
            structure=structure,
            info=_info(),
            preamble_text="",
            source_id="yt-x",
            client=_mock_client(payload),
            model="m/one",
            existing_fact_counts={"Aldara": 0},
            existing_slugs={"Aldara": "aldara"},
        )
        assert proposals[0].text_corrects_transcript is True
        assert len(proposals[0].corrections_applied) == 1
        assert proposals[0].corrections_applied[0].from_ == "Fair-on"


# ---------------------------------------------------------------------------
# Multi-target dedup
# ---------------------------------------------------------------------------


class TestMultiTargetDedup:
    def test_one_call_n_proposals(self) -> None:
        transcript, structure = _default_setup()
        claim = _claim(
            targets=[
                ClaimTarget(
                    entity="Aldara",
                    entity_state="existing",
                    proposed_section="founding",
                    rationale="r",
                ),
                ClaimTarget(
                    entity="Theron",
                    entity_state="existing",
                    proposed_section="lineage",
                    rationale="r",
                ),
                ClaimTarget(
                    entity="Second Age",
                    entity_state="new",
                    proposed_category="events",
                    proposed_section="events-in-era",
                    rationale="r",
                ),
            ],
        )
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[claim],
        )
        client = _mock_client(_aldara_payload())
        proposals = stage3.run(
            plan=plan,
            transcript=transcript,
            structure=structure,
            info=_info(),
            preamble_text="",
            source_id="yt-x",
            client=client,
            model="m/one",
            existing_fact_counts={"Aldara": 0, "Theron": 10},
            existing_slugs={"Aldara": "aldara", "Theron": "theron"},
        )
        # one LLM call, three proposals
        assert client.complete.call_count == 1
        assert len(proposals) == 3
        # span/locator/text shared
        spans = {p.raw_transcript_span for p in proposals}
        assert spans == {"King Theron rules Aldara."}
        assert {p.locator for p in proposals} == {"0:00:02-0:00:04"}
        # ids distinct
        assert {p.proposed_id for p in proposals} == {
            "aldara-f001",
            "theron-f011",
            "second-age-f001",
        }
        # proposal_type per target
        by_entity = {p.target_entity: p for p in proposals}
        assert by_entity["Second Age"].proposal_type == "new_entity_with_facts"
        assert by_entity["Aldara"].proposal_type == "new_fact"
        # siblings exclude self
        assert {s.entity for s in by_entity["Aldara"].claim_group_siblings} == {
            "Theron",
            "Second Age",
        }


# ---------------------------------------------------------------------------
# Widening + flagging
# ---------------------------------------------------------------------------


class TestSubstringFallback:
    def test_widens_to_segment_no_second_llm_call(self) -> None:
        # Hint window covers cues [1..3] (start in [1, 5)); span is in cue at t=4
        cues = [
            Cue(index=1, start=1.0, end=2.0, text="alpha alpha"),
            Cue(index=2, start=4.0, end=5.0, text="THE-MISSING-SPAN beta"),
            Cue(index=3, start=10.0, end=11.0, text="gamma gamma"),
        ]
        transcript = _transcript(cues, duration=30.0)
        structure = _structure(
            segments=[
                Segment(id="seg1", start=0.0, end=20.0, title="Whole", speaker="DM"),
            ]
        )
        # Hint window 1.0-3.0 (only cue 1; span lives outside it)
        claim = _claim(
            locator_hint="0:00:01-0:00:03",
            reading_section="[0:00:00-0:00:20] Whole",
        )
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[claim],
        )
        payload = json.dumps({
            "text": "THE-MISSING-SPAN beta",
            "raw_transcript_span": "THE-MISSING-SPAN beta",
            "text_corrects_transcript": False,
            "corrections_applied": [],
        })
        client = _mock_client(payload)
        proposals = stage3.run(
            plan=plan,
            transcript=transcript,
            structure=structure,
            info=_info(),
            preamble_text="",
            source_id="yt-x",
            client=client,
            model="m/one",
            existing_fact_counts={"Aldara": 0},
            existing_slugs={"Aldara": "aldara"},
        )
        # Only one LLM call, even though we widened mechanically.
        assert client.complete.call_count == 1
        assert len(proposals) == 1
        assert proposals[0].hint_widened is True
        assert proposals[0].extractor_flagged is False
        assert proposals[0].locator == "0:00:04-0:00:10"

    def test_flags_when_span_missing_everywhere(self) -> None:
        transcript, structure = _default_setup()
        claim = _claim()
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[claim],
        )
        payload = json.dumps({
            "text": "Made up text not in transcript.",
            "raw_transcript_span": "Made up text not in transcript.",
            "text_corrects_transcript": False,
            "corrections_applied": [],
        })
        proposals = stage3.run(
            plan=plan,
            transcript=transcript,
            structure=structure,
            info=_info(),
            preamble_text="",
            source_id="yt-x",
            client=_mock_client(payload),
            model="m/one",
            existing_fact_counts={"Aldara": 0},
            existing_slugs={"Aldara": "aldara"},
        )
        assert len(proposals) == 1
        p = proposals[0]
        assert p.extractor_flagged is True
        assert p.flag_reason
        assert p.locator == claim.locator_hint
        assert not p.context_before
        assert not p.context_after
        # text + raw retained as model's last attempt
        assert p.raw_transcript_span == "Made up text not in transcript."

    def test_flagged_proposals_emitted_per_target(self) -> None:
        transcript, structure = _default_setup()
        claim = _claim(
            targets=[
                ClaimTarget(
                    entity="Aldara",
                    entity_state="existing",
                    proposed_section="founding",
                    rationale="r",
                ),
                ClaimTarget(
                    entity="Theron",
                    entity_state="existing",
                    proposed_section="lineage",
                    rationale="r",
                ),
            ],
        )
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[claim],
        )
        payload = json.dumps({
            "text": "missing",
            "raw_transcript_span": "missing",
            "text_corrects_transcript": False,
            "corrections_applied": [],
        })
        proposals = stage3.run(
            plan=plan,
            transcript=transcript,
            structure=structure,
            info=_info(),
            preamble_text="",
            source_id="yt-x",
            client=_mock_client(payload),
            model="m/one",
            existing_fact_counts={"Aldara": 0, "Theron": 0},
            existing_slugs={"Aldara": "aldara", "Theron": "theron"},
        )
        assert len(proposals) == 2
        assert all(p.extractor_flagged for p in proposals)


# ---------------------------------------------------------------------------
# Parallelism + errors
# ---------------------------------------------------------------------------


class TestParallel:
    def test_one_llm_call_per_claim(self) -> None:
        transcript, structure = _default_setup()
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[
                _claim(cg_id="cg-001"),
                _claim(cg_id="cg-002"),
                _claim(cg_id="cg-003"),
            ],
        )
        client = _mock_client(_aldara_payload())
        proposals = stage3.run(
            plan=plan,
            transcript=transcript,
            structure=structure,
            info=_info(),
            preamble_text="",
            source_id="yt-x",
            client=client,
            model="m/one",
            existing_fact_counts={"Aldara": 0},
            existing_slugs={"Aldara": "aldara"},
        )
        # 3 claims with 1 target each = 3 proposals; 3 LLM calls (one per claim).
        assert client.complete.call_count == 3
        assert len(proposals) == 3


class TestErrors:
    def test_malformed_json_raises_stage3error(self) -> None:
        transcript, structure = _default_setup()
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[_claim()],
        )
        with pytest.raises(Stage3Error):
            stage3.run(
                plan=plan,
                transcript=transcript,
                structure=structure,
                info=_info(),
                preamble_text="",
                source_id="yt-x",
                client=_mock_client("not-json"),
                model="m/one",
                existing_fact_counts={"Aldara": 0},
                existing_slugs={"Aldara": "aldara"},
            )

    def test_plain_text_transcript_raises(self) -> None:
        transcript = LoadedTranscript(
            text_for_llm="plain", total_duration=10.0, cues=None
        )
        structure = _structure(
            segments=[
                Segment(id="seg1", start=0.0, end=10.0, title="x", speaker="DM"),
            ]
        )
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[_claim()],
        )
        with pytest.raises(Stage3Error, match="SRT"):
            stage3.run(
                plan=plan,
                transcript=transcript,
                structure=structure,
                info=_info(),
                preamble_text="",
                source_id="yt-x",
                client=_mock_client(_aldara_payload()),
                model="m/one",
                existing_fact_counts={"Aldara": 0},
                existing_slugs={"Aldara": "aldara"},
            )

    def test_empty_plan_returns_empty_list(self) -> None:
        transcript, structure = _default_setup()
        plan = Plan(
            source_id="yt-x",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[],
        )
        out = stage3.run(
            plan=plan,
            transcript=transcript,
            structure=structure,
            info=_info(),
            preamble_text="",
            source_id="yt-x",
            client=_mock_client(_aldara_payload()),
            model="m/one",
            existing_fact_counts={},
            existing_slugs={},
        )
        assert out == []
