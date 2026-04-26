"""Stage 4 (review) engine: walk proposals, mutate entity YAMLs.

Pure-logic module. No `input()` calls. Display + prompt I/O lives in
`commands/review.py` and is injected via the `Reviewer` protocol. Tests
script a `Reviewer` to drive the engine deterministically.

Walk order is authoritative from the plan: we iterate
`plan.planned_claims` then `claim.targets`, looking up each
`(claim_group_id, target_entity)` against the on-disk proposal files.
This preserves the spec's "siblings shown contiguously, transcript
order across groups" no matter how proposed_id slugs sort
lexicographically.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from auto_lorebook import config as cfg_mod
from auto_lorebook import (
    entity_index as entity_index_mod,
)
from auto_lorebook import entity_yaml as entity_yaml_mod
from auto_lorebook import info_yaml as info_yaml_mod
from auto_lorebook import plan_yaml as plan_yaml_mod
from auto_lorebook import proposal_yaml as proposal_yaml_mod
from auto_lorebook import reading_pipeline
from auto_lorebook.entity_yaml import Alias, Entity, normalize_alias_name
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    from pathlib import Path

    from auto_lorebook.info_yaml import Info
    from auto_lorebook.plan_yaml import Plan
    from auto_lorebook.proposal_yaml import Proposal

_logger = logging.getLogger(__name__)


class ReviewError(RuntimeError):
    """Raised for unrecoverable engine failures."""


# ------------- decisions / reviewer protocol -----------------------------


@dataclass(frozen=True)
class ApproveDecision:
    """Approve as-is."""


@dataclass(frozen=True)
class EditDecision:
    """Approve with edited text. `text_source` will retain the original."""

    new_text: str


@dataclass(frozen=True)
class RejectDecision:
    """Discard the proposal."""


Decision = ApproveDecision | EditDecision | RejectDecision


@dataclass(frozen=True)
class ProposalView:
    """All display-relevant data for one proposal."""

    proposal: Proposal
    proposal_index: int  # 1-of-N across this run
    proposal_total: int
    group_position: int  # 1-of-K within claim group
    group_size: int
    is_new_entity: bool
    new_entity_category: str | None
    created_earlier_in_session: bool
    suggested_aliases: tuple[str, ...]
    matched_via: str | None
    source_url: str | None
    source_title: str | None


class Reviewer(Protocol):
    """Decision-maker injected into `run`. Tests script this directly."""

    @property
    def by_label(self) -> str:
        """Recorded as `status_history[].by` on approved facts."""
        ...

    def decide(self, view: ProposalView) -> Decision: ...

    def confirm_alias(self, entity: str, mention: str) -> bool: ...


@dataclass
class ReviewResult:
    approved: int = 0
    edited: int = 0
    rejected: int = 0
    remaining: int = 0  # > 0 only after KeyboardInterrupt


# ------------- ordering / lookup helpers ---------------------------------


def sorted_proposals(plan: Plan, source_id: str) -> list[Proposal]:
    """Walk plan in order; yield proposals still on disk."""
    proposals_dir = reading_pipeline.pending_proposals_dir(source_id)
    if not proposals_dir.is_dir():
        return []
    by_key: dict[tuple[str, str], Proposal] = {}
    for path in sorted(proposals_dir.glob("*.yaml")):
        try:
            p = proposal_yaml_mod.read(path)
        except proposal_yaml_mod.ProposalError:
            _logger.warning("review: could not parse %s; skipping", path)
            continue
        by_key[p.claim_group_id, p.target_entity] = p

    out: list[Proposal] = []
    for claim in plan.planned_claims:
        for target in claim.targets:
            key = (claim.claim_group_id, target.entity)
            p = by_key.pop(key, None)
            if p is not None:
                out.append(p)
    # Any proposal whose plan-key didn't match (orphan) gets appended
    # deterministically by file order so we never silently drop work.
    out.extend(by_key[key] for key in sorted(by_key))
    return out


def _suggested_aliases_for(plan: Plan, proposal: Proposal) -> tuple[str, ...]:
    """Union of planner-suggested aliases for this proposal's target entity."""
    seen: dict[str, None] = {}  # ordered set
    if proposal.proposal_type == "new_entity_with_facts":
        for n in plan.new_entities:
            if n.name == proposal.target_entity:
                for alias in n.aliases_suggested:
                    seen[alias] = None
    for r in plan.entity_resolutions:
        if proposal.target_entity in {r.matched_entity, r.proposed_entity_name}:
            for alias in r.suggested_aliases_to_add:
                seen[alias] = None
    return tuple(seen.keys())


def _matched_via_for(plan: Plan, proposal: Proposal) -> str | None:
    """Join `mention` strings from existing-entity resolutions; None if no match."""
    if proposal.proposal_type != "new_fact":
        return None
    mentions = [
        r.mention
        for r in plan.entity_resolutions
        if r.resolution == "existing"
        and r.matched_entity == proposal.target_entity
        and r.mention
    ]
    if not mentions:
        return None
    return " / ".join(f'"{m}"' for m in mentions)


def _category_for_new(plan: Plan, target_entity: str) -> str | None:
    for n in plan.new_entities:
        if n.name == target_entity:
            return n.category
    return None


# ------------- fact dict builder -----------------------------------------


def proposal_to_fact_dict(
    proposal: Proposal,
    *,
    edited_text: str | None,
    ingest_id: str,
    by_label: str,
) -> dict[str, Any]:
    """Build the dict appended to `entity.facts`.

    `edited_text=None` means approve as-is. Non-None means human edited.
    """
    now = format_iso_now()
    text = edited_text if edited_text is not None else proposal.text
    edited_by_human = edited_text is not None
    return {
        "id": proposal.proposed_id,
        "text": text,
        "raw_transcript_span": proposal.raw_transcript_span,
        "text_corrects_transcript": proposal.text_corrects_transcript
        or edited_by_human,
        "corrections_applied": [
            {"from": c.from_, "to": c.to, "source": c.source}
            for c in proposal.corrections_applied
        ],
        "edited_by_human": edited_by_human,
        "edited_at": now if edited_by_human else None,
        "text_source": proposal.text if edited_by_human else None,
        "source_id": proposal.source_id,
        "locator": proposal.locator,
        "speaker": proposal.speaker,
        "status": proposal.status,
        "status_reason": proposal.status_reason,
        "status_history": [
            {
                "status": proposal.status,
                "at": now,
                "by": by_label,
                "reason": proposal.status_reason,
            },
        ],
        "session_date": proposal.session_date,
        "approved_at": now,
        "created_by_ingest": ingest_id,
        "claim_group_id": proposal.claim_group_id,
        "section": proposal.section,
    }


# ------------- approval mechanics ----------------------------------------


@dataclass
class _ApprovalContext:
    """Mutable state shared across the loop."""

    cfg: cfg_mod.Config
    source_id: str
    info: Info
    plan: Plan
    index: entity_index_mod.EntityIndex
    merged_aliases: set[tuple[str, str]] = field(default_factory=set)


def _resolve_entity_path(
    ctx: _ApprovalContext, proposal: Proposal
) -> tuple[Path, str, str]:
    """Return (path, slug, category) for the proposal's target entity.

    The slug must agree with the slug Stage 3 used when generating
    `proposed_id`. Both go through `entity_yaml.slugify`, so they can't
    drift in practice — guarded by the integration test.
    """
    if proposal.proposal_type == "new_entity_with_facts":
        category = _category_for_new(ctx.plan, proposal.target_entity)
        if category is None:
            msg = (
                f"proposal {proposal.proposed_id}: target {proposal.target_entity!r} "
                f"is new but missing from plan.new_entities"
            )
            raise ReviewError(msg)
        slug = entity_yaml_mod.slugify(proposal.target_entity)
        path = ctx.cfg.wiki_repo_path / category / f"{slug}.yaml"
        return path, slug, category

    entry = ctx.index.lookup(proposal.target_entity)
    if entry is None:
        msg = (
            f"proposal {proposal.proposed_id}: target {proposal.target_entity!r} "
            f"is marked existing but not found in entity index"
        )
        raise ReviewError(msg)
    path = ctx.cfg.wiki_repo_path / entry.category / f"{entry.slug}.yaml"
    return path, entry.slug, entry.category


def _approve(
    ctx: _ApprovalContext,
    proposal: Proposal,
    proposal_path: Path,
    *,
    edited_text: str | None,
    confirmed_aliases: list[str],
    by_label: str,
) -> bool:
    """Return True if a fact was appended (False if idempotent skip)."""
    path, slug, category = _resolve_entity_path(ctx, proposal)
    now = format_iso_now()
    fact = proposal_to_fact_dict(
        proposal,
        edited_text=edited_text,
        ingest_id=ctx.source_id,
        by_label=by_label,
    )

    if path.exists():
        entity = entity_yaml_mod.read(path)
        # idempotent guard: same proposed_id already approved
        if any(f.get("id") == proposal.proposed_id for f in entity.facts):
            _logger.info(
                "review: fact %s already in %s; skipping append",
                proposal.proposed_id,
                path,
            )
            proposal_path.unlink(missing_ok=True)
            return False
        for alias in confirmed_aliases:
            entity.aliases.append(
                Alias(
                    name=alias,
                    added_by_ingest=ctx.source_id,
                    added_at=now,
                    source="alias-confirmation",
                ),
            )
        entity.facts.append(fact)
        entity.updated_at = now
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        entity = Entity(
            entity=proposal.target_entity,
            category=category,
            slug=slug,
            aliases=[
                Alias(
                    name=alias,
                    added_by_ingest=ctx.source_id,
                    added_at=now,
                    source="stub-creation",
                )
                for alias in confirmed_aliases
            ],
            created_at=now,
            created_by_ingest=ctx.source_id,
            updated_at=now,
            facts=[fact],
        )
    entity_yaml_mod.write(entity, path)
    proposal_path.unlink(missing_ok=True)
    # refresh in-memory index so siblings later in the loop see this entity
    ctx.index = entity_index_mod.build(ctx.cfg.wiki_repo_path)
    # record merged aliases so sibling prompts skip them
    target_key = proposal.target_entity.casefold()
    for alias in confirmed_aliases:
        ctx.merged_aliases.add((target_key, normalize_alias_name(alias)))
    return True


def _reject(proposal_path: Path) -> None:
    proposal_path.unlink(missing_ok=True)


# ------------- main loop -------------------------------------------------


def _build_view(
    ctx: _ApprovalContext,
    proposal: Proposal,
    *,
    proposal_index: int,
    proposal_total: int,
    group_position: int,
    group_size: int,
) -> ProposalView:
    is_new = proposal.proposal_type == "new_entity_with_facts"
    category = _category_for_new(ctx.plan, proposal.target_entity) if is_new else None
    suggested = _suggested_aliases_for(ctx.plan, proposal)
    target_key = proposal.target_entity.casefold()
    suggested = tuple(
        a
        for a in suggested
        if (target_key, normalize_alias_name(a)) not in ctx.merged_aliases
    )
    matched_via = _matched_via_for(ctx.plan, proposal)
    # "created earlier in session": entity exists on disk and was created
    # by this same ingest run.
    created_earlier = False
    if is_new:
        existing_entry = ctx.index.lookup(proposal.target_entity)
        if existing_entry is not None:
            entity_path = (
                ctx.cfg.wiki_repo_path
                / existing_entry.category
                / f"{existing_entry.slug}.yaml"
            )
            try:
                existing = entity_yaml_mod.read(entity_path)
                if existing.created_by_ingest == ctx.source_id:
                    created_earlier = True
            except entity_yaml_mod.EntityError:
                pass
    return ProposalView(
        proposal=proposal,
        proposal_index=proposal_index,
        proposal_total=proposal_total,
        group_position=group_position,
        group_size=group_size,
        is_new_entity=is_new,
        new_entity_category=category,
        created_earlier_in_session=created_earlier,
        suggested_aliases=suggested,
        matched_via=matched_via,
        source_url=ctx.info.source_url,
        source_title=ctx.info.title,
    )


def run(
    *,
    cfg: cfg_mod.Config,
    source_id: str,
    reviewer: Reviewer,
) -> ReviewResult:
    """Walk pending proposals; mutate entity YAMLs; return counts.

    KeyboardInterrupt from `reviewer.decide` propagates after marking
    the not-yet-decided proposals as `remaining`.
    """
    wiki_repo = cfg.wiki_repo_path
    info_path = wiki_repo / "sources" / source_id / "info.yaml"
    info = info_yaml_mod.read(info_path)
    plan_path = reading_pipeline.pending_plan_path(source_id)
    if not plan_path.exists():
        msg = f"No plan at {plan_path}; run `approve-reading {source_id}` first."
        raise ReviewError(msg)
    plan = plan_yaml_mod.read(plan_path)
    index = entity_index_mod.build(wiki_repo)
    ctx = _ApprovalContext(
        cfg=cfg, source_id=source_id, info=info, plan=plan, index=index
    )

    ordered = sorted_proposals(plan, source_id)
    if not ordered:
        return ReviewResult()

    # claim-group sizes for "K of M" header
    group_sizes: dict[str, int] = {}
    for p in ordered:
        group_sizes[p.claim_group_id] = group_sizes.get(p.claim_group_id, 0) + 1
    group_seen: dict[str, int] = {}

    result = ReviewResult()
    total = len(ordered)
    for i, proposal in enumerate(ordered, start=1):
        group_seen[proposal.claim_group_id] = (
            group_seen.get(proposal.claim_group_id, 0) + 1
        )
        view = _build_view(
            ctx,
            proposal,
            proposal_index=i,
            proposal_total=total,
            group_position=group_seen[proposal.claim_group_id],
            group_size=group_sizes[proposal.claim_group_id],
        )
        try:
            decision = reviewer.decide(view)
        except KeyboardInterrupt:
            result.remaining = total - (i - 1)
            raise
        if isinstance(decision, RejectDecision):
            proposal_path = reading_pipeline.pending_proposal_path(
                source_id, proposal.proposed_id
            )
            _reject(proposal_path)
            result.rejected += 1
            continue
        # approve / edit: gather alias confirmations
        confirmed: list[str] = []
        for alias in view.suggested_aliases:
            try:
                if reviewer.confirm_alias(proposal.target_entity, alias):
                    confirmed.append(alias)
            except KeyboardInterrupt:
                result.remaining = total - (i - 1)
                raise
        edited_text = decision.new_text if isinstance(decision, EditDecision) else None
        proposal_path = reading_pipeline.pending_proposal_path(
            source_id, proposal.proposed_id
        )
        appended = _approve(
            ctx,
            proposal,
            proposal_path,
            edited_text=edited_text,
            confirmed_aliases=confirmed,
            by_label=reviewer.by_label,
        )
        if appended:
            if isinstance(decision, EditDecision):
                result.edited += 1
            else:
                result.approved += 1
    return result
