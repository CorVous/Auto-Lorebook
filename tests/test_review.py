"""Tests for review.py — Stage 4 (review) engine."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml

from auto_lorebook import config as cfg_mod
from auto_lorebook import db as db_mod
from auto_lorebook import (
    entities as entities_mod,
)
from auto_lorebook import (
    entity_yaml,
    plan_yaml,
    proposal_yaml,
    review,
)
from auto_lorebook import (
    facts as facts_mod,
)
from auto_lorebook import wiki_state as wiki_state_mod
from auto_lorebook.proposal_yaml import ProposalTarget
from auto_lorebook.review import (
    ApproveDecision,
    BundleDecision,
    BundleEdits,
    BundleView,
    Decision,
    MergedEdits,
    RejectDecision,
    ReviewResult,
    TargetEdits,
)
from auto_lorebook.wiki_registry import WikiEntry

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Generator
    from pathlib import Path


# ---------------------------------------------------------------------------
# Module-level stub: no real LLM calls from review.run
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_review_openrouter_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None]:
    """Prevent test_review.py from hitting the real LLM via review.run()."""
    # review.run() requires an API key before it builds the page-step client;
    # supply a dummy so the stub below is reached instead of a ReviewError.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-stub-key")
    stub = MagicMock()
    stub.complete.return_value = MagicMock(text='{"prose": "Stub prose."}')
    with patch("auto_lorebook.review.OpenRouterClient", return_value=stub):
        yield


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
    (home / "config.yaml").write_text(
        "schema_version: 2\nactive_wiki: test\nwikis:\n"
        f"- nickname: test\n  path: {tmp_wiki}\n",
        encoding="utf-8",
    )
    return cfg_mod.Config(wikis=[WikiEntry("test", tmp_wiki)], active_wiki="test")


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
    wiki: Path,
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
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sources(source_id, source_type, fetched_at,"
            " context_json) VALUES (?,?,?,?)",
            (source_id, "youtube", "2026-01-01T00:00:00Z", "{}"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ingests(ingest_id, source_id, started_at, state)"
            " VALUES (?,?,?,?)",
            (source_id, source_id, "2026-01-01T00:00:00Z", "planned"),
        )
        plan_yaml.write_plan_routes(conn, source_id, plan)
        conn.commit()
    finally:
        conn.close()


def _write_proposal(wiki: Path, source_id: str, p: proposal_yaml.Proposal) -> None:
    """Write proposal to DB using new schema (no plan_route_id)."""
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sources(source_id, source_type, fetched_at,"
            " context_json) VALUES (?,?,?,?)",
            (source_id, "youtube", "2026-01-01T00:00:00Z", "{}"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ingests(ingest_id, source_id, started_at, state)"
            " VALUES (?,?,?,?)",
            (source_id, source_id, "2026-01-01T00:00:00Z", "planned"),
        )
        proposal_yaml.write_proposal(conn, source_id, p)
        conn.commit()
    finally:
        conn.close()


def _make_proposal(
    *,
    target: str,
    proposed_id: str,
    cg: str = "cg-001",
    proposal_type: str = "new_entity_with_facts",
    text: str = "Aldara was founded in the Second Age.",
    locator: str = "0:00:08-0:00:18",
    section: str = "founding",
    proposed_category: str | None = None,
) -> proposal_yaml.Proposal:
    """Single-target proposal helper."""
    return proposal_yaml.Proposal(
        proposed_id=proposed_id,
        claim_group_id=cg,
        targets=[
            ProposalTarget(
                entity=target,
                section=section,
                speaker="DM",
                proposal_type=proposal_type,
                proposed_category=proposed_category,
            ),
        ],
        text=text,
        raw_transcript_span=text,
        text_corrects_transcript=False,
        corrections_applied=[],
        source_id="yt-x",
        locator=locator,
        reading_section="[0:00:00-0:00:30] Founding",
        reading_bullet_index=0,
        status="authoritative",
        status_reason=None,
        session_date="2026-04-15",
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


def _open_db(wiki: Path) -> sqlite3.Connection:
    """Open wiki DB (creating schema if absent)."""
    return db_mod.open(wiki_state_mod.wiki_db_path(wiki))


def _db_facts(wiki: Path, category: str, slug: str) -> list[facts_mod.FactRow]:
    """Return facts for entity from DB."""
    conn = _open_db(wiki)
    try:
        return facts_mod.list_facts_by_entity(conn, category, slug)
    finally:
        conn.close()


def _db_aliases(wiki: Path, category: str, slug: str) -> list[entities_mod.AliasRow]:
    """Return aliases for entity from DB."""
    conn = _open_db(wiki)
    try:
        return entities_mod.list_aliases(conn, category, slug)
    finally:
        conn.close()


def _db_entity(wiki: Path, category: str, slug: str) -> entities_mod.EntityRow | None:
    """Return entity row from DB."""
    conn = _open_db(wiki)
    try:
        return entities_mod.get_entity(conn, category, slug)
    finally:
        conn.close()


def _db_proposal_count(wiki: Path, source_id: str) -> int:
    """Count proposals still in DB for a given ingest."""
    conn = _open_db(wiki)
    try:
        return proposal_yaml.count_proposals(conn, source_id)
    finally:
        conn.close()


def _db_proposal_exists(wiki: Path, proposal_id: str) -> bool:
    """Return True if a proposal with the given id still exists in DB."""
    conn = _open_db(wiki)
    try:
        row = conn.execute(
            "SELECT 1 FROM proposals WHERE proposal_id=?", (proposal_id,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _seed_existing_entity(
    wiki: Path,
    *,
    name: str = "Aldara",
    category: str = "locations",
    slug: str = "aldara",
    facts: list[dict] | None = None,
) -> None:
    """Seed an existing entity into both YAML (for backfill) and DB directly."""
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
    # also insert directly into DB so lookup_by_planner_name works
    conn = _open_db(wiki)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO entities"
            "(category, slug, canonical_name,"
            " created_at, created_by_ingest, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                category,
                slug,
                name,
                "2026-01-01T00:00:00Z",
                "prior-ingest",
                "2026-01-01T00:00:00Z",
            ),
        )
        # seed any pre-existing facts into DB
        for f in facts or []:
            conn.execute(
                "INSERT OR IGNORE INTO sources(source_id, source_type, fetched_at,"
                " context_json) VALUES (?,?,?,?)",
                (
                    f.get("source_id", "prior-ingest"),
                    "youtube",
                    "2026-01-01T00:00:00Z",
                    "{}",
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO ingests(ingest_id, source_id, started_at, state)"
                " VALUES (?,?,?,?)",
                (
                    f.get("source_id", "prior-ingest"),
                    f.get("source_id", "prior-ingest"),
                    "2026-01-01T00:00:00Z",
                    "done",
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO facts"
                "(id, text, raw_transcript_span, text_corrects_transcript,"
                " source_id, locator, speaker, status, approved_at,"
                " created_by_ingest, claim_group_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f["id"],
                    f.get("text", ""),
                    f.get("text", ""),
                    0,
                    f.get("source_id", "prior-ingest"),
                    "0:00:00",
                    "DM",
                    f.get("status", "authoritative"),
                    "2026-01-01T00:00:00Z",
                    "prior-ingest",
                    f.get("claim_group_id", None),
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO fact_targets"
                "(fact_id, entity_category, entity_slug, section)"
                " VALUES (?,?,?,?)",
                (f["id"], category, slug, f.get("section", "overview")),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Walk order
# ---------------------------------------------------------------------------


class TestSortedProposals:
    def test_groups_siblings_in_target_order(self, cfg: cfg_mod.Config) -> None:
        """Plan order beats lex sort: cg-001 (War of the Dusk, Aldara) first."""
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        # cg-001 has two targets: War of the Dusk first, then Aldara.
        # cg-002 has one target: Theron.
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
            cfg.resolve_active_wiki(None),
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(name="War of the Dusk", category="events"),
                plan_yaml.NewEntityProposal(name="Aldara", category="locations"),
                plan_yaml.NewEntityProposal(name="Theron", category="characters"),
            ],
            planned_claims=[claim1, claim2],
        )
        # One proposal per claim group — cg-001 has 2 targets, cg-002 has 1.
        p1 = proposal_yaml.Proposal(
            proposed_id="war-of-the-dusk-f001",
            claim_group_id="cg-001",
            targets=[
                ProposalTarget(
                    entity="War of the Dusk",
                    section="overview",
                    speaker="DM",
                    proposal_type="new_entity_with_facts",
                    proposed_category="events",
                ),
                ProposalTarget(
                    entity="Aldara",
                    section="founding",
                    speaker="DM",
                    proposal_type="new_entity_with_facts",
                    proposed_category="locations",
                ),
            ],
            text="Aldara was founded in the Second Age.",
            raw_transcript_span="Aldara was founded in the Second Age.",
            text_corrects_transcript=False,
            corrections_applied=[],
            source_id=source_id,
            locator="0:00:08-0:00:18",
            reading_section="[0:00:00-0:00:30] Founding",
            reading_bullet_index=0,
            status="authoritative",
            session_date="2026-04-15",
            context_before="",
            context_after="",
        )
        p2 = _make_proposal(
            target="Theron",
            proposed_id="theron-f001",
            cg="cg-002",
            section="lineage",
            proposed_category="characters",
        )
        _write_proposal(cfg.resolve_active_wiki(None), source_id, p1)
        _write_proposal(cfg.resolve_active_wiki(None), source_id, p2)

        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        try:
            plan = plan_yaml.read_plan_routes(conn, source_id)
            assert plan is not None
            ordered = review.sorted_proposals(conn, plan)
        finally:
            conn.close()
        # Two proposals in plan order: cg-001 first, cg-002 second.
        assert len(ordered) == 2
        assert ordered[0].claim_group_id == "cg-001"
        assert ordered[1].claim_group_id == "cg-002"
        # cg-001 targets in plan order: War of the Dusk, then Aldara.
        assert [t.entity for t in ordered[0].targets] == ["War of the Dusk", "Aldara"]


# ---------------------------------------------------------------------------
# Validate proposals subset of plan
# ---------------------------------------------------------------------------


class TestValidateProposalsSubsetOfPlan:
    def test_exact_match_succeeds(self, cfg: cfg_mod.Config) -> None:
        """Proposals exactly matching plan keys → run() succeeds."""
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        proposal = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)
        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()]),
        )
        assert result.approved == 1

    def test_strict_subset_succeeds(self, cfg: cfg_mod.Config) -> None:
        """Plan has two claim groups; only one proposal on disk (Ctrl-C resume)."""
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        # Only write proposal for cg-001, not cg-002
        proposal = _make_proposal(
            target="Aldara", proposed_id="aldara-f001", cg="cg-001"
        )
        _write_plan(
            cfg.resolve_active_wiki(None),
            source_id,
            new_entities=[
                plan_yaml.NewEntityProposal(name="Aldara", category="locations"),
                plan_yaml.NewEntityProposal(name="Theron", category="characters"),
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
                            entity="Theron",
                            entity_state="new",
                            proposed_section="lineage",
                            proposed_category="characters",
                        ),
                    ],
                ),
            ],
        )
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)
        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()]),
        )
        assert result.approved == 1

    def test_orphan_raises_review_error(self, cfg: cfg_mod.Config) -> None:
        """Proposal whose target is not in plan → ReviewError."""
        source_id = "yt-x"
        wiki = cfg.resolve_active_wiki(None)
        _write_info(wiki, source_id)
        _write_plan(
            wiki,
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
        # In-plan proposal
        _write_proposal(
            wiki,
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f001", cg="cg-001"),
        )
        # Orphan: inject a proposal for cg-001 targeting Ghost (not in plan)
        conn = _open_db(wiki)
        try:
            conn.execute(
                "INSERT INTO proposals("
                "proposal_id, ingest_id, proposed_id, claim_group_id,"
                " text, raw_transcript_span, text_corrects_transcript,"
                " corrections_applied_json, source_id, locator, status,"
                " reading_section, reading_bullet_index)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "ghost-f001",
                    source_id,
                    "ghost-f001",
                    "cg-001",
                    "Ghost appeared.",
                    "Ghost appeared.",
                    0,
                    "[]",
                    source_id,
                    "0:00:08",
                    "authoritative",
                    "[0:00:00-0:00:30] Founding",
                    0,
                ),
            )
            conn.execute(
                "INSERT INTO proposal_targets(proposal_id, position, entity_name,"
                " section, speaker, proposal_type)"
                " VALUES (?,?,?,?,?,?)",
                ("ghost-f001", 0, "Ghost", "overview", "DM", "new_entity_with_facts"),
            )
            conn.commit()
        finally:
            conn.close()
        with pytest.raises(review.ReviewError) as exc_info:
            review.run(
                cfg=cfg,
                source_id=source_id,
                reviewer=ScriptedReviewer([ApproveDecision()]),
            )
        msg = str(exc_info.value)
        assert "ghost-f001" in msg
        assert "replan" in msg


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
            edits=MergedEdits(new_text="Aldara was founded earlier than that."),
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
            edits=MergedEdits(
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
        t0 = p.targets[0]
        fact = review.proposal_to_fact_dict(
            p,
            edits=MergedEdits(new_status="trustworthy"),
            ingest_id="yt-x",
            by_label="human-review",
        )
        assert fact["status"] == "trustworthy"
        assert fact["speaker"] == t0.speaker  # untouched
        assert fact["section"] == t0.section  # untouched
        assert fact["text"] == p.text  # untouched

    def test_noop_edit_keeps_proposal_values(self) -> None:
        p = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        t0 = p.targets[0]
        fact = review.proposal_to_fact_dict(
            p,
            edits=MergedEdits(),  # no overrides
            ingest_id="yt-x",
            by_label="human-review",
        )
        assert fact["text"] == p.text
        assert fact["speaker"] == t0.speaker
        assert fact["status"] == p.status
        assert fact["section"] == t0.section
        assert fact["edited_by_human"] is False


# ---------------------------------------------------------------------------
# Existing-entity approve
# ---------------------------------------------------------------------------


class TestExistingEntityApprove:
    def test_appends_fact_and_deletes_proposal(self, cfg: cfg_mod.Config) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        _seed_existing_entity(
            cfg.resolve_active_wiki(None), name="Aldara", slug="aldara"
        )

        proposal = _make_proposal(
            target="Aldara", proposed_id="aldara-f001", proposal_type="new_fact"
        )
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)

        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()]),
        )
        assert result.approved == 1
        assert result.rejected == 0
        # proposal removed from DB
        assert not _db_proposal_exists(cfg.resolve_active_wiki(None), "aldara-f001")
        # entity grew by one fact
        db_facts = _db_facts(cfg.resolve_active_wiki(None), "locations", "aldara")
        assert len(db_facts) == 1
        assert db_facts[0].id == "aldara-f001"


# ---------------------------------------------------------------------------
# Idempotent re-approval guard
# ---------------------------------------------------------------------------


class TestIdempotentGuard:
    def test_does_not_double_append(self, cfg: cfg_mod.Config) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        _seed_existing_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            slug="aldara",
            facts=[{"id": "aldara-f001", "text": "previously approved."}],
        )

        proposal = _make_proposal(
            target="Aldara", proposed_id="aldara-f001", proposal_type="new_fact"
        )
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)

        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()]),
        )
        # neither approved nor rejected counter increments — idempotent skip
        assert result.approved == 0
        # proposal removed from DB
        assert not _db_proposal_exists(cfg.resolve_active_wiki(None), "aldara-f001")
        # only the original fact remains (the pre-seeded one)
        db_facts = _db_facts(cfg.resolve_active_wiki(None), "locations", "aldara")
        assert len(db_facts) == 1


# ---------------------------------------------------------------------------
# New-entity stub creation + alias confirmation
# ---------------------------------------------------------------------------


class TestNewEntityStub:
    def test_creates_stub_with_timestamps(self, cfg: cfg_mod.Config) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        proposal = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)

        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()]),
        )
        assert result.approved == 1
        wiki = cfg.resolve_active_wiki(None)
        # .md written by summary_regen
        assert (wiki / "locations" / "aldara.md").exists()
        ent = _db_entity(wiki, "locations", "aldara")
        assert ent is not None
        assert ent.canonical_name == "Aldara"
        assert ent.created_at is not None
        assert ent.created_by_ingest == "yt-x"
        assert ent.updated_at == ent.created_at  # same timestamp on creation
        assert _db_aliases(wiki, "locations", "aldara") == []
        assert len(_db_facts(wiki, "locations", "aldara")) == 1

    def test_first_approval_alias_source_is_stub_creation(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        proposal = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)

        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()], alias_responses=[True]),
        )
        assert result.approved == 1
        aliases = _db_aliases(cfg.resolve_active_wiki(None), "locations", "aldara")
        assert len(aliases) == 1
        assert aliases[0].name == "the Realm"
        assert aliases[0].source == "stub-creation"
        assert aliases[0].added_by_ingest == "yt-x"

    def test_existing_entity_alias_source_is_alias_confirmation(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        _seed_existing_entity(
            cfg.resolve_active_wiki(None), name="Aldara", slug="aldara"
        )
        proposal = _make_proposal(
            target="Aldara", proposed_id="aldara-f001", proposal_type="new_fact"
        )
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)
        review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision()], alias_responses=[True]),
        )
        aliases = _db_aliases(cfg.resolve_active_wiki(None), "locations", "aldara")
        assert len(aliases) == 1
        assert aliases[0].source == "alias-confirmation"

    def test_orphan_alias_prevented_when_fact_insert_fails(
        self, cfg: cfg_mod.Config, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Alias must not persist if fact insert fails mid-tx."""
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        proposal = _make_proposal(target="Aldara", proposed_id="aldara-f001")
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("injected")

        monkeypatch.setattr(facts_mod, "create_fact_with_targets", _raise)

        with pytest.raises(RuntimeError, match="injected"):
            review.run(
                cfg=cfg,
                source_id=source_id,
                reviewer=ScriptedReviewer([ApproveDecision()], alias_responses=[True]),
            )

        aliases = _db_aliases(cfg.resolve_active_wiki(None), "locations", "aldara")
        assert len(aliases) == 0


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


class TestReject:
    def test_deletes_proposal_without_touching_entity(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        _seed_existing_entity(
            cfg.resolve_active_wiki(None), name="Aldara", slug="aldara"
        )
        proposal = _make_proposal(
            target="Aldara", proposed_id="aldara-f001", proposal_type="new_fact"
        )
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)
        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([RejectDecision()]),
        )
        assert result.rejected == 1
        assert result.approved == 0
        # no facts added to DB
        assert _db_facts(cfg.resolve_active_wiki(None), "locations", "aldara") == []
        # proposal removed from DB
        assert not _db_proposal_exists(cfg.resolve_active_wiki(None), "aldara-f001")


# ---------------------------------------------------------------------------
# Multi-target dedup at review time
# ---------------------------------------------------------------------------


class TestMultiTargetDedup:
    def test_two_proposals_share_one_stub(self, cfg: cfg_mod.Config) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        # Two separate claim groups, each targeting Aldara (different sections).
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(
                target="Aldara",
                proposed_id="aldara-f001",
                cg="cg-001",
            ),
        )
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(
                target="Aldara",
                proposed_id="aldara-f002",
                cg="cg-002",
                section="lore",
            ),
        )
        review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision(), ApproveDecision()]),
        )
        wiki = cfg.resolve_active_wiki(None)
        db_facts = _db_facts(wiki, "locations", "aldara")
        assert len(db_facts) == 2
        assert {f.id for f in db_facts} == {"aldara-f001", "aldara-f002"}
        # created_by_ingest stays as the first-approval ingest
        ent = _db_entity(wiki, "locations", "aldara")
        assert ent is not None
        assert ent.created_by_ingest == "yt-x"


# ---------------------------------------------------------------------------
# `created_earlier_in_session` annotation
# ---------------------------------------------------------------------------


class TestCreatedEarlierInSession:
    def test_second_proposal_view_marks_session_creation(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f001", cg="cg-001"),
        )
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f002", cg="cg-002"),
        )
        scripted = ScriptedReviewer([ApproveDecision(), ApproveDecision()])
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        # Each cg has one target → one TargetView per bundle.
        assert scripted.decided[0].targets[0].created_earlier_in_session is False
        # Second bundle: stub was created in this same run
        assert scripted.decided[1].targets[0].created_earlier_in_session is True


# ---------------------------------------------------------------------------
# Sibling alias dedup (across claim groups)
# ---------------------------------------------------------------------------


class TestSiblingAliasDedup:
    def test_second_sibling_skips_already_merged_alias(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f001", cg="cg-001"),
        )
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f002", cg="cg-002"),
        )
        scripted = ScriptedReviewer(
            [ApproveDecision(), ApproveDecision()], alias_responses=[True]
        )
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        # Only one alias prompt fired — the second bundle saw the alias as
        # already merged.
        assert len(scripted.alias_calls) == 1
        # And only one alias landed.
        assert (
            len(_db_aliases(cfg.resolve_active_wiki(None), "locations", "aldara")) == 1
        )


# ---------------------------------------------------------------------------
# Declined-alias dedup (in-memory, single run)
# ---------------------------------------------------------------------------


class TestDeclinedAliasMemory:
    def test_decline_in_first_route_skips_alias_for_sibling_routes(
        self, cfg: cfg_mod.Config
    ) -> None:
        # Single bundle: three proposals in the same cg all targeting Aldara.
        # In new model, one proposal with 3 targets for Aldara.
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        cg = "cg-001"
        _write_plan(
            cfg.resolve_active_wiki(None),
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
                    cg=cg,
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
        # One proposal for cg-001, targeting Aldara once.
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f001", cg=cg),
        )
        # One bundle decision, alias declined.
        scripted = ScriptedReviewer([ApproveDecision()], alias_responses=[False])
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        # Only one alias prompt fired.
        assert len(scripted.alias_calls) == 1
        assert (
            len(_db_aliases(cfg.resolve_active_wiki(None), "locations", "aldara")) == 0
        )

    def _setup_two_bundles_same_alias(
        self, cfg: cfg_mod.Config, source_id: str
    ) -> None:
        """Two consecutive bundles both suggesting 'the Realm' for Aldara."""
        _write_info(cfg.resolve_active_wiki(None), source_id)
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f001", cg="cg-001"),
        )
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f002", cg="cg-002"),
        )

    def test_decline_in_first_bundle_skips_alias_in_second_bundle(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        self._setup_two_bundles_same_alias(cfg, source_id)
        scripted = ScriptedReviewer(
            [ApproveDecision(), ApproveDecision()], alias_responses=[False]
        )
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        # Declined in first bundle → skipped in second bundle.
        assert len(scripted.alias_calls) == 1
        assert (
            len(_db_aliases(cfg.resolve_active_wiki(None), "locations", "aldara")) == 0
        )

    def test_second_run_does_not_inherit_declines_from_first(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        self._setup_two_bundles_same_alias(cfg, source_id)
        # First run: decline alias once, entity gets created (both proposals approved).
        scripted1 = ScriptedReviewer(
            [ApproveDecision(), ApproveDecision()], alias_responses=[False]
        )
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted1)
        assert len(scripted1.alias_calls) == 1

        # Second run: new plan targeting existing Aldara with same alias.
        source_id2 = "yt-y"
        _write_info(cfg.resolve_active_wiki(None), source_id2)
        _write_plan(
            cfg.resolve_active_wiki(None),
            source_id2,
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
                    cg="cg-001",
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
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id2,
            _make_proposal(
                target="Aldara",
                proposed_id="aldara-g001",
                cg="cg-001",
                proposal_type="new_fact",
            ),
        )
        scripted2 = ScriptedReviewer([ApproveDecision()], alias_responses=[True])
        review.run(cfg=cfg, source_id=source_id2, reviewer=scripted2)
        # Prompt fires because declines don't persist across run() calls.
        assert scripted2.alias_calls == [("Aldara", "the Realm")]


# ---------------------------------------------------------------------------
# In-memory index refresh (end-to-end)
# ---------------------------------------------------------------------------


class TestIndexRefresh:
    def test_sibling_b_resolves_via_index_after_a_creates_stub(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        # cg-001: new Aldara; cg-002: existing Aldara (depends on cg-001 creating stub).
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(
                target="Aldara",
                proposed_id="aldara-f001",
                cg="cg-001",
                proposal_type="new_entity_with_facts",
            ),
        )
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(
                target="Aldara",
                proposed_id="aldara-f002",
                cg="cg-002",
                proposal_type="new_fact",
            ),
        )
        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([ApproveDecision(), ApproveDecision()]),
        )
        assert result.approved == 2
        assert len(_db_facts(cfg.resolve_active_wiki(None), "locations", "aldara")) == 2


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
        _write_info(cfg.resolve_active_wiki(None), source_id)
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        for i in range(1, 4):
            _write_proposal(
                cfg.resolve_active_wiki(None),
                source_id,
                _make_proposal(
                    target=f"E{i}",
                    proposed_id=f"e{i}-f001",
                    cg=f"cg-{i:03d}",
                ),
            )
        with pytest.raises(KeyboardInterrupt):
            review.run(
                cfg=cfg,
                source_id=source_id,
                reviewer=_RaisingReviewer(raise_on=2),
            )
        wiki = cfg.resolve_active_wiki(None)
        # First proposal approved → removed from DB
        assert not _db_proposal_exists(wiki, "e1-f001")
        # Second + third still pending in DB
        assert _db_proposal_exists(wiki, "e2-f001")
        assert _db_proposal_exists(wiki, "e3-f001")

    def test_empty_proposals_dir_returns_zeroed_result(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        _write_plan(cfg.resolve_active_wiki(None), source_id)
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
    """Plan with one cg routing to (Aldara, Theron, Second Age), all NEW.

    Writes ONE proposal with 3 targets.
    """
    aliases_per_entity = aliases_per_entity or {}
    _write_info(cfg.resolve_active_wiki(None), source_id)
    cg = "cg-multi"
    targets = [
        ("Aldara", "locations", sections[0]),
        ("Theron", "characters", sections[1]),
        ("Second Age", "events", sections[2]),
    ]
    _write_plan(
        cfg.resolve_active_wiki(None),
        source_id,
        new_entities=[
            plan_yaml.NewEntityProposal(
                name=name,
                category=cat,
                aliases_suggested=aliases_per_entity.get(name, []),
            )
            for name, cat, _ in targets
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
                    for name, cat, section in targets
                ],
            )
        ],
    )
    # ONE proposal with 3 targets
    proposal = proposal_yaml.Proposal(
        proposed_id="aldara-f001",
        claim_group_id=cg,
        targets=[
            ProposalTarget(
                entity=name,
                section=section,
                speaker="DM",
                proposal_type="new_entity_with_facts",
                proposed_category=cat,
            )
            for name, cat, section in targets
        ],
        text="Aldara was founded in the Second Age.",
        raw_transcript_span="Aldara was founded in the Second Age.",
        text_corrects_transcript=False,
        corrections_applied=[],
        source_id=source_id,
        locator="0:00:08-0:00:18",
        reading_section="[0:00:00-0:00:30] Founding",
        reading_bullet_index=0,
        status="authoritative",
        session_date="2026-04-15",
        context_before="",
        context_after="",
    )
    _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)


class TestBundle:
    def test_bundle_groups_siblings_into_one_decision(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _multi_target_setup(cfg, source_id)
        scripted = ScriptedReviewer([ApproveDecision()])
        result = review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        # ONE decide_bundle call, ONE proposal approved (3 fact_targets).
        assert len(scripted.decided) == 1
        assert result.approved == 1
        assert result.rejected == 0
        wiki = cfg.resolve_active_wiki(None)
        for slug, cat in (
            ("aldara", "locations"),
            ("theron", "characters"),
            ("second-age", "events"),
        ):
            assert (wiki / cat / f"{slug}.md").exists()
        # 3 fact_targets for the single fact
        conn = _open_db(wiki)
        try:
            ft_count = conn.execute(
                "SELECT COUNT(*) FROM fact_targets WHERE fact_id='aldara-f001'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert ft_count == 3

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
        assert result.approved == 1
        assert result.rejected == 0
        wiki = cfg.resolve_active_wiki(None)
        assert (wiki / "locations" / "aldara.md").exists()
        assert (wiki / "events" / "second-age.md").exists()
        # Theron stub never created.
        assert _db_entity(wiki, "characters", "theron") is None
        # Proposal removed from DB (aldara-f001 was approved).
        assert not _db_proposal_exists(wiki, "aldara-f001")
        # Only 2 fact_targets (Aldara and Second Age)
        conn = _open_db(wiki)
        try:
            ft_count = conn.execute(
                "SELECT COUNT(*) FROM fact_targets WHERE fact_id='aldara-f001'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert ft_count == 2

    def test_edit_text_propagates_across_selected_siblings(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _multi_target_setup(cfg, source_id)
        scripted = ScriptedReviewer(
            bundle_decisions=[
                BundleDecision(
                    decision=BundleEdits(new_text="Edited claim text."),
                    selected_indices=(0, 1, 2),
                )
            ]
        )
        result = review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        assert result.edited == 1
        wiki = cfg.resolve_active_wiki(None)
        # The single fact has the edited text
        conn = _open_db(wiki)
        try:
            row = conn.execute(
                "SELECT text, edited_by_human FROM facts WHERE id='aldara-f001'"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "Edited claim text."
        assert row[1] == 1
        # All 3 entity .md files written
        for slug, cat in (
            ("aldara", "locations"),
            ("theron", "characters"),
            ("second-age", "events"),
        ):
            assert (wiki / cat / f"{slug}.md").exists()

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
                        1: TargetEdits(new_section="bloodlines"),
                    },
                )
            ]
        )
        wiki = cfg.resolve_active_wiki(None)
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)

        def _section(category: str, slug: str) -> str:
            conn = _open_db(wiki)
            try:
                row = conn.execute(
                    "SELECT section FROM fact_targets"
                    " WHERE fact_id='aldara-f001'"
                    " AND entity_category=? AND entity_slug=?",
                    (category, slug),
                ).fetchone()
                return row[0] if row else ""
            finally:
                conn.close()

        assert _section("locations", "aldara") == "founding"
        assert _section("characters", "theron") == "bloodlines"
        assert _section("events", "second-age") == "events-in-era"

    def test_bundle_level_edit_propagates_text_status_status_reason_to_all_routes(
        self, cfg: cfg_mod.Config
    ) -> None:
        """Bundle-level text/status/status_reason land on the single fact."""
        source_id = "yt-x"
        _multi_target_setup(cfg, source_id)
        scripted = ScriptedReviewer(
            bundle_decisions=[
                BundleDecision(
                    decision=BundleEdits(
                        new_text="Edited claim.",
                        new_status="hearsay",
                        new_status_reason="tavern rumor",
                    ),
                    selected_indices=(0, 1, 2),
                )
            ]
        )
        result = review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        assert result.edited == 1
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        try:
            row = conn.execute(
                "SELECT text, status, status_reason FROM facts WHERE id='aldara-f001'"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "Edited claim."
        assert row[1] == "hearsay"
        assert row[2] == "tavern rumor"

    def test_per_target_section_speaker_scoped_to_that_target_only(
        self, cfg: cfg_mod.Config
    ) -> None:
        """TargetEdits on idx 1 must not leak to idx 0 or idx 2."""
        source_id = "yt-x"
        _multi_target_setup(cfg, source_id)
        scripted = ScriptedReviewer(
            bundle_decisions=[
                BundleDecision(
                    decision=ApproveDecision(),
                    selected_indices=(0, 1, 2),
                    per_target_overrides={
                        1: TargetEdits(
                            new_section="bloodlines",
                            new_speaker="Player-Thorin",
                        ),
                    },
                )
            ]
        )
        wiki = cfg.resolve_active_wiki(None)
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)

        conn = _open_db(wiki)
        try:
            # section check via fact_targets
            # (speaker is set at fact level by approval call)
            aldara_section = conn.execute(
                "SELECT section FROM fact_targets WHERE fact_id='aldara-f001'"
                " AND entity_category='locations'",
            ).fetchone()[0]
            theron_section = conn.execute(
                "SELECT section FROM fact_targets WHERE fact_id='aldara-f001'"
                " AND entity_category='characters'",
            ).fetchone()[0]
            second_section = conn.execute(
                "SELECT section FROM fact_targets WHERE fact_id='aldara-f001'"
                " AND entity_category='events'",
            ).fetchone()[0]
        finally:
            conn.close()
        assert aldara_section == "founding"
        assert theron_section == "bloodlines"
        assert second_section == "events-in-era"

    def test_bundle_edit_combined_with_per_target_override_layers_disjoint_fields(
        self, cfg: cfg_mod.Config
    ) -> None:
        """Bundle text/status + per-target section coexist on the same fact."""
        source_id = "yt-x"
        _multi_target_setup(cfg, source_id)
        scripted = ScriptedReviewer(
            bundle_decisions=[
                BundleDecision(
                    decision=BundleEdits(
                        new_text="Edited claim.",
                        new_status="hearsay",
                    ),
                    selected_indices=(0, 1, 2),
                    per_target_overrides={
                        1: TargetEdits(new_section="bloodlines"),
                    },
                )
            ]
        )
        wiki = cfg.resolve_active_wiki(None)
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)

        conn = _open_db(wiki)
        try:
            fact_row = conn.execute(
                "SELECT text, status FROM facts WHERE id='aldara-f001'"
            ).fetchone()
            theron_section = conn.execute(
                "SELECT section FROM fact_targets WHERE fact_id='aldara-f001'"
                " AND entity_category='characters'",
            ).fetchone()[0]
            aldara_section = conn.execute(
                "SELECT section FROM fact_targets WHERE fact_id='aldara-f001'"
                " AND entity_category='locations'",
            ).fetchone()[0]
            second_section = conn.execute(
                "SELECT section FROM fact_targets WHERE fact_id='aldara-f001'"
                " AND entity_category='events'",
            ).fetchone()[0]
        finally:
            conn.close()

        # bundle fields land on the fact
        assert fact_row[0] == "Edited claim."
        assert fact_row[1] == "hearsay"
        # only Theron has the per-target section override
        assert theron_section == "bloodlines"
        assert aldara_section == "founding"
        assert second_section == "events-in-era"

    def test_singleton_claim_group_renders_as_single_target(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(
            cfg.resolve_active_wiki(None),
            source_id,
            _make_proposal(target="Aldara", proposed_id="aldara-f001", cg="cg-001"),
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
        wiki = cfg.resolve_active_wiki(None)
        assert result.rejected == 1
        assert result.approved == 0
        # No entity stubs created in DB.
        for slug, cat in (
            ("aldara", "locations"),
            ("theron", "characters"),
            ("second-age", "events"),
        ):
            assert _db_entity(wiki, cat, slug) is None
        # Proposal removed from DB.
        assert not _db_proposal_exists(wiki, "aldara-f001")

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
        _write_info(cfg.resolve_active_wiki(None), source_id)
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
        entity_yaml.write(
            prior, cfg.resolve_active_wiki(None) / "locations" / "aldara.yaml"
        )
        proposal = _make_proposal(
            target="Aldara",
            proposed_id="aldara-f002",
            cg="cg-002",
            proposal_type="new_fact",
        )
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)
        scripted = ScriptedReviewer([ApproveDecision()])
        review.run(cfg=cfg, source_id=source_id, reviewer=scripted)
        # No alias prompt fired — already seeded from disk.
        assert scripted.alias_calls == []
        # Still exactly one alias (no double-add).
        aliases = _db_aliases(cfg.resolve_active_wiki(None), "locations", "aldara")
        assert len(aliases) == 1

    def test_interrupt_mid_bundle_leaves_unwritten_siblings_on_disk(
        self, cfg: cfg_mod.Config
    ) -> None:
        """KI mid-bundle leaves unwritten siblings' proposal files on disk.

        In new model: one proposal with 3 targets. KI fires while processing
        the second target (Theron's alias confirmation). Aldara has already
        been approved (entity created), but the fact hasn't been committed yet
        (KI happens before approve_proposal call).

        Actually in the new implementation, approve_proposal is called once
        for all selected targets together after all aliases are processed.
        So if KI fires during alias confirmation for Theron, the proposal
        is still in the DB (not yet committed).
        """
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        cg = "cg-multi"
        _write_plan(
            cfg.resolve_active_wiki(None),
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
                    cg=cg,
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
        # ONE proposal with 3 targets
        proposal = proposal_yaml.Proposal(
            proposed_id="aldara-f001",
            claim_group_id=cg,
            targets=[
                ProposalTarget(
                    entity="Aldara",
                    section="founding",
                    speaker="DM",
                    proposal_type="new_entity_with_facts",
                    proposed_category="locations",
                ),
                ProposalTarget(
                    entity="Theron",
                    section="lineage",
                    speaker="DM",
                    proposal_type="new_entity_with_facts",
                    proposed_category="characters",
                ),
                ProposalTarget(
                    entity="Second Age",
                    section="events-in-era",
                    speaker="DM",
                    proposal_type="new_entity_with_facts",
                    proposed_category="events",
                ),
            ],
            text="Aldara was founded in the Second Age.",
            raw_transcript_span="Aldara was founded in the Second Age.",
            text_corrects_transcript=False,
            corrections_applied=[],
            source_id=source_id,
            locator="0:00:08-0:00:18",
            reading_section="[0:00:00-0:00:30] Founding",
            reading_bullet_index=0,
            status="authoritative",
            session_date="2026-04-15",
            context_before="",
            context_after="",
        )
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)

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
                # First alias prompt is for Theron → raise KI
                raise KeyboardInterrupt

        wiki = cfg.resolve_active_wiki(None)
        with pytest.raises(KeyboardInterrupt):
            review.run(cfg=cfg, source_id=source_id, reviewer=_AliasInterrupter())
        # KI fires during alias processing → proposal still in DB
        assert _db_proposal_exists(wiki, "aldara-f001")


class TestEditPath:
    def test_edit_writes_text_source_and_increments_edited_counter(
        self, cfg: cfg_mod.Config
    ) -> None:
        source_id = "yt-x"
        _write_info(cfg.resolve_active_wiki(None), source_id)
        _seed_existing_entity(
            cfg.resolve_active_wiki(None), name="Aldara", slug="aldara"
        )
        proposal = _make_proposal(
            target="Aldara",
            proposed_id="aldara-f001",
            proposal_type="new_fact",
            text="Original LLM text.",
        )
        _write_plan(
            cfg.resolve_active_wiki(None),
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
        _write_proposal(cfg.resolve_active_wiki(None), source_id, proposal)
        result = review.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=ScriptedReviewer([BundleEdits(new_text="Edited by hand.")]),
        )
        assert result.edited == 1
        assert result.approved == 0
        db_facts = _db_facts(cfg.resolve_active_wiki(None), "locations", "aldara")
        assert db_facts[0].text == "Edited by hand."
        assert db_facts[0].text_source == "Original LLM text."
        assert db_facts[0].edited_by_human is True
