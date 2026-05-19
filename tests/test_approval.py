"""Tests for approval.py — single-target proposal approval."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from auto_lorebook import db
from auto_lorebook.approval import ApprovalResult, approve_proposal
from auto_lorebook.proposal_yaml import Proposal

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
    c.commit()
    yield c
    c.close()


def _make_proposal(**overrides: Any) -> Proposal:  # noqa: ANN401
    base: dict[str, Any] = {
        "proposal_type": "new_fact",
        "target_entity": "Theron",
        "proposed_id": "f-001",
        "claim_group_id": "cg-001",
        "text": "Theron founded Aldara.",
        "raw_transcript_span": "Theron founded all-dara.",
        "text_corrects_transcript": True,
        "source_id": "src-001",
        "locator": "0:04:32",
        "speaker": "DM",
        "reading_section": "[4:30-8:00]",
        "reading_bullet_index": 0,
        "status": "authoritative",
        "session_date": "2026-01-15",
        "section": "biography",
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
            entity_category="characters",
            entity_slug="theron",
            section="biography",
            by="test-user",
        )
        assert result == ApprovalResult.APPROVED

    def test_fact_row_inserted(self, conn: sqlite3.Connection) -> None:
        proposal = _make_proposal()
        approve_proposal(
            conn,
            proposal=proposal,
            entity_category="characters",
            entity_slug="theron",
            section="biography",
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
            entity_category="characters",
            entity_slug="theron",
            section="biography",
            by="test-user",
        )
        second = approve_proposal(
            conn,
            proposal=proposal,
            entity_category="characters",
            entity_slug="theron",
            section="biography",
            by="test-user",
        )
        assert first == ApprovalResult.APPROVED
        assert second == ApprovalResult.SKIPPED_IDEMPOTENT

    def test_edited_text_reflected_in_fact(self, conn: sqlite3.Connection) -> None:
        proposal = _make_proposal()
        approve_proposal(
            conn,
            proposal=proposal,
            entity_category="characters",
            entity_slug="theron",
            section="biography",
            by="test-user",
            edited_text="King Theron founded Aldara.",
        )
        row = conn.execute(
            "SELECT text, edited_by_human FROM facts WHERE id='f-001'"
        ).fetchone()
        assert row[0] == "King Theron founded Aldara."
        assert row[1] == 1

    def test_proposal_row_deleted_on_approval(self, conn: sqlite3.Connection) -> None:
        # seed a proposal row in DB
        conn.execute(
            "INSERT INTO plan_routes(ingest_id, claim_group_id, target_entity_name,"
            " entity_state, proposed_section, proposed_status, locator, locator_hint,"
            " reading_section, reading_bullet_index)"
            " VALUES ('ing-001','cg-001','Theron','existing','biography',"
            " 'authoritative','0:04:32','0:04:00-0:05:00','[4:30-8:00]',0)"
        )
        route_id = conn.execute(
            "SELECT id FROM plan_routes WHERE ingest_id='ing-001'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO proposals(proposal_id, ingest_id, plan_route_id,"
            " proposal_type, target_entity_name, proposed_id, claim_group_id,"
            " text, raw_transcript_span, text_corrects_transcript,"
            " corrections_applied_json, source_id, locator, status, section,"
            " reading_section, reading_bullet_index)"
            " VALUES ('f-001','ing-001',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                route_id,
                "new_fact",
                "Theron",
                "f-001",
                "cg-001",
                "Theron founded Aldara.",
                "Theron founded all-dara.",
                1,
                "[]",
                "src-001",
                "0:04:32",
                "authoritative",
                "biography",
                "[4:30-8:00]",
                0,
            ),
        )
        conn.commit()
        proposal = _make_proposal()
        approve_proposal(
            conn,
            proposal=proposal,
            entity_category="characters",
            entity_slug="theron",
            section="biography",
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
                "auto_lorebook.approval.facts_mod.create_fact_with_target",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            approve_proposal(
                conn,
                proposal=proposal,
                entity_category="characters",
                entity_slug="theron",
                section="biography",
                by="test-user",
            )
        # fact should not exist after rollback
        row = conn.execute("SELECT id FROM facts WHERE id='f-001'").fetchone()
        assert row is None
