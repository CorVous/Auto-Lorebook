"""Tests for approval.py — multi-target proposal approval."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from auto_lorebook import db
from auto_lorebook import entities as entities_mod
from auto_lorebook.approval import ApprovalResult, approve_proposal
from auto_lorebook.entities import normalize_name
from auto_lorebook.proposal_yaml import Proposal, ProposalTarget

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Generator


@pytest.fixture
def conn() -> Generator[sqlite3.Connection]:
    """In-memory DB with prerequisite rows seeded.

    Yields:
        open in-memory connection.

    """
    c = db.open(":memory:")
    c.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at, context_json)"
        " VALUES ('src-001', 'youtube', '2026-01-01T00:00:00Z', '{}')"
    )
    c.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('ing-001', 'src-001', '2026-01-01T00:00:00Z', 'done')"
    )
    c.execute(
        "INSERT INTO entities(category, slug, canonical_name, created_at,"
        " created_by_ingest, updated_at)"
        " VALUES ('characters', 'theron', 'Theron',"
        " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
    )
    c.execute(
        "INSERT INTO entities(category, slug, canonical_name, created_at,"
        " created_by_ingest, updated_at)"
        " VALUES ('locations', 'aldara', 'Aldara',"
        " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
    )
    c.commit()
    yield c
    c.close()


def _make_proposal(**overrides: Any) -> Proposal:  # noqa: ANN401
    base: dict[str, Any] = {
        "proposed_id": "f-001",
        "claim_group_id": "cg-001",
        "targets": [
            ProposalTarget(
                entity="Theron",
                section="biography",
                speaker="DM",
                proposal_type="new_fact",
            ),
        ],
        "text": "Theron founded Aldara.",
        "raw_transcript_span": "Theron founded all-dara.",
        "text_corrects_transcript": True,
        "source_id": "src-001",
        "locator": "0:04:32",
        "reading_section": "[4:30-8:00]",
        "reading_bullet_index": 0,
        "status": "authoritative",
        "session_date": "2026-01-15",
        "context_before": "",
        "context_after": "",
    }
    base.update(overrides)
    return Proposal(**base)


class TestApproveProposal:
    def test_approved_returns_approved(self, conn: sqlite3.Connection) -> None:
        proposal = _make_proposal()
        result = approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[("characters", "theron", "biography")],
            by="test-user",
        )
        assert result == ApprovalResult.APPROVED

    def test_fact_row_inserted(self, conn: sqlite3.Connection) -> None:
        proposal = _make_proposal()
        approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[("characters", "theron", "biography")],
            by="test-user",
        )
        row = conn.execute("SELECT id, text FROM facts WHERE id='f-001'").fetchone()
        assert row is not None
        assert row[1] == "Theron founded Aldara."

    def test_idempotent_on_second_call(self, conn: sqlite3.Connection) -> None:
        proposal = _make_proposal()
        first = approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[("characters", "theron", "biography")],
            by="test-user",
        )
        second = approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[("characters", "theron", "biography")],
            by="test-user",
        )
        assert first == ApprovalResult.APPROVED
        assert second == ApprovalResult.SKIPPED_IDEMPOTENT

    def test_edited_text_reflected_in_fact(self, conn: sqlite3.Connection) -> None:
        proposal = _make_proposal()
        approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[("characters", "theron", "biography")],
            by="test-user",
            edited_text="King Theron founded Aldara.",
        )
        row = conn.execute(
            "SELECT text, edited_by_human FROM facts WHERE id='f-001'"
        ).fetchone()
        assert row[0] == "King Theron founded Aldara."
        assert row[1] == 1

    def test_proposal_row_deleted_on_approval(self, conn: sqlite3.Connection) -> None:
        # seed a proposal row in DB (new v5 schema — no plan_route_id/section/speaker)
        conn.execute(
            "INSERT INTO proposals(proposal_id, ingest_id, proposed_id, claim_group_id,"
            " text, raw_transcript_span, text_corrects_transcript,"
            " corrections_applied_json, source_id, locator, status,"
            " reading_section, reading_bullet_index)"
            " VALUES ('f-001','ing-001','f-001','cg-001',"
            " 'Theron founded Aldara.','Theron founded all-dara.',1,"
            " '[]','src-001','0:04:32','authoritative','[4:30-8:00]',0)"
        )
        conn.commit()
        proposal = _make_proposal()
        approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[("characters", "theron", "biography")],
            by="test-user",
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE proposal_id='f-001'"
        ).fetchone()[0]
        assert count == 0

    def test_rollback_on_exception(self, conn: sqlite3.Connection) -> None:
        proposal = _make_proposal()
        with (
            patch(
                "auto_lorebook.approval.facts_mod.create_fact_with_targets",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            approve_proposal(
                conn,
                proposal=proposal,
                targets_resolved=[("characters", "theron", "biography")],
                by="test-user",
            )
        # fact should not exist after rollback
        row = conn.execute("SELECT id FROM facts WHERE id='f-001'").fetchone()
        assert row is None


class TestTwoEntityApprove:
    def test_two_entity_atomic_approve(self, conn: sqlite3.Connection) -> None:
        proposal = _make_proposal()
        result = approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[
                ("characters", "theron", "biography"),
                ("locations", "aldara", "founding"),
            ],
            by="test-user",
        )
        assert result == ApprovalResult.APPROVED
        # 1 fact row
        fact_count = conn.execute(
            "SELECT COUNT(*) FROM facts WHERE id='f-001'"
        ).fetchone()[0]
        assert fact_count == 1
        # 2 fact_targets rows
        ft_count = conn.execute(
            "SELECT COUNT(*) FROM fact_targets WHERE fact_id='f-001'"
        ).fetchone()[0]
        assert ft_count == 2
        # 1 history row
        hist_count = conn.execute(
            "SELECT COUNT(*) FROM fact_status_history WHERE fact_id='f-001'"
        ).fetchone()[0]
        assert hist_count == 1

    def test_two_entity_rollback_leaves_no_partial(
        self, conn: sqlite3.Connection
    ) -> None:
        proposal = _make_proposal()
        with (
            patch(
                "auto_lorebook.approval.facts_mod.create_fact_with_targets",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            approve_proposal(
                conn,
                proposal=proposal,
                targets_resolved=[
                    ("characters", "theron", "biography"),
                    ("locations", "aldara", "founding"),
                ],
                by="test-user",
            )
        assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fact_targets").fetchone()[0] == 0

    def test_two_entity_idempotent_reapproval_silent(
        self, conn: sqlite3.Connection
    ) -> None:
        proposal = _make_proposal()
        approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[
                ("characters", "theron", "biography"),
                ("locations", "aldara", "founding"),
            ],
            by="test-user",
        )
        result = approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[
                ("characters", "theron", "biography"),
                ("locations", "aldara", "founding"),
            ],
            by="test-user",
        )
        assert result == ApprovalResult.SKIPPED_IDEMPOTENT
        ft_count = conn.execute(
            "SELECT COUNT(*) FROM fact_targets WHERE fact_id='f-001'"
        ).fetchone()[0]
        assert ft_count == 2


class TestApproveWithAliases:
    def test_confirmed_aliases_inserted_with_fact(
        self, conn: sqlite3.Connection
    ) -> None:
        proposal = _make_proposal()
        result = approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[("characters", "theron", "biography")],
            by="test-user",
            confirmed_aliases_per_target=[[("Iron King", "alias-confirmation")]],
            ingest_id="ing-001",
        )
        assert result == ApprovalResult.APPROVED
        row = conn.execute(
            "SELECT name, source, added_by_ingest FROM aliases"
            " WHERE entity_category='characters' AND entity_slug='theron'"
        ).fetchone()
        assert row is not None
        assert row[0] == "Iron King"
        assert row[1] == "alias-confirmation"
        assert row[2] == "ing-001"

    def test_alias_failure_rolls_back_fact(self, conn: sqlite3.Connection) -> None:
        proposal = _make_proposal()
        with pytest.raises(entities_mod.EntityError):
            approve_proposal(
                conn,
                proposal=proposal,
                targets_resolved=[("characters", "theron", "biography")],
                by="test-user",
                confirmed_aliases_per_target=[[("Iron King", "bogus-source")]],
                ingest_id="ing-001",
            )
        assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM fact_targets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0] == 0
        # proposal row still present (was never deleted)
        conn.execute(
            "INSERT INTO proposals(proposal_id, ingest_id, proposed_id, claim_group_id,"
            " text, raw_transcript_span, text_corrects_transcript,"
            " corrections_applied_json, source_id, locator, status,"
            " reading_section, reading_bullet_index)"
            " VALUES ('f-001','ing-001','f-001','cg-001',"
            " 'Theron founded Aldara.','Theron founded all-dara.',1,"
            " '[]','src-001','0:04:32','authoritative','[4:30-8:00]',0)"
        )
        conn.commit()
        # verify no orphan proposals were left in a broken state by the rollback
        count = conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE proposal_id='f-001'"
        ).fetchone()[0]
        assert count == 1

    def test_idempotent_reapprove_does_not_duplicate_aliases(
        self, conn: sqlite3.Connection
    ) -> None:
        proposal = _make_proposal()
        first = approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[("characters", "theron", "biography")],
            by="test-user",
            confirmed_aliases_per_target=[[("Iron King", "alias-confirmation")]],
            ingest_id="ing-001",
        )
        assert first == ApprovalResult.APPROVED

        # pre-seed a different alias between calls
        conn.execute(
            "INSERT INTO aliases(entity_category, entity_slug, name, name_normalized,"
            " added_by_ingest, added_at, source)"
            " VALUES ('characters','theron','the Realm',?,'ing-001',"
            " '2026-01-01T00:00:00Z','hand-edited')",
            (normalize_name("the Realm"),),
        )
        conn.commit()

        second = approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[("characters", "theron", "biography")],
            by="test-user",
            confirmed_aliases_per_target=[[("New Name", "alias-confirmation")]],
            ingest_id="ing-001",
        )
        assert second == ApprovalResult.SKIPPED_IDEMPOTENT
        # only "Iron King" + "the Realm" — "New Name" NOT inserted (skip path)
        count = conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
        assert count == 2

    def test_dedup_collision_is_silent_no_op(self, conn: sqlite3.Connection) -> None:
        # pre-seed alias with different casing
        conn.execute(
            "INSERT INTO aliases(entity_category, entity_slug, name, name_normalized,"
            " added_by_ingest, added_at, source)"
            " VALUES ('characters','theron','Iron King',?,'ing-001',"
            " '2026-01-01T00:00:00Z','hand-edited')",
            (normalize_name("Iron King"),),
        )
        conn.commit()

        proposal = _make_proposal()
        result = approve_proposal(
            conn,
            proposal=proposal,
            targets_resolved=[("characters", "theron", "biography")],
            by="test-user",
            # different casing, same normalized form
            confirmed_aliases_per_target=[[("iron king", "alias-confirmation")]],
            ingest_id="ing-001",
        )
        assert result == ApprovalResult.APPROVED
        count = conn.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
        assert count == 1  # pre-seeded row survives, no duplicate
