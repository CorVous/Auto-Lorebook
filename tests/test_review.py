"""Tests for review.py — Stage 4 (review) engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook import config as cfg_mod
from auto_lorebook import (
    entity_yaml,
    plan_yaml,
    proposal_yaml,
    reading_pipeline,
    review,
)
from auto_lorebook.review import (
    ApproveDecision,
    BundleDecision,
    BundleView,
    Decision,
    EditDecision,
    RejectDecision,
    ReviewResult,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg(
    tmp_path: Path, tmp_wiki: Path, monkeypatch: pytest.MonkeyPatch
) -> cfg_mod.Config:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    return cfg_mod.Config(wiki_repo_path=tmp_wiki)


def _write_info(wiki: Path, source_id: str = "yt-x") -> None:
    src = wiki / "sources" / source_id
    src.mkdir(parents=True, exist_ok=True)
    (src / "info.yaml").write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "source_id": source_id,
            "source_type": "youtube",
            "source_url": "https://example.com/v?x=1",
            "title": "Test source",
            "duration_seconds": 600,
            "fetched_at": "2026-04-20T00:00:00Z",
            "session_date": "2026-04-15",
            "transcript_filename": "transcript.en.srt",
            "context": {},
        }),
        encoding="utf-8",
    )


def _write_plan(
    wiki: Path,  # noqa: ARG001
    source_id: str,
    *,
    new_entities: list[plan_yaml.NewEntityProposal] | None = None,
    entity_resolutions: list[plan_yaml.EntityResolution] | None = None,
    planned_claims: list[plan_yaml.PlannedClaim] | None = None,
) -> None:
    plan = plan_yaml.Plan(
        source_id=source_id,
        planned_at="2026-04-20T00:00:00Z",
        new_entities=new_entities or [],
        entity_resolutions=entity_resolutions or [],
        planned_claims=planned_claims or [],
    )
    plan_yaml.write(plan, reading_pipeline.pending_plan_path(source_id))


def _write_proposal(source_id: str, p: proposal_yaml.Proposal) -> None:
    path = reading_pipeline.pending_proposal_path(source_id, p.proposed_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    proposal_yaml.write(p, path)


def _make_proposal(
    *,
    target: str,
    proposed_id: str,
    cg: str = "cg-001",
    proposal_type: str = "new_entity_with_facts",
    siblings: list[proposal_yaml.Sibling] | None = None,
    text: str = "Aldara was founded in the Second Age.",
    locator: str = "0:00:08-0:00:18",
    section: str = "founding",
) -> proposal_yaml.Proposal:
    return proposal_yaml.Proposal(
        proposal_type=proposal_type,
        target_entity=target,
        proposed_id=proposed_id,
        claim_group_id=cg,
        claim_group_siblings=siblings or [],
        text=text,
        raw_transcript_span=text,
        text_corrects_transcript=False,
        corrections_applied=[],
        source_id="yt-x",
        locator=locator,
        speaker="DM",
        reading_section="[0:00:00-0:00:30] Founding",
        reading_bullet_index=0,
        status="authoritative",
        status_reason=None,
        session_date="2026-04-15",
        section=section,
        context_before="",
        context_after="",
    )


def _make_claim(
    *,
    cg: str = "cg-001",
    targets: list[plan_yaml.ClaimTarget],
) -> plan_yaml.PlannedClaim:
    return plan_yaml.PlannedClaim(
        claim_group_id=cg,
        reading_section="[0:00:00-0:00:30] Founding",
        reading_bullet_index=0,
        locator="0:00:08",
        locator_hint="0:00:00-0:00:30",
        proposed_speaker="DM",
        proposed_status="authoritative",
        proposed_status_reason=None,
        targets=targets,
    )


class ScriptedReviewer:
    """Bundle reviewer with scripted bundle decisions and alias responses.

    Existing tests pass a list of `Decision`s — we wrap each as a
    bundle-wide decision selecting every route. New bundling tests can
    construct `BundleDecision` directly via `bundle_decisions=`.
    """

    by_label = "human-review"

    def __init__(
        self,
        decisions: list[Decision] | None = None,
        alias_responses: list[bool] | None = None,
        *,
        bundle_decisions: list[BundleDecision] | None = None,
    ) -> None:
        if decisions is not None and bundle_decisions is not None:
            msg = "pass decisions OR bundle_decisions, not both"
            raise AssertionError(msg)
        self._decisions: list[Decision] = list(decisions or [])
        self._bundle_decisions: list[BundleDecision] = list(bundle_decisions or [])
        self._alias_responses = list(alias_responses or [])
        self.decided: list[BundleView] = []
        self.alias_calls: list[tuple[str, str]] = []

    def decide_bundle(self, view: BundleView) -> BundleDecision:
        self.decided.append(view)
        if self._bundle_decisions:
            return self._bundle_decisions.pop(0)
        if not self._decisions:
            msg = "ScriptedReviewer ran out of decisions"
            raise AssertionError(msg)
        decision = self._decisions.pop(0)
        return BundleDecision(
            decision=decision,
            selected_indices=tuple(range(len(view.targets))),
        )

    def confirm_alias(self, entity: str, mention: str) -> bool:
        self.alias_calls.append((entity, mention))
        if not self._alias_responses:
            return False
        return self._alias_responses.pop(0)


def _seed_existing_entity(
    wiki: Path,
    *,
    name: str = "Aldara",
    category: str = "locations",
    slug: str = "aldara",
    facts: list[dict] | None = None,
) -> None:
    e = entity_yaml.Entity(
        entity=name,
        category=category,
        slug=slug,
        created_at="2026-01-01T00:00:00Z",
        created_by_ingest="prior-ingest",
        updated_at="2026-01-01T00:00:00Z",
        facts=facts or [],
    )
    entity_yaml.write(e, wiki / category / f"{slug}.yaml")


# ---------------------------------------------------------------------------
# Walk order
# ---------------------------------------------------------------------------


class TestSortedProposals:
    def test_groups_siblings_in_target_order(self, cfg: cfg_mod.Config) -> None:
        """Plan order beats lex sort: targets ordered as in claim.targets."""
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        # cg-001: War of the Dusk first, then Aldara — slug-sort would invert.
        claim1 = _make_claim(
            cg="cg-001",
            targets=[
                plan_yaml.ClaimTarget(
                    entity="War of the Dusk",
                    entity_state="new",
                    proposed_section="overview",
                    proposed_category="events",
                ),
                plan_yaml.ClaimTarget(
                    entity="Aldara",
                    entity_state="new",
                    proposed_section="founding",
                    proposed_category="locations",
                ),
            ],
        )
        claim2 = _make_claim(
            cg="cg-002",
            targets=[
                plan_yaml.ClaimTarget(
                    entity="Theron",
                    entity_state="new",
                    proposed_section="lineage",
                    proposed_category="characters",
                ),
            ],
        )
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(name="War of the Dusk", category="events"),
                plan_yaml.NewEntityProposal(name="Aldara", category="locations"),
                plan_yaml.NewEntityProposal(name="Theron", category="characters"),
            ],
            planned_claims=[claim1, claim2],
        )
        for p in [
            _make_proposal(
                target="War of the Dusk", proposed_id="war-of-the-dusk-f001"
            ),
            _make_proposal(target="Aldara", proposed_id="aldara-f001"),
            _make_proposal(target="Theron", proposed_id="theron-f001", cg="cg-002"),
        ]:
            _write_proposal(source_id, p)

        plan = plan_yaml.read(reading_pipeline.pending_plan_path(source_id))
        ordered = review.sorted_proposals(plan, source_id)
        assert [p.target_entity for p in ordered] == [
            "War of the Dusk",
            "Aldara",
            "Theron",
        ]


# ---------------------------------------------------------------------------
# Fact dict shape
# ---------------------------------------------------------------------------


class TestFactDict:
    def test_plain_approve(self) -> None:
        p = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        fact = review.proposal_to_fact_dict(
            p, edits=None, ingest_id="yt-x", by_label="human-review"
        )
        assert fact["id"] == "aldara-f001"
        assert fact["text"] == p.text
        assert fact["edited_by_human"] is False
        assert fact["edited_at"] is None
        assert fact["text_source"] is None
        assert fact["created_by_ingest"] == "yt-x"
        assert fact["section"] == "founding"
        assert fact["claim_group_id"] == "cg-001"
        assert fact["status_history"][0]["by"] == "human-review"
        assert fact["status_history"][0]["status"] == "authoritative"

    def test_edit_path_preserves_original_in_text_source(self) -> None:
        p = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        fact = review.proposal_to_fact_dict(
            p,
            edits=EditDecision(new_text="Aldara was founded earlier than that."),
            ingest_id="yt-x",
            by_label="human-review",
        )
        assert fact["text"] == "Aldara was founded earlier than that."
        assert fact["text_source"] == p.text
        assert fact["edited_by_human"] is True
        assert fact["edited_at"] is not None
        # human edit forces text_corrects_transcript even if original was False
        assert fact["text_corrects_transcript"] is True

    def test_by_label_passed_through(self) -> None:
        p = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        fact = review.proposal_to_fact_dict(
            p, edits=None, ingest_id="yt-x", by_label="auto-approve"
        )
        assert fact["status_history"][0]["by"] == "auto-approve"

    def test_speaker_status_section_overrides(self) -> None:
        p = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        fact = review.proposal_to_fact_dict(
            p,
            edits=EditDecision(
                new_speaker="Player-Thorin",
                new_status="hearsay",
                new_status_reason="Speaker is a tavern rumor source.",
                new_section="legends",
            ),
            ingest_id="yt-x",
            by_label="human-review",
        )
        assert fact["speaker"] == "Player-Thorin"
        assert fact["status"] == "hearsay"
        assert fact["status_reason"] == "Speaker is a tavern rumor source."
        assert fact["section"] == "legends"
        # status_history reflects the *edited* status, not the proposal's.
        assert fact["status_history"][0]["status"] == "hearsay"
        assert (
            fact["status_history"][0]["reason"] == "Speaker is a tavern rumor source."
        )
        # Text untouched, so edited_by_human stays false.
        assert fact["edited_by_human"] is False
        assert fact["text_source"] is None

    def test_partial_edit_only_changes_specified_fields(self) -> None:
        p = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        fact = review.proposal_to_fact_dict(
            p,
            edits=EditDecision(new_status="trustworthy"),
            ingest_id="yt-x",
            by_label="human-review",
        )
        assert fact["status"] == "trustworthy"
        assert fact["speaker"] == p.speaker  # untouched
        assert fact["section"] == p.section  # untouched
        assert fact["text"] == p.text  # untouched

    def test_noop_edit_keeps_proposal_values(self) -> None:
        p = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        fact = review.proposal_to_fact_dict(
            p,
            edits=EditDecision(),  # no overrides
            ingest_id="yt-x",
            by_label="human-review",
        )
        assert fact["text"] == p.text
        assert fact["speaker"] == p.speaker
        assert fact["status"] == p.status
        assert fact["section"] == p.section
        assert fact["edited_by_human"] is False


# ---------------------------------------------------------------------------
# Existing-entity approve
# ---------------------------------------------------------------------------


class TestExistingEntityApprove:
    def test_appends_fact_and_deletes_proposal(self, cfg: cfg_mod.Config) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        _seed_existing_entity(cfg.wiki_repo_path, name="Aldara", slug="aldara")

        proposal = _make_proposal(
            target="Aldara", proposed_id="aldara-f001", proposal_type="new_fact"
        )
        _write_proposal(source_id, proposal)
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            entity_resolutions=[
                plan_yaml.EntityResolution(
                    mention="Aldara",
                    resolution="existing",
                    matched_entity="Aldara",
                ),
            ],
            planned_claims=[
                _make_claim(
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="existing",
                            proposed_section="founding",
                        ),
                    ],
                ),
            ],
        )

        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()]),
        )
        assert result.approved == 1
        assert result.rejected == 0
        # proposal file gone
        assert not reading_pipeline.pending_proposal_path(
            source_id, "aldara-f001"
        ).exists()
        # entity grew by one fact
        e = entity_yaml.read(cfg.wiki_repo_path / "locations" / "aldara.yaml")
        assert len(e.facts) == 1
        assert e.facts[0]["id"] == "aldara-f001"


# ---------------------------------------------------------------------------
# Idempotent re-approval guard
# ---------------------------------------------------------------------------


class TestIdempotentGuard:
    def test_does_not_double_append(self, cfg: cfg_mod.Config) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        _seed_existing_entity(
            cfg.wiki_repo_path,
            name="Aldara",
            slug="aldara",
            facts=[{"id": "aldara-f001", "text": "previously approved."}],
        )

        proposal = _make_proposal(
            target="Aldara", proposed_id="aldara-f001", proposal_type="new_fact"
        )
        _write_proposal(source_id, proposal)
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            planned_claims=[
                _make_claim(
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="existing",
                            proposed_section="founding",
                        ),
                    ],
                ),
            ],
        )

        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()]),
        )
        # neither approved nor rejected counter increments — idempotent skip
        assert result.approved == 0
        # proposal file still cleaned up
        assert not reading_pipeline.pending_proposal_path(
            source_id, "aldara-f001"
        ).exists()
        # only the original fact remains
        e = entity_yaml.read(cfg.wiki_repo_path / "locations" / "aldara.yaml")
        assert len(e.facts) == 1


# ---------------------------------------------------------------------------
# New-entity stub creation + alias confirmation
# ---------------------------------------------------------------------------


class TestNewEntityStub:
    def test_creates_stub_with_timestamps(self, cfg: cfg_mod.Config) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        proposal = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        _write_proposal(source_id, proposal)
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(name="Aldara", category="locations"),
            ],
            planned_claims=[
                _make_claim(
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="founding",
                            proposed_category="locations",
                        ),
                    ],
                ),
            ],
        )

        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()]),
        )
        assert result.approved == 1
        path = cfg.wiki_repo_path / "locations" / "aldara.yaml"
        assert path.exists()
        e = entity_yaml.read(path)
        assert e.entity == "Aldara"
        assert e.created_at is not None
        assert e.created_by_ingest == "yt-x"
        assert e.updated_at == e.created_at  # same timestamp on creation
        assert e.aliases == []
        assert len(e.facts) == 1

    def test_first_approval_alias_source_is_stub_creation(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        proposal = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        _write_proposal(source_id, proposal)
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(
                    name="Aldara",
                    category="locations",
                    aliases_suggested=["the Realm"],
                ),
            ],
            planned_claims=[
                _make_claim(
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="founding",
                            proposed_category="locations",
                        ),
                    ],
                ),
            ],
        )

        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()], alias_responses=[True]),
        )
        assert result.approved == 1
        e = entity_yaml.read(cfg.wiki_repo_path / "locations" / "aldara.yaml")
        assert len(e.aliases) == 1
        assert e.aliases[0].name == "the Realm"
        assert e.aliases[0].source == "stub-creation"
        assert e.aliases[0].added_by_ingest == "yt-x"

    def test_existing_entity_alias_source_is_alias_confirmation(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        _seed_existing_entity(cfg.wiki_repo_path, name="Aldara", slug="aldara")
        proposal = _make_proposal(
            target="Aldara", proposed_id="aldara-f001", proposal_type="new_fact"
        )
        _write_proposal(source_id, proposal)
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            entity_resolutions=[
                plan_yaml.EntityResolution(
                    mention="the Aldaran Realm",
                    resolution="existing",
                    matched_entity="Aldara",
                    suggested_aliases_to_add=["the Aldaran Realm"],
                ),
            ],
            planned_claims=[
                _make_claim(
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="existing",
                            proposed_section="founding",
                        ),
                    ],
                ),
            ],
        )
        review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()], alias_responses=[True]),
        )
        e = entity_yaml.read(cfg.wiki_repo_path / "locations" / "aldara.yaml")
        assert len(e.aliases) == 1
        assert e.aliases[0].source == "alias-confirmation"


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


class TestReject:
    def test_deletes_proposal_without_touching_entity(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        _seed_existing_entity(cfg.wiki_repo_path, name="Aldara", slug="aldara")
        proposal = _make_proposal(
            target="Aldara", proposed_id="aldara-f001", proposal_type="new_fact"
        )
        _write_proposal(source_id, proposal)
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            planned_claims=[
                _make_claim(
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="existing",
                            proposed_section="founding",
                        ),
                    ],
                ),
            ],
        )
        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([RejectDecision()]),
        )
        assert result.rejected == 1
        assert result.approved == 0
        e = entity_yaml.read(cfg.wiki_repo_path / "locations" / "aldara.yaml")
        assert e.facts == []
        assert not reading_pipeline.pending_proposal_path(
            source_id, "aldara-f001"
        ).exists()


# ---------------------------------------------------------------------------
# Multi-target dedup at review time
# ---------------------------------------------------------------------------


class TestMultiTargetDedup:
    def test_two_proposals_share_one_stub(self, cfg: cfg_mod.Config) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        # Two new-entity proposals in the same claim group both targeting Aldara.
        # (Multi-target onto a brand-new entity is rare but legal.)
        # In practice they'd be different entities; we contrive same-target to
        # exercise the dedup path.
        siblings = [
            proposal_yaml.Sibling(entity="Aldara", proposed_id="aldara-f002"),
        ]
        siblings_back = [
            proposal_yaml.Sibling(entity="Aldara", proposed_id="aldara-f001"),
        ]
        _write_proposal(
            source_id,
            _make_proposal(
                target="Aldara",
                proposed_id="aldara-f001",
                cg="cg-001",
                siblings=siblings,
            ),
        )
        _write_proposal(
            source_id,
            _make_proposal(
                target="Aldara",
                proposed_id="aldara-f002",
                cg="cg-002",
                siblings=siblings_back,
            ),
        )
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(name="Aldara", category="locations"),
            ],
            planned_claims=[
                _make_claim(
                    cg="cg-001",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="founding",
                            proposed_category="locations",
                        ),
                    ],
                ),
                _make_claim(
                    cg="cg-002",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="lore",
                            proposed_category="locations",
                        ),
                    ],
                ),
            ],
        )
        review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision(), ApproveDecision()]),
        )
        e = entity_yaml.read(cfg.wiki_repo_path / "locations" / "aldara.yaml")
        assert len(e.facts) == 2
        assert {f["id"] for f in e.facts} == {"aldara-f001", "aldara-f002"}
        # created_by_ingest stays as the first-approval ingest
        assert e.created_by_ingest == "yt-x"
        # updated_at advanced past created_at after the second append
        assert e.updated_at is not None


# ---------------------------------------------------------------------------
# `created_earlier_in_session` annotation
# ---------------------------------------------------------------------------


class TestCreatedEarlierInSession:
    def test_second_proposal_view_marks_session_creation(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        _write_proposal(
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f001", cg="cg-001"),
        )
        _write_proposal(
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f002", cg="cg-002"),
        )
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(name="Aldara", category="locations"),
            ],
            planned_claims=[
                _make_claim(
                    cg="cg-001",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="founding",
                            proposed_category="locations",
                        ),
                    ],
                ),
                _make_claim(
                    cg="cg-002",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="lore",
                            proposed_category="locations",
                        ),
                    ],
                ),
            ],
        )
        scripted = ScriptedReviewer([ApproveDecision(), ApproveDecision()])
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        # Each cg has one target → one TargetView per bundle.
        assert scripted.decided[0].targets[0].created_earlier_in_session is False
        # Second bundle: stub was created in this same run
        assert scripted.decided[1].targets[0].created_earlier_in_session is True


# ---------------------------------------------------------------------------
# Sibling alias dedup
# ---------------------------------------------------------------------------


class TestSiblingAliasDedup:
    def test_second_sibling_skips_already_merged_alias(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        _write_proposal(
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f001", cg="cg-001"),
        )
        _write_proposal(
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f002", cg="cg-002"),
        )
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(
                    name="Aldara",
                    category="locations",
                    aliases_suggested=["the Realm"],
                ),
            ],
            planned_claims=[
                _make_claim(
                    cg="cg-001",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="founding",
                            proposed_category="locations",
                        ),
                    ],
                ),
                _make_claim(
                    cg="cg-002",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="lore",
                            proposed_category="locations",
                        ),
                    ],
                ),
            ],
        )
        scripted = ScriptedReviewer(
            [ApproveDecision(), ApproveDecision()], alias_responses=[True]
        )
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        # Only one alias prompt fired — the second sibling saw the alias as
        # already merged.
        assert len(scripted.alias_calls) == 1
        # And only one alias landed.
        e = entity_yaml.read(cfg.wiki_repo_path / "locations" / "aldara.yaml")
        assert len(e.aliases) == 1


# ---------------------------------------------------------------------------
# In-memory index refresh (end-to-end)
# ---------------------------------------------------------------------------


class TestIndexRefresh:
    def test_sibling_b_resolves_via_index_after_a_creates_stub(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        # A targets new entity Aldara; B is a `new_fact` proposal claiming
        # Aldara as existing. B can only succeed if the index refresh after A's
        # approval surfaces Aldara as existing.
        _write_proposal(
            source_id,
            _make_proposal(
                target="Aldara",
                proposed_id="aldara-f001",
                cg="cg-001",
                proposal_type="new_entity_with_facts",
            ),
        )
        _write_proposal(
            source_id,
            _make_proposal(
                target="Aldara",
                proposed_id="aldara-f002",
                cg="cg-002",
                proposal_type="new_fact",
            ),
        )
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(name="Aldara", category="locations"),
            ],
            planned_claims=[
                _make_claim(
                    cg="cg-001",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="founding",
                            proposed_category="locations",
                        ),
                    ],
                ),
                _make_claim(
                    cg="cg-002",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="existing",
                            proposed_section="lore",
                        ),
                    ],
                ),
            ],
        )
        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision(), ApproveDecision()]),
        )
        assert result.approved == 2
        e = entity_yaml.read(cfg.wiki_repo_path / "locations" / "aldara.yaml")
        assert len(e.facts) == 2


# ---------------------------------------------------------------------------
# KeyboardInterrupt resume + empty dir
# ---------------------------------------------------------------------------


class _RaisingReviewer:
    by_label = "human-review"

    def __init__(self, raise_on: int) -> None:
        self.raise_on = raise_on
        self.calls = 0

    def decide_bundle(self, view: BundleView) -> BundleDecision:
        self.calls += 1
        if self.calls == self.raise_on:
            raise KeyboardInterrupt
        return BundleDecision(
            decision=ApproveDecision(),
            selected_indices=tuple(range(len(view.targets))),
        )

    def confirm_alias(self, entity: str, mention: str) -> bool:  # noqa: ARG002
        return False


class TestResumeAndEmpty:
    def test_keyboard_interrupt_leaves_remaining_files(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        for i in range(1, 4):
            _write_proposal(
                source_id,
                _make_proposal(
                    target=f"E{i}",
                    proposed_id=f"e{i}-f001",
                    cg=f"cg-{i:03d}",
                ),
            )
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(name=f"E{i}", category="concepts")
                for i in range(1, 4)
            ],
            planned_claims=[
                _make_claim(
                    cg=f"cg-{i:03d}",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity=f"E{i}",
                            entity_state="new",
                            proposed_section="overview",
                            proposed_category="concepts",
                        ),
                    ],
                )
                for i in range(1, 4)
            ],
        )
        with pytest.raises(KeyboardInterrupt):
            review.run(
                cfg=cfg,
                source_id=source_id,
                reviewer=_RaisingReviewer(raise_on=2),
            )
        # First proposal approved → file gone
        assert not reading_pipeline.pending_proposal_path(source_id, "e1-f001").exists()
        # Second + third still pending
        assert reading_pipeline.pending_proposal_path(source_id, "e2-f001").exists()
        assert reading_pipeline.pending_proposal_path(source_id, "e3-f001").exists()

    def test_empty_proposals_dir_returns_zeroed_result(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        _write_plan(cfg.wiki_repo_path, source_id)
        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([]),
        )
        assert result == ReviewResult()


# ---------------------------------------------------------------------------
# Edit path end-to-end
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Bundle behavior (multi-target review screen)
# ---------------------------------------------------------------------------


def _multi_target_setup(
    cfg: cfg_mod.Config,
    source_id: str,
    *,
    sections: tuple[str, str, str] = ("founding", "lineage", "events-in-era"),
    aliases_per_entity: dict[str, list[str]] | None = None,
) -> None:
    """Plan with one cg routing to (Aldara, Theron, Second Age), all NEW."""
    aliases_per_entity = aliases_per_entity or {}
    _write_info(cfg.wiki_repo_path, source_id)
    cg = "cg-multi"
    targets = [
        ("Aldara", "locations", sections[0], "aldara-f001"),
        ("Theron", "characters", sections[1], "theron-f001"),
        ("Second Age", "events", sections[2], "second-age-f001"),
    ]
    siblings_for = {
        name: [
            proposal_yaml.Sibling(entity=other, proposed_id=oid)
            for other, _, _, oid in targets
            if other != name
        ]
        for name, _, _, _ in targets
    }
    for name, _cat, section, pid in targets:
        _write_proposal(
            source_id,
            _make_proposal(
                target=name,
                proposed_id=pid,
                cg=cg,
                section=section,
                siblings=siblings_for[name],
            ),
        )
    _write_plan(
        cfg.wiki_repo_path,
        source_id,
        new_entities=[
            plan_yaml.NewEntityProposal(
                name=name,
                category=cat,
                aliases_suggested=aliases_per_entity.get(name, []),
            )
            for name, cat, _, _ in targets
        ],
        planned_claims=[
            _make_claim(
                cg=cg,
                targets=[
                    plan_yaml.ClaimTarget(
                        entity=name,
                        entity_state="new",
                        proposed_section=section,
                        proposed_category=cat,
                    )
                    for name, cat, section, _ in targets
                ],
            )
        ],
    )


class TestBundle:
    def test_bundle_groups_siblings_into_one_decision(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _multi_target_setup(cfg, source_id)
        scripted = ScriptedReviewer([ApproveDecision()])
        result = review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        # ONE decide_bundle call, THREE facts approved.
        assert len(scripted.decided) == 1
        assert result.approved == 3
        assert result.rejected == 0
        for slug, cat in (
            ("aldara", "locations"),
            ("theron", "characters"),
            ("second-age", "events"),
        ):
            assert (cfg.wiki_repo_path / cat / f"{slug}.yaml").exists()

    def test_targets_drop_unchecks_a_route(self, cfg: cfg_mod.Config) -> None:
        source_id = "yt-x"
        _multi_target_setup(cfg, source_id)
        # Drop target index 1 (Theron); keep Aldara and Second Age.
        scripted = ScriptedReviewer(
            bundle_decisions=[
                BundleDecision(
                    decision=ApproveDecision(),
                    selected_indices=(0, 2),
                )
            ]
        )
        result = review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        assert result.approved == 2
        assert result.rejected == 1
        assert (cfg.wiki_repo_path / "locations" / "aldara.yaml").exists()
        assert (cfg.wiki_repo_path / "events" / "second-age.yaml").exists()
        # Theron stub never created.
        assert not (cfg.wiki_repo_path / "characters" / "theron.yaml").exists()
        # Theron proposal file gone.
        assert not reading_pipeline.pending_proposal_path(
            source_id, "theron-f001"
        ).exists()

    def test_edit_text_propagates_across_selected_siblings(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _multi_target_setup(cfg, source_id)
        scripted = ScriptedReviewer(
            bundle_decisions=[
                BundleDecision(
                    decision=EditDecision(new_text="Edited claim text."),
                    selected_indices=(0, 1, 2),
                )
            ]
        )
        result = review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        assert result.edited == 3
        for slug, cat in (
            ("aldara", "locations"),
            ("theron", "characters"),
            ("second-age", "events"),
        ):
            e = entity_yaml.read(cfg.wiki_repo_path / cat / f"{slug}.yaml")
            assert e.facts[0]["text"] == "Edited claim text."
            assert e.facts[0]["text_source"] is not None
            assert e.facts[0]["edited_by_human"] is True

    def test_per_target_override_only_applies_to_that_target(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _multi_target_setup(cfg, source_id)
        scripted = ScriptedReviewer(
            bundle_decisions=[
                BundleDecision(
                    decision=ApproveDecision(),
                    selected_indices=(0, 1, 2),
                    per_target_overrides={
                        1: EditDecision(new_section="bloodlines"),
                    },
                )
            ]
        )
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        aldara = entity_yaml.read(cfg.wiki_repo_path / "locations" / "aldara.yaml")
        theron = entity_yaml.read(cfg.wiki_repo_path / "characters" / "theron.yaml")
        second = entity_yaml.read(cfg.wiki_repo_path / "events" / "second-age.yaml")
        assert aldara.facts[0]["section"] == "founding"
        assert theron.facts[0]["section"] == "bloodlines"
        assert second.facts[0]["section"] == "events-in-era"

    def test_singleton_claim_group_renders_as_single_target(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        _write_proposal(
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f001", cg="cg-001"),
        )
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(name="Aldara", category="locations"),
            ],
            planned_claims=[
                _make_claim(
                    cg="cg-001",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="founding",
                            proposed_category="locations",
                        ),
                    ],
                ),
            ],
        )
        scripted = ScriptedReviewer([ApproveDecision()])
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        assert len(scripted.decided) == 1
        assert len(scripted.decided[0].targets) == 1

    def test_reject_discards_whole_bundle(self, cfg: cfg_mod.Config) -> None:
        source_id = "yt-x"
        _multi_target_setup(cfg, source_id)
        scripted = ScriptedReviewer([RejectDecision()])
        result = review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        assert result.rejected == 3
        assert result.approved == 0
        # No entity stubs created.
        for slug, cat in (
            ("aldara", "locations"),
            ("theron", "characters"),
            ("second-age", "events"),
        ):
            assert not (cfg.wiki_repo_path / cat / f"{slug}.yaml").exists()
        # All proposal files gone.
        for pid in ("aldara-f001", "theron-f001", "second-age-f001"):
            assert not reading_pipeline.pending_proposal_path(source_id, pid).exists()

    def test_alias_confirmation_only_fires_for_selected_targets(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        # Each entity has one alias suggestion. Drop Theron — its alias
        # prompt must NOT fire.
        _multi_target_setup(
            cfg,
            source_id,
            aliases_per_entity={
                "Aldara": ["the Realm"],
                "Theron": ["the King"],
                "Second Age": ["the Age"],
            },
        )
        scripted = ScriptedReviewer(
            bundle_decisions=[
                BundleDecision(
                    decision=ApproveDecision(),
                    selected_indices=(0, 2),
                )
            ],
            alias_responses=[True, True, True],
        )
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        prompted_entities = {entity for entity, _ in scripted.alias_calls}
        assert "Theron" not in prompted_entities
        assert prompted_entities == {"Aldara", "Second Age"}

    def test_approve_iterates_targets_in_plan_order(self, cfg: cfg_mod.Config) -> None:
        """Engine processes targets in plan order regardless of selection order.

        Observe via alias-prompt order: each entity has one alias
        suggestion; prompts must fire in plan order (Aldara, Theron,
        Second Age) even when the reviewer returns
        ``selected_indices`` reversed.
        """
        source_id = "yt-x"
        _multi_target_setup(
            cfg,
            source_id,
            aliases_per_entity={
                "Aldara": ["the Realm"],
                "Theron": ["the King"],
                "Second Age": ["the Age"],
            },
        )
        scripted = ScriptedReviewer(
            bundle_decisions=[
                BundleDecision(
                    decision=ApproveDecision(),
                    selected_indices=(2, 0, 1),
                )
            ],
            alias_responses=[True, True, True],
        )
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        prompt_order = [entity for entity, _ in scripted.alias_calls]
        assert prompt_order == ["Aldara", "Theron", "Second Age"]

    def test_resume_seeds_merged_aliases_from_disk(self, cfg: cfg_mod.Config) -> None:
        """Alias added by this ingest in a prior run() must not re-prompt."""
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        # Pre-existing entity with an alias added by THIS ingest.
        prior = entity_yaml.Entity(
            entity="Aldara",
            category="locations",
            slug="aldara",
            aliases=[
                entity_yaml.Alias(
                    name="the Realm",
                    added_by_ingest=source_id,
                    added_at="2026-04-20T00:00:00Z",
                    source="alias-confirmation",
                ),
            ],
            created_at="2026-04-20T00:00:00Z",
            created_by_ingest=source_id,
            updated_at="2026-04-20T00:00:00Z",
            facts=[],
        )
        entity_yaml.write(prior, cfg.wiki_repo_path / "locations" / "aldara.yaml")
        proposal = _make_proposal(
            target="Aldara",
            proposed_id="aldara-f002",
            cg="cg-002",
            proposal_type="new_fact",
        )
        _write_proposal(source_id, proposal)
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            entity_resolutions=[
                plan_yaml.EntityResolution(
                    mention="the Realm",
                    resolution="existing",
                    matched_entity="Aldara",
                    suggested_aliases_to_add=["the Realm"],
                ),
            ],
            planned_claims=[
                _make_claim(
                    cg="cg-002",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="existing",
                            proposed_section="founding",
                        ),
                    ],
                ),
            ],
        )
        scripted = ScriptedReviewer([ApproveDecision()])
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        # No alias prompt fired — already seeded from disk.
        assert scripted.alias_calls == []
        e = entity_yaml.read(cfg.wiki_repo_path / "locations" / "aldara.yaml")
        # Still exactly one alias (no double-add).
        assert len(e.aliases) == 1

    def test_interrupt_mid_bundle_leaves_unwritten_siblings_on_disk(
        self, cfg: cfg_mod.Config
    ) -> None:
        """KI mid-bundle leaves unwritten siblings' proposal files on disk."""
        source_id = "yt-x"
        _multi_target_setup(cfg, source_id)

        class _AliasInterrupter:
            by_label = "human-review"

            def __init__(self) -> None:
                self.alias_calls = 0

            def decide_bundle(self, view: BundleView) -> BundleDecision:
                return BundleDecision(
                    decision=ApproveDecision(),
                    selected_indices=tuple(range(len(view.targets))),
                )

            def confirm_alias(self, entity: str, mention: str) -> bool:  # noqa: ARG002
                self.alias_calls += 1
                # Raise after Aldara approved (no aliases for it in
                # this setup → no prompt). First alias prompt is for
                # Theron — raise then to leave Theron + Second Age
                # files on disk.
                raise KeyboardInterrupt

        # Re-seed plan with one alias on Theron (proposals already on
        # disk are unaffected). Aldara has no suggestion → its
        # confirm_alias is never called → KI fires on Theron's prompt.
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(name="Aldara", category="locations"),
                plan_yaml.NewEntityProposal(
                    name="Theron",
                    category="characters",
                    aliases_suggested=["the King"],
                ),
                plan_yaml.NewEntityProposal(name="Second Age", category="events"),
            ],
            planned_claims=[
                _make_claim(
                    cg="cg-multi",
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="founding",
                            proposed_category="locations",
                        ),
                        plan_yaml.ClaimTarget(
                            entity="Theron",
                            entity_state="new",
                            proposed_section="lineage",
                            proposed_category="characters",
                        ),
                        plan_yaml.ClaimTarget(
                            entity="Second Age",
                            entity_state="new",
                            proposed_section="events-in-era",
                            proposed_category="events",
                        ),
                    ],
                ),
            ],
        )
        with pytest.raises(KeyboardInterrupt):
            review.run(cfg=cfg, source_id=source_id, reviewer=_AliasInterrupter())
        # Aldara approved before the raise.
        assert not reading_pipeline.pending_proposal_path(
            source_id, "aldara-f001"
        ).exists()
        # Theron + Second Age still pending.
        assert reading_pipeline.pending_proposal_path(source_id, "theron-f001").exists()
        assert reading_pipeline.pending_proposal_path(
            source_id, "second-age-f001"
        ).exists()


class TestEditPath:
    def test_edit_writes_text_source_and_increments_edited_counter(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.wiki_repo_path, source_id)
        _seed_existing_entity(cfg.wiki_repo_path, name="Aldara", slug="aldara")
        proposal = _make_proposal(
            target="Aldara",
            proposed_id="aldara-f001",
            proposal_type="new_fact",
            text="Original LLM text.",
        )
        _write_proposal(source_id, proposal)
        _write_plan(
            cfg.wiki_repo_path,
            source_id,
            planned_claims=[
                _make_claim(
                    targets=[
                        plan_yaml.ClaimTarget(
                            entity="Aldara",
                            entity_state="existing",
                            proposed_section="founding",
                        ),
                    ],
                ),
            ],
        )
        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([EditDecision(new_text="Edited by hand.")]),
        )
        assert result.edited == 1
        assert result.approved == 0
        e = entity_yaml.read(cfg.wiki_repo_path / "locations" / "aldara.yaml")
        assert e.facts[0]["text"] == "Edited by hand."
        assert e.facts[0]["text_source"] == "Original LLM text."
        assert e.facts[0]["edited_by_human"] is True
