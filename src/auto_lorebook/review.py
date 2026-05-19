"""Stage 4 (review) engine: walk bundles, approve into DB, write .md files.

Pure-logic module. No `input()` calls. Display + prompt I/O lives in
`commands/review.py` and is injected via the `Reviewer` protocol. Tests
script a `Reviewer` to drive the engine deterministically.

Walk order is authoritative from the plan: we iterate
`plan.planned_claims`, one `Proposal` per claim group. Each `Proposal`
has N `ProposalTarget` entries; all are surfaced as one `BundleView`.
The reviewer decides once per bundle and may drop individual targets
before approval via `BundleDecision.selected_indices`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from auto_lorebook import approval as approval_mod
from auto_lorebook import config as cfg_mod
from auto_lorebook import db as db_mod
from auto_lorebook import entities as entities_mod
from auto_lorebook import info_yaml as info_yaml_mod
from auto_lorebook import plan_yaml as plan_yaml_mod
from auto_lorebook import proposal_yaml as proposal_yaml_mod
from auto_lorebook import summary_regen as summary_regen_mod
from auto_lorebook.approval import ApprovalResult
from auto_lorebook.entities import slugify
from auto_lorebook.entity_yaml import normalize_alias_name
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from auto_lorebook.info_yaml import Info
    from auto_lorebook.plan_yaml import Plan
    from auto_lorebook.proposal_yaml import Proposal, ProposalTarget

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
    target: ProposalTarget
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


def _validate_proposals_subset_of_plan(conn: sqlite3.Connection, plan: Plan) -> None:
    """Raise ReviewError if any DB proposal target is not in plan keys.

    Missing keys are fine (subset allowed — covers Ctrl-C resume).
    Orphans (extra keys) indicate drift; user must replan.
    """
    proposals = proposal_yaml_mod.list_proposals(conn, plan.source_id)
    if not proposals:
        return
    plan_keys: set[tuple[str, str]] = {
        (claim.claim_group_id, target.entity)
        for claim in plan.planned_claims
        for target in claim.targets
    }
    orphans: list[str] = [
        f"  - proposal_id={p.proposed_id}"
        f" (claim_group_id={p.claim_group_id},"
        f" target_entity={t.entity})"
        for p in proposals
        for t in p.targets
        if (p.claim_group_id, t.entity) not in plan_keys
    ]
    if orphans:
        lines = "\n".join(orphans)
        msg = (
            f"Orphan proposals not in plan"
            f" (run `auto-lorebook replan {plan.source_id}` to recover):\n{lines}"
        )
        raise ReviewError(msg)


def sorted_proposals(conn: sqlite3.Connection, plan: Plan) -> list[Proposal]:
    """Walk plan in order; return proposals still in DB.

    One Proposal per claim group (in plan order).
    Precondition: DB proposals are a subset of plan keys
    (enforced by _validate_proposals_subset_of_plan before this runs).
    """
    all_proposals = proposal_yaml_mod.list_proposals(conn, plan.source_id)
    by_cg: dict[str, Proposal] = {p.claim_group_id: p for p in all_proposals}
    out: list[Proposal] = []
    for claim in plan.planned_claims:
        p = by_cg.pop(claim.claim_group_id, None)
        if p is not None:
            out.append(p)
    return out


def _suggested_aliases_for_target(
    plan: Plan, target: ProposalTarget
) -> tuple[str, ...]:
    """Union of planner-suggested aliases for this target's entity."""
    seen: dict[str, None] = {}  # ordered set
    if target.proposal_type == "new_entity_with_facts":
        for n in plan.new_entities:
            if n.name == target.entity:
                for alias in n.aliases_suggested:
                    seen[alias] = None
    for r in plan.entity_resolutions:
        if target.entity in {r.matched_entity, r.proposed_entity_name}:
            for alias in r.suggested_aliases_to_add:
                seen[alias] = None
    return tuple(seen.keys())


def _matched_via_for_target(plan: Plan, target: ProposalTarget) -> str | None:
    """Join `mention` strings from existing-entity resolutions; None if no match."""
    if target.proposal_type != "new_fact":
        return None
    mentions = [
        r.mention
        for r in plan.entity_resolutions
        if r.resolution == "existing"
        and r.matched_entity == target.entity
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
    target: ProposalTarget | None = None,
) -> dict[str, Any]:
    """Build the dict appended to `entity.facts`.

    ``edits=None`` means approve as-is. A non-None ``edits.new_text``
    triggers ``edited_by_human=True`` + ``text_source``; speaker /
    status / status_reason / section overrides change the recorded
    value (and ``status_history`` reflects the *final* status).
    ``target`` provides per-target section/speaker when present.
    """
    tgt = target or (proposal.targets[0] if proposal.targets else None)
    now = format_iso_now()
    edited_text = edits.new_text if edits else None
    edited_by_human = edited_text is not None
    text = edited_text if edited_text is not None else proposal.text
    speaker = (
        edits.new_speaker
        if edits and edits.new_speaker
        else (tgt.speaker if tgt else None)
    )
    status = edits.new_status if edits and edits.new_status else proposal.status
    status_reason = (
        edits.new_status_reason
        if edits and edits.new_status_reason is not None
        else proposal.status_reason
    )
    section = (
        edits.new_section
        if edits and edits.new_section
        else (tgt.section if tgt else "")
    )
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
    wiki_repo: Path
    source_id: str
    info: Info
    plan: Plan
    conn: sqlite3.Connection
    merged_aliases: set[tuple[str, str]] = field(default_factory=set)
    declined_aliases: set[tuple[str, str]] = field(default_factory=set)


def _resolve_target(
    ctx: _ApprovalContext,
    proposal: Proposal,
    target: ProposalTarget,
) -> tuple[str, str]:
    """Return (category, slug) for a target entity.

    For new entities: INSERT OR IGNORE into entities first.
    For existing entities: lookup via entities.lookup_by_planner_name.
    """
    now = format_iso_now()
    if target.proposal_type == "new_entity_with_facts":
        category = _category_for_new(ctx.plan, target.entity)
        if category is None:
            msg = (
                f"proposal {proposal.proposed_id}: target {target.entity!r} "
                f"is new but missing from plan.new_entities"
            )
            raise ReviewError(msg)
        slug = slugify(target.entity)
        # INSERT OR IGNORE — idempotent if stub already created this session
        ctx.conn.execute(
            """
            INSERT OR IGNORE INTO entities
                (category, slug, canonical_name,
                 superseded_by_category, superseded_by_slug,
                 created_at, created_by_ingest, updated_at)
            VALUES (?, ?, ?, NULL, NULL, ?, ?, ?)
            """,
            (category, slug, target.entity, now, ctx.source_id, now),
        )
        return category, slug

    entry = entities_mod.lookup_by_planner_name(ctx.conn, target.entity)
    if entry is None:
        msg = (
            f"proposal {proposal.proposed_id}: target {target.entity!r} "
            f"is marked existing but no entity matches in the wiki DB"
        )
        raise ReviewError(msg)
    return entry.category, entry.slug


def _reject(conn: sqlite3.Connection, proposal_id: str) -> None:
    proposal_yaml_mod.delete_proposal(conn, proposal_id)


# ------------- main loop -------------------------------------------------


def _build_target_view(
    ctx: _ApprovalContext,
    proposal: Proposal,
    target: ProposalTarget,
) -> TargetView:
    """Per-target slice of a `BundleView`; filters aliases via merged_aliases."""
    is_new = target.proposal_type == "new_entity_with_facts"
    category = _category_for_new(ctx.plan, target.entity) if is_new else None
    suggested = _suggested_aliases_for_target(ctx.plan, target)
    target_key = target.entity.casefold()
    suggested = tuple(
        a
        for a in suggested
        if (target_key, normalize_alias_name(a)) not in ctx.merged_aliases
        and (target_key, normalize_alias_name(a)) not in ctx.declined_aliases
    )
    matched_via = _matched_via_for_target(ctx.plan, target)
    created_earlier = False
    if is_new:
        existing_entry = entities_mod.lookup_by_planner_name(ctx.conn, target.entity)
        if existing_entry is not None:
            created_earlier = True
    return TargetView(
        proposal=proposal,
        target=target,
        is_new_entity=is_new,
        new_entity_category=category,
        created_earlier_in_session=created_earlier,
        suggested_aliases=suggested,
        matched_via=matched_via,
    )


def _build_bundle_view(
    ctx: _ApprovalContext,
    proposal: Proposal,
    *,
    bundle_index: int,
    bundle_total: int,
) -> BundleView:
    """Wrap one proposal's targets (plan order) into a `BundleView`."""
    targets = tuple(_build_target_view(ctx, proposal, t) for t in proposal.targets)
    return BundleView(
        bundle_index=bundle_index,
        bundle_total=bundle_total,
        claim_group_id=proposal.claim_group_id,
        targets=targets,
        source_url=ctx.info.source_url,
        source_title=ctx.info.title,
    )


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


def _count_remaining(conn: sqlite3.Connection, ingest_id: str) -> int:
    """Count proposals still in DB. Used for KI accounting."""
    return proposal_yaml_mod.count_proposals(conn, ingest_id)


def _seed_merged_aliases(ctx: _ApprovalContext) -> None:
    """Seed `ctx.merged_aliases` with aliases this ingest already wrote.

    Without this, Ctrl-C resume re-prompts for aliases the user
    already confirmed earlier in the same ingest.
    """
    seen_entities: set[str] = set()
    for claim in ctx.plan.planned_claims:
        for target in claim.targets:
            if target.entity in seen_entities:
                continue
            seen_entities.add(target.entity)
            entry = entities_mod.lookup_by_planner_name(ctx.conn, target.entity)
            if entry is None:
                continue
            target_key = target.entity.casefold()
            rows = ctx.conn.execute(
                """
                SELECT name FROM aliases
                WHERE entity_category=? AND entity_slug=? AND added_by_ingest=?
                """,
                (entry.category, entry.slug, ctx.source_id),
            ).fetchall()
            for row in rows:
                ctx.merged_aliases.add((target_key, normalize_alias_name(row["name"])))


def run(
    *,
    cfg: cfg_mod.Config,
    source_id: str,
    reviewer: Reviewer,
    wiki_override: str | None = None,
) -> ReviewResult:
    """Walk pending bundles; approve into DB; write .md files; return counts.

    Each claim group is one `Proposal` with N `ProposalTarget` entries;
    surfaced as a single `BundleView`; one `decide_bundle` call covers
    every target. approved/edited/rejected count proposals, not targets.
    KeyboardInterrupt propagates after recording the count of proposals
    left in DB as `remaining`.
    """
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    from auto_lorebook import wiki_state as wiki_state_mod  # noqa: PLC0415

    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        plan = plan_yaml_mod.read_plan_routes(conn, source_id)
        if plan is None:
            msg = f"No plan for {source_id!r}; run `approve-reading {source_id}` first."
            raise ReviewError(msg)
        _validate_proposals_subset_of_plan(conn, plan)
        info = info_yaml_mod.read(conn, source_id, wiki_repo=wiki_repo)
        # ensure entities backfilled from YAMLs (idempotent)
        entities_mod.list_entities(conn, wiki_repo=wiki_repo)
        ctx = _ApprovalContext(
            cfg=cfg,
            wiki_repo=wiki_repo,
            source_id=source_id,
            info=info,
            plan=plan,
            conn=conn,
        )
        _seed_merged_aliases(ctx)

        ordered = sorted_proposals(conn, plan)
        if not ordered:
            return ReviewResult()
        bundle_total = len(ordered)

        result = ReviewResult()
        for bundle_idx, proposal in enumerate(ordered, start=1):
            try:
                _process_bundle(
                    ctx,
                    proposal,
                    reviewer=reviewer,
                    bundle_index=bundle_idx,
                    bundle_total=bundle_total,
                    result=result,
                )
            except KeyboardInterrupt:
                result.remaining = _count_remaining(conn, source_id)
                raise
        return result
    finally:
        conn.close()


def _process_bundle(
    ctx: _ApprovalContext,
    proposal: Proposal,
    *,
    reviewer: Reviewer,
    bundle_index: int,
    bundle_total: int,
    result: ReviewResult,
) -> None:
    """Drive one bundle: ask reviewer, fan out approvals / rejects."""
    view = _build_bundle_view(
        ctx, proposal, bundle_index=bundle_index, bundle_total=bundle_total
    )
    bundle_decision = reviewer.decide_bundle(view)

    if isinstance(bundle_decision.decision, RejectDecision):
        _reject(ctx.conn, proposal.proposed_id)
        result.rejected += 1
        return

    selected = set(bundle_decision.selected_indices)
    targets = proposal.targets

    # approve selected targets, building resolved list
    targets_resolved: list[tuple[str, str, str]] = []
    confirmed_per_target: list[list[str]] = []
    edits_per_target: list[MergedEdits | None] = []

    for i, target in enumerate(targets):
        if i not in selected:
            continue
        # re-filter aliases against merged/declined
        target_key = target.entity.casefold()
        fresh_aliases = tuple(
            a
            for a in _suggested_aliases_for_target(ctx.plan, target)
            if (target_key, normalize_alias_name(a)) not in ctx.merged_aliases
            and (target_key, normalize_alias_name(a)) not in ctx.declined_aliases
        )
        confirmed: list[str] = []
        for alias in fresh_aliases:
            if reviewer.confirm_alias(target.entity, alias):
                confirmed.append(alias)
            else:
                ctx.declined_aliases.add((target_key, normalize_alias_name(alias)))

        edits = _merge_edits(
            bundle_decision.decision,
            bundle_decision.per_target_overrides.get(i),
        )

        # resolve entity and apply edits
        category, slug = _resolve_target(ctx, proposal, target)
        now = format_iso_now()
        for alias in confirmed:
            entities_mod.add_alias(
                ctx.conn,
                category=category,
                slug=slug,
                name=alias,
                ingest_id=ctx.source_id,
                source="stub-creation"
                if target.proposal_type == "new_entity_with_facts"
                else "alias-confirmation",
                when=now,
            )

        section = edits.new_section if edits and edits.new_section else target.section
        targets_resolved.append((category, slug, section))
        confirmed_per_target.append(confirmed)
        edits_per_target.append(edits)

        # record merged aliases
        for alias in confirmed:
            ctx.merged_aliases.add((target_key, normalize_alias_name(alias)))

    if not targets_resolved:
        # all targets dropped — treat as reject
        _reject(ctx.conn, proposal.proposed_id)
        result.rejected += 1
        return

    # determine edits for the proposal-level approval call
    # use the last non-None edits for bundle-level fields (text/status/status_reason)
    # the per-target section is handled via targets_resolved above
    bundle_edits: MergedEdits | None = None
    bd = bundle_decision.decision
    if isinstance(bd, BundleEdits) and not bd.is_noop():
        bundle_edits = MergedEdits(
            new_text=bd.new_text,
            new_status=bd.new_status,
            new_status_reason=bd.new_status_reason,
        )

    approval_result = approval_mod.approve_proposal(
        ctx.conn,
        proposal=proposal,
        targets_resolved=targets_resolved,
        by=reviewer.by_label,
        edited_text=bundle_edits.new_text if bundle_edits else None,
        edited_status=bundle_edits.new_status if bundle_edits else None,
        edited_status_reason=bundle_edits.new_status_reason if bundle_edits else None,
    )

    if approval_result == ApprovalResult.APPROVED:
        # regen .md per target only after the fact is committed
        for category, slug, _section in targets_resolved:
            try:
                summary_regen_mod.regenerate_entity(
                    ctx.conn, ctx.wiki_repo, category, slug
                )
            except ValueError:
                _logger.warning(
                    "review: regenerate_entity failed for %s/%s; .md not written",
                    category,
                    slug,
                )
        if bundle_edits is not None:
            result.edited += 1
        else:
            # check if any per-target edits were applied
            any_edits = any(e is not None for e in edits_per_target)
            if any_edits:
                result.edited += 1
            else:
                result.approved += 1
    # SKIPPED_IDEMPOTENT: no counter increment, no regen
