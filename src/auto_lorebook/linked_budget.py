"""Linked-context token budgeter for Stage 4."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auto_lorebook.entities import EntityRow
    from auto_lorebook.facts import FactRow

_logger = logging.getLogger(__name__)

LinkedContext = list[tuple["EntityRow", list["FactRow"]]]

# priority order: shared > non-shared authoritative > non-shared trustworthy;
# drop non-shared disproven before hearsay (both non-shared)
_STATUS_PRIORITY: dict[str, int] = {
    "authoritative": 1,
    "trustworthy": 2,
    "hearsay": 3,
    "disproven": 4,
}


class LinkedContextTooLargeError(RuntimeError):
    """Linked context exceeds the configured token budget even after trimming."""


def _shared_fact_count(facts: list[FactRow], subject_fact_ids: set[str]) -> int:
    """Count facts whose id is in subject_fact_ids."""
    return sum(1 for f in facts if f.id in subject_fact_ids)


def _rank_entities(
    linked_facts: LinkedContext,
    subject_fact_ids: set[str],
) -> LinkedContext:
    """Sort entities by shared-fact count desc, then (category, slug) asc."""
    return sorted(
        linked_facts,
        key=lambda item: (
            -_shared_fact_count(item[1], subject_fact_ids),
            item[0].category,
            item[0].slug,
        ),
    )


def _sort_facts(facts: list[FactRow], subject_fact_ids: set[str]) -> list[FactRow]:
    """Sort facts: shared first, then by status priority asc."""
    return sorted(
        facts,
        key=lambda f: (
            0 if f.id in subject_fact_ids else 1,
            _STATUS_PRIORITY.get(f.status, 3),
        ),
    )


def _render_for_estimate(entity: EntityRow, facts: list[FactRow]) -> str:
    """Render linked-entity block for token estimation.

    Must mirror the linked block rendering in stage4.build_prompt.
    """
    lines: list[str] = []
    name = entity.canonical_name
    cat_slug = f"{entity.category}/{entity.slug}"
    lines.append(f"\n{name} ({cat_slug}):")
    by_status: dict[str, list[FactRow]] = {}
    for fact in facts:
        by_status.setdefault(fact.status, []).append(fact)
    for status, bucket in sorted(by_status.items()):
        lines.append(f"  {status.upper()}:")
        for fact in bucket:
            reason = f" [reason: {fact.status_reason}]" if fact.status_reason else ""
            speaker = f" (speaker: {fact.speaker})" if fact.speaker else ""
            lines.append(f"    - [{fact.id}] {fact.text}{speaker}{reason}")
    return "\n".join(lines)


def _estimate_tokens(text: str) -> int:
    """Approximate token count; matches preamble.check_budget heuristic."""
    return len(text) // 4


def budget_linked_context(
    linked_facts: LinkedContext,
    subject_fact_ids: set[str],
    *,
    context_window: int,
    budget_fraction: float,
    max_linked_entities: int | None = None,
) -> LinkedContext:
    """Cap linked context to token budget.

    Priority: shared facts first, then non-shared by status (authoritative,
    trustworthy, hearsay, disproven). Entities ranked by shared-fact count
    desc, tie-break (category, slug) asc. Greedily includes entities until
    budget exhausted. Raises LinkedContextTooLargeError when no trimming
    can satisfy the budget.

    :param linked_facts: (entity, facts) pairs for linked entities
    :param subject_fact_ids: fact ids belonging to the subject entity
    :param context_window: model context window in tokens
    :param budget_fraction: fraction of context_window for linked block
    :param max_linked_entities: hard cap on entity count (None = unlimited)
    """
    if not linked_facts:
        return []

    budget = int(context_window * budget_fraction)

    # rank entities nearest-first
    ranked = _rank_entities(linked_facts, subject_fact_ids)

    # apply entity-count cap before token budgeting
    if max_linked_entities is not None:
        ranked = ranked[:max_linked_entities]

    # for each entity, sort facts by priority
    ordered: LinkedContext = [
        (ent, _sort_facts(facts, subject_fact_ids)) for ent, facts in ranked
    ]

    # greedy token budget: include entities in rank order until budget exceeded
    result: LinkedContext = []
    used = 0
    for ent, facts in ordered:
        block = _render_for_estimate(ent, facts)
        cost = _estimate_tokens(block)
        if used + cost <= budget:
            result.append((ent, facts))
            used += cost
            continue
        # entity over budget: fact-level trim — drop lowest-priority facts from tail
        trimmed = list(facts)
        while trimmed:
            trimmed.pop()  # drop lowest-priority fact (tail of sorted list)
            if not trimmed:
                break  # no facts left — skip entity entirely
            block = _render_for_estimate(ent, trimmed)
            cost = _estimate_tokens(block)
            if used + cost <= budget:
                result.append((ent, trimmed))
                used += cost
                break

    # hard check: if fact text alone exceeds budget, no trimming can help → raise
    if not result and ordered:
        first_ent, first_facts = ordered[0]
        if first_facts:
            # minimal unit = the fact with highest priority; check text tokens only
            min_fact = first_facts[0]
            if _estimate_tokens(min_fact.text) > budget:
                msg = (
                    f"Linked context for {first_ent.category}/{first_ent.slug} "
                    f"exceeds token budget ({budget}) even after trimming."
                )
                raise LinkedContextTooLargeError(msg)

    return result
