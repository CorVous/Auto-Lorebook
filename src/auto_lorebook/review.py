"""Stage 4 (review) engine: walk bundles, mutate entity YAMLs.

Pure-logic module. No `input()` calls. Display + prompt I/O lives in
`commands/review.py` and is injected via the `Reviewer` protocol. Tests
script a `Reviewer` to drive the engine deterministically.

Walk order is authoritative from the plan: we iterate
`plan.planned_claims` then `claim.targets`, looking up each
`(claim_group_id, target_entity)` against the on-disk proposal files.
Consecutive proposals sharing a `claim_group_id` are surfaced as one
`BundleView`; the reviewer decides once per bundle and may drop
individual routes before approval via `BundleDecision.selected_indices`.
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
class BundleEdits:
    """Bundle-level overrides: claim-wide fields only.

    ``new_text`` triggers ``edited_by_human=True`` + ``text_source``
    on every checked route. Status fields change the recorded value
    (reflected in ``status_history``). Section/speaker are absent by
    design — those are route-shaped and live in ``TargetEdits``.
    """

    new_text: str | None = None
    new_status: str | None = None
    new_status_reason: str | None = None

    def is_noop(self) -> bool:
        """Return True when no field override is set."""
        return (
            self.new_text is None
            and self.new_status is None
            and self.new_status_reason is None
        )


@dataclass(frozen=True)
class TargetEdits:
    """Per-target overrides: route-shaped fields only.

    Section and speaker differ per entity, so they live here rather
    than in ``BundleEdits``. Text/status fields are absent by design.
    """

    new_section: str | None = None
    new_speaker: str | None = None

    def is_noop(self) -> bool:
        """Return True when no field override is set."""
        return self.new_section is None and self.new_speaker is None


@dataclass(frozen=True)
class MergedEdits:
    """Internal shape after layering bundle + per-target overrides.

    Consumed only by ``proposal_to_fact_dict`` and ``_approve``.
    """

    new_text: str | None = None
    new_speaker: str | None = None
    new_status: str | None = None
    new_status_reason: str | None = None
    new_section: str | None = None

    def is_noop(self) -> bool:
        """Return True when no field override is set."""
        return all(
            v is None
            for v in (
                self.new_text,
                self.new_speaker,
                self.new_status,
                self.new_status_reason,
                self.new_section,
            )
        )


@dataclass(frozen=True)
class RejectDecision:
    """Discard the proposal."""


Decision = ApproveDecision | BundleEdits | RejectDecision


@dataclass(frozen=True)
class TargetView:
    """Per-route display data inside a bundle."""

    proposal: Proposal
    is_new_entity: bool
    new_entity_category: str | None
    created_earlier_in_session: bool
    suggested_aliases: tuple[str, ...]
    matched_via: str | None


@dataclass(frozen=True)
class BundleView:
    """One claim-group, shown as a single review screen."""

    bundle_index: int  # 1-of-N bundles
    bundle_total: int
    claim_group_id: str
    targets: tuple[TargetView, ...]  # plan order
    source_url: str | None
    source_title: str | None


@dataclass(frozen=True)
class BundleDecision:
    """Result of one `decide_bundle` call.

    `decision` is bundle-wide: ``ApproveDecision`` or ``BundleEdits``
    (text/status/status_reason) or ``RejectDecision``. `selected_indices`
    lists which target rows the user kept checked. Unselected targets are
    dropped on Approve / Edit; Reject discards the whole bundle (selection
    ignored). `per_target_overrides[i]` holds ``TargetEdits``
    (section/speaker) for route `i` — disjoint from bundle-level fields.
    """

    decision: ApproveDecision | BundleEdits | RejectDecision
    selected_indices: tuple[int, ...]
    per_target_overrides: dict[int, TargetEdits] = field(default_factory=dict)


class Reviewer(Protocol):
    """Decision-maker injected into `run`. Tests script this directly."""

    @property
    def by_label(self) -> str:
        """Recorded as `status_history[].by` on approved facts."""
        ...

    def decide_bundle(self, view: BundleView) -> BundleDecision: ...

    def confirm_alias(self, entity: str, mention: str) -> bool: ...


@dataclass
class ReviewResult:
    approved: int = 0
    edited: int = 0
    rejected: int = 0
    remaining: int = 0  # > 0 only after KeyboardInterrupt


# ------------- ordering / lookup helpers ---------------------------------


def _validate_proposals_subset_of_plan(plan: Plan, source_id: str) -> None:
    """Raise ReviewError if any on-disk proposal is not in plan keys.

    Missing keys are fine (subset allowed — covers Ctrl-C resume).
    Orphans (extra keys) indicate drift; user must replan.
    """
    proposals_dir = reading_pipeline.pending_proposals_dir(source_id)
    if not proposals_dir.is_dir():
        return
    plan_keys: set[tuple[str, str]] = {
        (claim.claim_group_id, target.entity)
        for claim in plan.planned_claims
        for target in claim.targets
    }
    orphans: list[str] = []
    for path in sorted(proposals_dir.glob("*.yaml")):
        try:
            p = proposal_yaml_mod.read(path)
        except proposal_yaml_mod.ProposalError:
            _logger.warning("review: could not parse %s; skipping", path)
            continue
        if (p.claim_group_id, p.target_entity) not in plan_keys:
            orphans.append(
                f"  - {path}  (claim_group_id={p.claim_group_id},"
                f" target_entity={p.target_entity})"
            )
    if orphans:
        lines = "\n".join(orphans)
        msg = (
            f"Orphan proposals not in plan"
            f" (run `auto-lorebook replan {source_id}` to recover):\n{lines}"
        )
        raise ReviewError(msg)


def sorted_proposals(plan: Plan, source_id: str) -> list[Proposal]:
    """Walk plan in order; yield proposals still on disk.

    Precondition: on-disk proposals are a subset of plan keys
    (enforced by _validate_proposals_subset_of_plan before this runs).
    """
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
    edits: MergedEdits | None,
    ingest_id: str,
    by_label: str,
) -> dict[str, Any]:
    """Build the dict appended to `entity.facts`.

    ``edits=None`` means approve as-is. A non-None ``edits.new_text``
    triggers ``edited_by_human=True`` + ``text_source``; speaker /
    status / status_reason / section overrides change the recorded
    value (and ``status_history`` reflects the *final* status).
    """
    now = format_iso_now()
    edited_text = edits.new_text if edits else None
    edited_by_human = edited_text is not None
    text = edited_text if edited_text is not None else proposal.text
    speaker = edits.new_speaker if edits and edits.new_speaker else proposal.speaker
    status = edits.new_status if edits and edits.new_status else proposal.status
    status_reason = (
        edits.new_status_reason
        if edits and edits.new_status_reason is not None
        else proposal.status_reason
    )
    section = edits.new_section if edits and edits.new_section else proposal.section
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
        "speaker": speaker,
        "status": status,
        "status_reason": status_reason,
        "status_history": [
            {
                "status": status,
                "at": now,
                "by": by_label,
                "reason": status_reason,
            },
        ],
        "session_date": proposal.session_date,
        "approved_at": now,
        "created_by_ingest": ingest_id,
        "claim_group_id": proposal.claim_group_id,
        "section": section,
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
    declined_aliases: set[tuple[str, str]] = field(default_factory=set)


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
    edits: MergedEdits | None,
    confirmed_aliases: list[str],
    by_label: str,
) -> bool:
    """Return True if a fact was appended (False if idempotent skip)."""
    path, slug, category = _resolve_entity_path(ctx, proposal)
    now = format_iso_now()
    fact = proposal_to_fact_dict(
        proposal,
        edits=edits,
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


def _build_target_view(ctx: _ApprovalContext, proposal: Proposal) -> TargetView:
    """Per-target slice of a `BundleView`; filters aliases via merged_aliases."""
    is_new = proposal.proposal_type == "new_entity_with_facts"
    category = _category_for_new(ctx.plan, proposal.target_entity) if is_new else None
    suggested = _suggested_aliases_for(ctx.plan, proposal)
    target_key = proposal.target_entity.casefold()
    suggested = tuple(
        a
        for a in suggested
        if (target_key, normalize_alias_name(a)) not in ctx.merged_aliases
        and (target_key, normalize_alias_name(a)) not in ctx.declined_aliases
    )
    matched_via = _matched_via_for(ctx.plan, proposal)
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
    return TargetView(
        proposal=proposal,
        is_new_entity=is_new,
        new_entity_category=category,
        created_earlier_in_session=created_earlier,
        suggested_aliases=suggested,
        matched_via=matched_via,
    )


def _build_bundle_view(
    ctx: _ApprovalContext,
    proposals: list[Proposal],
    *,
    bundle_index: int,
    bundle_total: int,
) -> BundleView:
    """Wrap claim-group `proposals` (plan order) into a `BundleView`."""
    targets = tuple(_build_target_view(ctx, p) for p in proposals)
    return BundleView(
        bundle_index=bundle_index,
        bundle_total=bundle_total,
        claim_group_id=proposals[0].claim_group_id,
        targets=targets,
        source_url=ctx.info.source_url,
        source_title=ctx.info.title,
    )


def _bundle_proposals(ordered: list[Proposal]) -> list[list[Proposal]]:
    """Group proposals into runs of consecutive matching `claim_group_id`.

    Input is already in plan order (siblings contiguous), so a single
    pass collecting consecutive runs preserves both within-bundle and
    cross-bundle order.
    """
    if not ordered:
        return []
    bundles: list[list[Proposal]] = [[ordered[0]]]
    for p in ordered[1:]:
        if p.claim_group_id == bundles[-1][0].claim_group_id:
            bundles[-1].append(p)
        else:
            bundles.append([p])
    return bundles


def _merge_edits(
    bundle_decision: ApproveDecision | BundleEdits,
    override: TargetEdits | None,
) -> MergedEdits | None:
    """Combine bundle-level and per-target edits into a single ``MergedEdits``.

    Returns None (plain-approve path) when result would be a no-op.
    Fields are disjoint: bundle owns text/status/status_reason;
    override owns section/speaker.
    """
    if isinstance(bundle_decision, ApproveDecision):
        if override is None or override.is_noop():
            return None
        return MergedEdits(
            new_section=override.new_section,
            new_speaker=override.new_speaker,
        )
    # BundleEdits branch
    has_bundle = not bundle_decision.is_noop()
    has_override = override is not None and not override.is_noop()
    if not has_bundle and not has_override:
        return None
    return MergedEdits(
        new_text=bundle_decision.new_text,
        new_status=bundle_decision.new_status,
        new_status_reason=bundle_decision.new_status_reason,
        new_section=override.new_section if override else None,
        new_speaker=override.new_speaker if override else None,
    )


def _count_remaining(source_id: str) -> int:
    """Count proposal files still on disk. Used for KI accounting."""
    proposals_dir = reading_pipeline.pending_proposals_dir(source_id)
    if not proposals_dir.is_dir():
        return 0
    return sum(1 for _ in proposals_dir.glob("*.yaml"))


def _seed_merged_aliases_from_disk(ctx: _ApprovalContext) -> None:
    """Seed `ctx.merged_aliases` with aliases this ingest already wrote.

    Without this, Ctrl-C resume re-prompts for aliases the user
    already confirmed earlier in the same ingest. Walks each entity
    referenced by the plan; cheap because plan size bounds the scan.
    """
    seen: set[str] = set()
    for claim in ctx.plan.planned_claims:
        for target in claim.targets:
            if target.entity in seen:
                continue
            seen.add(target.entity)
            entry = ctx.index.lookup(target.entity)
            if entry is None:
                continue
            path = ctx.cfg.wiki_repo_path / entry.category / f"{entry.slug}.yaml"
            if not path.exists():
                continue
            try:
                entity = entity_yaml_mod.read(path)
            except entity_yaml_mod.EntityError:
                continue
            target_key = target.entity.casefold()
            for alias in entity.aliases:
                if alias.added_by_ingest == ctx.source_id:
                    ctx.merged_aliases.add((
                        target_key,
                        normalize_alias_name(alias.name),
                    ))


def run(
    *,
    cfg: cfg_mod.Config,
    source_id: str,
    reviewer: Reviewer,
) -> ReviewResult:
    """Walk pending bundles; mutate entity YAMLs; return counts.

    Multi-target claims share one `claim_group_id` and surface as a
    single `BundleView`; one `decide_bundle` call covers every checked
    route. KeyboardInterrupt propagates after recording the count of
    proposal files left on disk as `remaining`.
    """
    wiki_repo = cfg.wiki_repo_path
    info_path = wiki_repo / "sources" / source_id / "info.yaml"
    info = info_yaml_mod.read(info_path)
    plan_path = reading_pipeline.pending_plan_path(source_id)
    if not plan_path.exists():
        msg = f"No plan at {plan_path}; run `approve-reading {source_id}` first."
        raise ReviewError(msg)
    plan = plan_yaml_mod.read(plan_path)
    _validate_proposals_subset_of_plan(plan, source_id)
    index = entity_index_mod.build(wiki_repo)
    ctx = _ApprovalContext(
        cfg=cfg, source_id=source_id, info=info, plan=plan, index=index
    )
    _seed_merged_aliases_from_disk(ctx)

    ordered = sorted_proposals(plan, source_id)
    if not ordered:
        return ReviewResult()
    bundles = _bundle_proposals(ordered)
    bundle_total = len(bundles)

    result = ReviewResult()
    for bundle_idx, bundle in enumerate(bundles, start=1):
        try:
            _process_bundle(
                ctx,
                bundle,
                reviewer=reviewer,
                bundle_index=bundle_idx,
                bundle_total=bundle_total,
                result=result,
            )
        except KeyboardInterrupt:
            result.remaining = _count_remaining(source_id)
            raise
    return result


def _process_bundle(
    ctx: _ApprovalContext,
    bundle: list[Proposal],
    *,
    reviewer: Reviewer,
    bundle_index: int,
    bundle_total: int,
    result: ReviewResult,
) -> None:
    """Drive one bundle: ask reviewer, fan out approvals / rejects."""
    view = _build_bundle_view(
        ctx, bundle, bundle_index=bundle_index, bundle_total=bundle_total
    )
    bundle_decision = reviewer.decide_bundle(view)

    if isinstance(bundle_decision.decision, RejectDecision):
        for proposal in bundle:
            proposal_path = reading_pipeline.pending_proposal_path(
                ctx.source_id, proposal.proposed_id
            )
            _reject(proposal_path)
            result.rejected += 1
        return

    selected = set(bundle_decision.selected_indices)
    # drop unselected routes first so they're gone before any writes
    for i, proposal in enumerate(bundle):
        if i in selected:
            continue
        proposal_path = reading_pipeline.pending_proposal_path(
            ctx.source_id, proposal.proposed_id
        )
        _reject(proposal_path)
        result.rejected += 1

    # approve checked routes in plan order so NEW-entity siblings land
    # before any sibling that depends on the entity existing.
    for i, proposal in enumerate(bundle):
        if i not in selected:
            continue
        # re-filter aliases against ctx.merged_aliases and ctx.declined_aliases —
        # earlier siblings in this bundle (or earlier bundles) may have already
        # settled the alias.
        target_key = proposal.target_entity.casefold()
        fresh_aliases = tuple(
            a
            for a in _suggested_aliases_for(ctx.plan, proposal)
            if (target_key, normalize_alias_name(a)) not in ctx.merged_aliases
            and (target_key, normalize_alias_name(a)) not in ctx.declined_aliases
        )
        confirmed: list[str] = []
        for alias in fresh_aliases:
            if reviewer.confirm_alias(proposal.target_entity, alias):
                confirmed.append(alias)
            else:
                ctx.declined_aliases.add((target_key, normalize_alias_name(alias)))

        edits = _merge_edits(
            bundle_decision.decision,
            bundle_decision.per_target_overrides.get(i),
        )
        proposal_path = reading_pipeline.pending_proposal_path(
            ctx.source_id, proposal.proposed_id
        )
        appended = _approve(
            ctx,
            proposal,
            proposal_path,
            edits=edits,
            confirmed_aliases=confirmed,
            by_label=reviewer.by_label,
        )
        if not appended:
            continue
        if edits is not None:
            result.edited += 1
        else:
            result.approved += 1
