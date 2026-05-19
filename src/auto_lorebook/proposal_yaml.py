"""Stage 3 proposals: schema, parse helpers, DB I/O, file I/O (legacy).

DB API (conn-first):
    write_proposal(conn, ingest_id, p)
    read_proposal(conn, proposal_id) -> Proposal | None
    list_proposals(conn, ingest_id) -> list[Proposal]
    delete_proposal(conn, proposal_id) -> None
    delete_all_for_ingest(conn, ingest_id) -> None
    count_proposals(conn, ingest_id) -> int
    proposals_exist(conn, ingest_id) -> bool

File I/O (legacy):
    read(path), write(proposal, path)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text
from auto_lorebook.schema import SchemaVersionError, read_schema_version

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

_MAX_SCHEMA = 1

PROPOSAL_TYPES = frozenset({"new_fact", "new_entity_with_facts"})
CORRECTION_SOURCES = frozenset({
    "global-transcription-correction",
    "reading-name-correction",
})


class ProposalError(ValueError):
    """proposal yaml is missing or malformed on read."""


@dataclass(frozen=True)
class Correction:
    """One correction applied between raw transcript span and clean text."""

    from_: str  # YAML key is `from`; renamed to avoid Python keyword
    to: str
    source: str  # ∈ CORRECTION_SOURCES


@dataclass(frozen=True)
class ProposalTarget:
    """One target entity within a multi-target proposal."""

    entity: str
    section: str
    speaker: str
    proposal_type: str  # ∈ PROPOSAL_TYPES
    proposed_category: str | None = None


@dataclass(frozen=True)
class Proposal:
    """One proposed fact awaiting human review. Covers N entity targets."""

    proposed_id: str
    claim_group_id: str
    targets: list[ProposalTarget]
    text: str
    raw_transcript_span: str
    text_corrects_transcript: bool
    source_id: str
    locator: str
    reading_section: str
    reading_bullet_index: int
    status: str
    session_date: str
    context_before: str
    context_after: str
    corrections_applied: list[Correction] = field(default_factory=list)
    status_reason: str | None = None
    hint_widened: bool = False
    extractor_flagged: bool = False
    flag_reason: str | None = None


# ------------- to_dict ----------------------------------------------------


def _correction_to_dict(c: Correction) -> dict[str, Any]:
    return {"from": c.from_, "to": c.to, "source": c.source}


def _target_to_dict(t: ProposalTarget) -> dict[str, Any]:
    out: dict[str, Any] = {
        "entity": t.entity,
        "section": t.section,
        "speaker": t.speaker,
        "proposal_type": t.proposal_type,
    }
    if t.proposed_category is not None:
        out["proposed_category"] = t.proposed_category
    return out


def _to_dict(p: Proposal) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": _MAX_SCHEMA,
        "proposed_id": p.proposed_id,
        "claim_group_id": p.claim_group_id,
        "targets": [_target_to_dict(t) for t in p.targets],
        "text": p.text,
        "raw_transcript_span": p.raw_transcript_span,
        "text_corrects_transcript": p.text_corrects_transcript,
        "corrections_applied": [_correction_to_dict(c) for c in p.corrections_applied],
        "source_id": p.source_id,
        "locator": p.locator,
        "status": p.status,
    }
    if p.status_reason is not None:
        out["status_reason"] = p.status_reason
    out.update({
        "session_date": p.session_date,
        "reading_section": p.reading_section,
        "reading_bullet_index": p.reading_bullet_index,
        "context_before": p.context_before,
        "context_after": p.context_after,
    })
    if p.hint_widened:
        out["hint_widened"] = True
    if p.extractor_flagged:
        out["extractor_flagged"] = True
    if p.flag_reason is not None:
        out["flag_reason"] = p.flag_reason
    return out


# ------------- parse_* ----------------------------------------------------


def parse_correction(raw: dict[str, Any]) -> Correction:
    if not isinstance(raw, dict):
        msg = f"corrections_applied: expected mapping, got {type(raw).__name__}"
        raise ProposalError(msg)
    from_val = raw.get("from")
    to_val = raw.get("to")
    source_val = raw.get("source")
    if not isinstance(from_val, str) or not from_val:
        msg = "corrections_applied: missing 'from'"
        raise ProposalError(msg)
    if not isinstance(to_val, str) or not to_val:
        msg = "corrections_applied: missing 'to'"
        raise ProposalError(msg)
    if not isinstance(source_val, str) or source_val not in CORRECTION_SOURCES:
        msg = (
            f"corrections_applied: source must be one of "
            f"{sorted(CORRECTION_SOURCES)}, got {source_val!r}"
        )
        raise ProposalError(msg)
    return Correction(from_=from_val, to=to_val, source=source_val)


def _parse_target(raw: dict[str, Any]) -> ProposalTarget:
    if not isinstance(raw, dict):
        msg = f"targets: expected mapping, got {type(raw).__name__}"
        raise ProposalError(msg)
    entity = str(raw.get("entity") or "").strip()
    section = str(raw.get("section") or "").strip()
    speaker = str(raw.get("speaker") or "").strip()
    proposal_type = str(raw.get("proposal_type") or "").strip()
    if not entity:
        msg = "targets: empty entity"
        raise ProposalError(msg)
    if proposal_type not in PROPOSAL_TYPES:
        msg = (
            f"targets: proposal_type must be one of {sorted(PROPOSAL_TYPES)},"
            f" got {proposal_type!r}"
        )
        raise ProposalError(msg)
    return ProposalTarget(
        entity=entity,
        section=section,
        speaker=speaker,
        proposal_type=proposal_type,
        proposed_category=raw.get("proposed_category") or None,
    )


def _required_str(raw: dict[str, Any], key: str) -> str:
    val = raw.get(key)
    if not isinstance(val, str) or not val:
        msg = f"missing required string field {key!r}"
        raise ProposalError(msg)
    return val


def parse_proposal(raw: dict[str, Any]) -> Proposal:
    if not isinstance(raw, dict):
        msg = f"proposal: expected mapping, got {type(raw).__name__}"
        raise ProposalError(msg)
    bullet_idx = raw.get("reading_bullet_index")
    if not isinstance(bullet_idx, int) or bullet_idx < 0:
        msg = f"reading_bullet_index must be non-negative int, got {bullet_idx!r}"
        raise ProposalError(msg)
    targets_raw = raw.get("targets") or []
    if not isinstance(targets_raw, list):
        msg = "targets must be a list"
        raise ProposalError(msg)
    targets = [_parse_target(t) for t in targets_raw]
    return Proposal(
        proposed_id=_required_str(raw, "proposed_id"),
        claim_group_id=_required_str(raw, "claim_group_id"),
        targets=targets,
        text=str(raw.get("text") or ""),
        raw_transcript_span=str(raw.get("raw_transcript_span") or ""),
        text_corrects_transcript=bool(raw.get("text_corrects_transcript")),
        corrections_applied=[
            parse_correction(c) for c in (raw.get("corrections_applied") or [])
        ],
        source_id=_required_str(raw, "source_id"),
        locator=_required_str(raw, "locator"),
        reading_section=str(raw.get("reading_section") or ""),
        reading_bullet_index=bullet_idx,
        status=str(raw.get("status") or ""),
        status_reason=(raw.get("status_reason") or None),
        session_date=str(raw.get("session_date") or ""),
        context_before=str(raw.get("context_before") or ""),
        context_after=str(raw.get("context_after") or ""),
        hint_widened=bool(raw.get("hint_widened")),
        extractor_flagged=bool(raw.get("extractor_flagged")),
        flag_reason=(raw.get("flag_reason") or None),
    )


# ------------- read / write ----------------------------------------------


def read(path: Path) -> Proposal:
    """Read and parse a proposal yaml.

    :raises ProposalError: missing / malformed / unsupported schema
    """
    if not path.exists():
        msg = f"{path}: file not found"
        raise ProposalError(msg)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a YAML mapping"
        raise ProposalError(msg)
    try:
        read_schema_version(raw, str(path), max_supported=_MAX_SCHEMA)
    except SchemaVersionError as e:
        raise ProposalError(str(e)) from e
    try:
        return parse_proposal(raw)
    except ProposalError as e:
        msg = f"{path}: {e}"
        raise ProposalError(msg) from e


def write(proposal: Proposal, path: Path) -> None:
    """Atomically write a proposal yaml."""
    text = yaml.safe_dump(
        _to_dict(proposal),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    atomic_write_text(path, text)


# ------------- DB API -------------------------------------------------------


def _proposal_from_rows(
    proposal_row: Any,  # noqa: ANN401
    target_rows: list[Any],
) -> Proposal:
    corrs_raw = proposal_row["corrections_applied_json"]
    try:
        corrs_list = json.loads(corrs_raw) if corrs_raw else []
    except (json.JSONDecodeError, TypeError):
        corrs_list = []
    corrections = [
        Correction(
            from_=c.get("from", ""),
            to=c.get("to", ""),
            source=c.get("source", ""),
        )
        for c in corrs_list
    ]
    targets = [
        ProposalTarget(
            entity=row["entity_name"],
            section=row["section"],
            speaker=row["speaker"] or "",
            proposal_type=row["proposal_type"],
            proposed_category=row["proposed_category"],
        )
        for row in target_rows
    ]
    return Proposal(
        proposed_id=proposal_row["proposed_id"],
        claim_group_id=proposal_row["claim_group_id"],
        targets=targets,
        text=proposal_row["text"],
        raw_transcript_span=proposal_row["raw_transcript_span"],
        text_corrects_transcript=bool(proposal_row["text_corrects_transcript"]),
        corrections_applied=corrections,
        source_id=proposal_row["source_id"],
        locator=proposal_row["locator"],
        reading_section=proposal_row["reading_section"],
        reading_bullet_index=proposal_row["reading_bullet_index"],
        status=proposal_row["status"],
        status_reason=proposal_row["status_reason"],
        session_date=proposal_row["session_date"] or "",
        context_before=proposal_row["context_before"] or "",
        context_after=proposal_row["context_after"] or "",
        hint_widened=bool(proposal_row["hint_widened"]),
        extractor_flagged=bool(proposal_row["extractor_flagged"]),
        flag_reason=proposal_row["flag_reason"],
    )


def write_proposal(
    conn: sqlite3.Connection,
    ingest_id: str,
    p: Proposal,
) -> None:
    """INSERT proposal + proposal_targets rows. Caller owns the tx."""
    corrections_json = json.dumps([
        {"from": c.from_, "to": c.to, "source": c.source} for c in p.corrections_applied
    ])
    conn.execute(
        """
        INSERT INTO proposals (
            proposal_id, ingest_id, proposed_id, claim_group_id,
            text, raw_transcript_span, text_corrects_transcript,
            corrections_applied_json, source_id, locator,
            status, status_reason, session_date,
            reading_section, reading_bullet_index,
            context_before, context_after,
            extractor_flagged, hint_widened, inputs_json, flag_reason
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            p.proposed_id,  # proposal_id = proposed_id
            ingest_id,
            p.proposed_id,
            p.claim_group_id,
            p.text,
            p.raw_transcript_span,
            int(p.text_corrects_transcript),
            corrections_json,
            p.source_id,
            p.locator,
            p.status,
            p.status_reason,
            p.session_date or None,
            p.reading_section,
            p.reading_bullet_index,
            p.context_before or None,
            p.context_after or None,
            int(p.extractor_flagged),
            int(p.hint_widened),
            None,  # inputs_json
            p.flag_reason,
        ),
    )
    for position, target in enumerate(p.targets):
        conn.execute(
            """
            INSERT INTO proposal_targets
                (proposal_id, position, entity_name, section, speaker,
                 proposal_type, proposed_category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                p.proposed_id,
                position,
                target.entity,
                target.section,
                target.speaker or None,
                target.proposal_type,
                target.proposed_category,
            ),
        )


def read_proposal(
    conn: sqlite3.Connection,
    proposal_id: str,
) -> Proposal | None:
    """Return Proposal or None."""
    import sqlite3 as _sqlite3  # noqa: PLC0415

    conn.row_factory = _sqlite3.Row
    row = conn.execute(
        "SELECT * FROM proposals WHERE proposal_id=?", (proposal_id,)
    ).fetchone()
    if row is None:
        return None
    target_rows = conn.execute(
        "SELECT * FROM proposal_targets WHERE proposal_id=? ORDER BY position",
        (proposal_id,),
    ).fetchall()
    return _proposal_from_rows(row, target_rows)


def list_proposals(
    conn: sqlite3.Connection,
    ingest_id: str,
) -> list[Proposal]:
    """Return all proposals for ingest_id, in rowid order."""
    import sqlite3 as _sqlite3  # noqa: PLC0415

    conn.row_factory = _sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM proposals WHERE ingest_id=? ORDER BY rowid",
        (ingest_id,),
    ).fetchall()
    result: list[Proposal] = []
    for row in rows:
        target_rows = conn.execute(
            "SELECT * FROM proposal_targets WHERE proposal_id=? ORDER BY position",
            (row["proposal_id"],),
        ).fetchall()
        result.append(_proposal_from_rows(row, target_rows))
    return result


def delete_proposal(conn: sqlite3.Connection, proposal_id: str) -> None:
    """DELETE proposal by proposal_id; silent if absent. CASCADE deletes targets."""
    conn.execute("DELETE FROM proposals WHERE proposal_id=?", (proposal_id,))


def delete_all_for_ingest(conn: sqlite3.Connection, ingest_id: str) -> None:
    """DELETE all proposals for an ingest. CASCADE deletes targets."""
    conn.execute("DELETE FROM proposals WHERE ingest_id=?", (ingest_id,))


def count_proposals(conn: sqlite3.Connection, ingest_id: str) -> int:
    """Count remaining proposals for ingest_id."""
    return conn.execute(
        "SELECT COUNT(*) FROM proposals WHERE ingest_id=?", (ingest_id,)
    ).fetchone()[0]


def proposals_exist(conn: sqlite3.Connection, ingest_id: str) -> bool:
    """Return True if any proposals row exists for ingest_id."""
    return count_proposals(conn, ingest_id) > 0
