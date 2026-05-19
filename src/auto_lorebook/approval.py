"""Single-target proposal approval: idempotent, transaction-owning.

Public API:
    ApprovalResult
    approve_proposal
"""

from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING

from auto_lorebook import facts as facts_mod
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    import sqlite3

    from auto_lorebook.proposal_yaml import Proposal

_logger = logging.getLogger(__name__)


class ApprovalResult(enum.Enum):
    """Return value of approve_proposal."""

    APPROVED = "approved"
    SKIPPED_IDEMPOTENT = "skipped_idempotent"


def approve_proposal(
    conn: sqlite3.Connection,
    *,
    proposal: Proposal,
    entity_category: str,
    entity_slug: str,
    section: str,
    by: str,
    when: str | None = None,
    edited_text: str | None = None,
    edited_speaker: str | None = None,
    edited_status: str | None = None,
    edited_status_reason: str | None = None,
    inputs_json: str | None = None,
) -> ApprovalResult:
    """Insert fact row + delete proposal; own the transaction.

    Issues BEGIN IMMEDIATE / COMMIT. Idempotent: if a facts row with the
    same id already exists, skips the insert and deletes the proposal row
    without error.

    Rolls back and re-raises on any other exception.
    """
    now = when or format_iso_now()
    fact_id = proposal.proposed_id
    text = edited_text if edited_text is not None else proposal.text
    speaker = edited_speaker if edited_speaker is not None else proposal.speaker
    status = edited_status if edited_status is not None else proposal.status
    status_reason = (
        edited_status_reason
        if edited_status_reason is not None
        else proposal.status_reason
    )
    edited_by_human = edited_text is not None
    edited_at = now if edited_by_human else None
    text_source = proposal.text if edited_by_human else None
    corrections = [
        {"from": c.from_, "to": c.to, "source": c.source}
        for c in proposal.corrections_applied
    ]

    conn.execute("BEGIN IMMEDIATE")
    try:
        # idempotent guard: fact already committed
        existing = conn.execute(
            "SELECT id FROM facts WHERE id=?", (fact_id,)
        ).fetchone()
        if existing is not None:
            _logger.info("approval: fact %s already exists; skipping insert", fact_id)
            conn.execute("DELETE FROM proposals WHERE proposed_id=?", (fact_id,))
            conn.execute("COMMIT")
            return ApprovalResult.SKIPPED_IDEMPOTENT

        facts_mod.create_fact_with_target(
            conn,
            fact_id=fact_id,
            text=text,
            raw_transcript_span=proposal.raw_transcript_span,
            text_corrects_transcript=(
                proposal.text_corrects_transcript or edited_by_human
            ),
            source_id=proposal.source_id,
            locator=proposal.locator,
            status=status,
            approved_at=now,
            created_by_ingest=proposal.source_id,
            entity_category=entity_category,
            entity_slug=entity_slug,
            section=section,
            by=by,
            text_source=text_source,
            edited_by_human=edited_by_human,
            edited_at=edited_at,
            speaker=speaker,
            status_reason=status_reason,
            session_date=proposal.session_date or None,
            corrections_applied=corrections,
            inputs_json=inputs_json,
        )

        # delete proposal row (silent if absent — filesystem proposal removed earlier)
        conn.execute("DELETE FROM proposals WHERE proposed_id=?", (fact_id,))

    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
        return ApprovalResult.APPROVED
