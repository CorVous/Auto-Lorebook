"""Stage 3 `proposals/<proposal_id>.yaml`: schema, read/write.

Mirrors `plan_yaml.py` shape. Each proposal is one file under
``pending/<source_id>/proposals/`` representing a single fact's worth of
located transcript span, awaiting human review.

For ``proposal_type == "new_entity_with_facts"`` the ``target_entity``
field carries the *proposed* (not-yet-canonical) name from the plan;
the entity stub is created atomically on first approval (Phase 4 review
loop, future PR).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text
from auto_lorebook.schema import SchemaVersionError, read_schema_version

if TYPE_CHECKING:
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
class Sibling:
    """One sibling entry in `claim_group_siblings`."""

    entity: str
    proposed_id: str


@dataclass(frozen=True)
class Proposal:
    """One proposed fact awaiting human review."""

    proposal_type: str  # ∈ PROPOSAL_TYPES
    target_entity: str
    proposed_id: str
    claim_group_id: str
    text: str
    raw_transcript_span: str
    text_corrects_transcript: bool
    source_id: str
    locator: str
    speaker: str
    reading_section: str
    reading_bullet_index: int
    status: str
    session_date: str
    section: str
    context_before: str
    context_after: str
    claim_group_siblings: list[Sibling] = field(default_factory=list)
    corrections_applied: list[Correction] = field(default_factory=list)
    status_reason: str | None = None
    hint_widened: bool = False
    extractor_flagged: bool = False
    flag_reason: str | None = None


# ------------- to_dict ----------------------------------------------------


def _correction_to_dict(c: Correction) -> dict[str, Any]:
    return {"from": c.from_, "to": c.to, "source": c.source}


def _sibling_to_dict(s: Sibling) -> dict[str, Any]:
    return {"entity": s.entity, "proposed_id": s.proposed_id}


def _to_dict(p: Proposal) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": _MAX_SCHEMA,
        "proposal_type": p.proposal_type,
        "target_entity": p.target_entity,
        "proposed_id": p.proposed_id,
        "claim_group_id": p.claim_group_id,
        "claim_group_siblings": [_sibling_to_dict(s) for s in p.claim_group_siblings],
        "text": p.text,
        "raw_transcript_span": p.raw_transcript_span,
        "text_corrects_transcript": p.text_corrects_transcript,
        "corrections_applied": [_correction_to_dict(c) for c in p.corrections_applied],
        "source_id": p.source_id,
        "locator": p.locator,
        "speaker": p.speaker,
        "status": p.status,
    }
    if p.status_reason is not None:
        out["status_reason"] = p.status_reason
    out.update({
        "session_date": p.session_date,
        "section": p.section,
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


def parse_sibling(raw: dict[str, Any]) -> Sibling:
    if not isinstance(raw, dict):
        msg = f"claim_group_siblings: expected mapping, got {type(raw).__name__}"
        raise ProposalError(msg)
    entity = str(raw.get("entity") or "").strip()
    proposed_id = str(raw.get("proposed_id") or "").strip()
    if not entity:
        msg = "claim_group_siblings: empty entity"
        raise ProposalError(msg)
    if not proposed_id:
        msg = "claim_group_siblings: empty proposed_id"
        raise ProposalError(msg)
    return Sibling(entity=entity, proposed_id=proposed_id)


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
    proposal_type = _required_str(raw, "proposal_type")
    if proposal_type not in PROPOSAL_TYPES:
        msg = (
            f"proposal_type must be one of {sorted(PROPOSAL_TYPES)}, "
            f"got {proposal_type!r}"
        )
        raise ProposalError(msg)
    bullet_idx = raw.get("reading_bullet_index")
    if not isinstance(bullet_idx, int) or bullet_idx < 0:
        msg = f"reading_bullet_index must be non-negative int, got {bullet_idx!r}"
        raise ProposalError(msg)
    return Proposal(
        proposal_type=proposal_type,
        target_entity=_required_str(raw, "target_entity"),
        proposed_id=_required_str(raw, "proposed_id"),
        claim_group_id=_required_str(raw, "claim_group_id"),
        claim_group_siblings=[
            parse_sibling(s) for s in (raw.get("claim_group_siblings") or [])
        ],
        text=str(raw.get("text") or ""),
        raw_transcript_span=str(raw.get("raw_transcript_span") or ""),
        text_corrects_transcript=bool(raw.get("text_corrects_transcript")),
        corrections_applied=[
            parse_correction(c) for c in (raw.get("corrections_applied") or [])
        ],
        source_id=_required_str(raw, "source_id"),
        locator=_required_str(raw, "locator"),
        speaker=str(raw.get("speaker") or ""),
        reading_section=str(raw.get("reading_section") or ""),
        reading_bullet_index=bullet_idx,
        status=str(raw.get("status") or ""),
        status_reason=(raw.get("status_reason") or None),
        session_date=str(raw.get("session_date") or ""),
        section=str(raw.get("section") or ""),
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
