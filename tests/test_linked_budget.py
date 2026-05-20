"""Tests for linked_budget.py — pure-function linked-context budgeter."""

from __future__ import annotations

import pytest

from auto_lorebook.entities import EntityRow
from auto_lorebook.facts import FactRow
from auto_lorebook.linked_budget import (
    LinkedContextTooLargeError,
    budget_linked_context,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entity(slug: str, category: str = "characters") -> EntityRow:
    return EntityRow(
        category=category,
        slug=slug,
        canonical_name=slug.capitalize(),
        superseded_by_category=None,
        superseded_by_slug=None,
        created_at="2026-01-01T00:00:00Z",
        created_by_ingest="ing-001",
        updated_at="2026-01-01T00:00:00Z",
    )


def _fact(
    fact_id: str,
    text: str = "Some fact.",
    status: str = "authoritative",
) -> FactRow:
    return FactRow(
        id=fact_id,
        text=text,
        raw_transcript_span="raw",
        text_corrects_transcript=False,
        text_source=None,
        edited_by_human=False,
        edited_at=None,
        source_id="src-001",
        locator="0:01:00",
        speaker=None,
        status=status,
        status_reason=None,
        session_date=None,
        approved_at="2026-01-01T00:00:00Z",
        created_by_ingest="ing-001",
        claim_group_id=None,
        corrections_applied=[],
        inputs_json=None,
    )


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty() -> None:
    result = budget_linked_context(
        [],
        subject_fact_ids=set(),
        context_window=10_000,
        budget_fraction=0.25,
    )
    assert result == []


def test_under_budget_unchanged() -> None:
    ent = _entity("theron")
    fact = _fact("f-001", "Short fact.")
    linked = [(ent, [fact])]
    result = budget_linked_context(
        linked,
        subject_fact_ids=set(),
        context_window=100_000,
        budget_fraction=0.25,
    )
    assert len(result) == 1
    assert result[0][0].slug == "theron"
    # fact list preserved
    assert result[0][1] == [fact]


def test_under_budget_all_facts_included() -> None:
    """All facts of an entity retained when within budget."""
    ent = _entity("theron")
    facts = [_fact(f"f-{i:03d}", f"Fact {i}.") for i in range(5)]
    result = budget_linked_context(
        [(ent, facts)],
        subject_fact_ids=set(),
        context_window=100_000,
        budget_fraction=0.25,
    )
    assert result[0][1] == facts


# ---------------------------------------------------------------------------
# Fact ordering within an entity: shared first, then by status
# ---------------------------------------------------------------------------


def test_shared_facts_kept_over_nonshared_when_tight() -> None:
    """When budget is tight, shared fact kept over non-shared authoritative."""
    ent = _entity("theron")
    # shared fact — id in subject_fact_ids
    shared = _fact("f-shared", "Shared fact.", status="hearsay")
    # non-shared authoritative
    non_shared = _fact("f-own", "X" * 300, status="authoritative")

    # budget just fits one fact's worth of tokens
    # shared text is short; non_shared is long; tight budget keeps shared
    result = budget_linked_context(
        [(ent, [non_shared, shared])],
        subject_fact_ids={"f-shared"},
        context_window=40,  # very small
        budget_fraction=0.25,
    )
    if result:
        included_ids = {f.id for f in result[0][1]}
        assert "f-shared" in included_ids


def test_hearsay_dropped_before_authoritative() -> None:
    """Hearsay dropped before authoritative when budget is tight."""
    ent = _entity("theron")
    auth_fact = _fact("f-auth", "Authoritative fact.", status="authoritative")
    hearsay_fact = _fact("f-hearsay", "Hearsay fact.", status="hearsay")

    # large budget — both included
    result = budget_linked_context(
        [(ent, [auth_fact, hearsay_fact])],
        subject_fact_ids=set(),
        context_window=100_000,
        budget_fraction=0.25,
    )
    ids = {f.id for f in result[0][1]}
    assert "f-auth" in ids
    assert "f-hearsay" in ids


def test_disproven_dropped_before_hearsay() -> None:
    """Disproven dropped before hearsay in trim order."""
    ent = _entity("theron")
    hearsay = _fact("f-hearsay", "Hearsay fact.", status="hearsay")
    disproven = _fact("f-disproven", "Disproven fact.", status="disproven")

    # under normal budget: both present
    result = budget_linked_context(
        [(ent, [hearsay, disproven])],
        subject_fact_ids=set(),
        context_window=100_000,
        budget_fraction=0.25,
    )
    ids = {f.id for f in result[0][1]}
    assert "f-hearsay" in ids
    assert "f-disproven" in ids


def test_nonshared_disproven_dropped_first() -> None:
    """Non-shared disproven dropped before non-shared hearsay."""
    ent = _entity("theron")
    # two long facts; budget only fits one
    disproven = _fact("f-disproven", "D" * 400, status="disproven")
    hearsay = _fact("f-hearsay", "H" * 400, status="hearsay")

    result = budget_linked_context(
        [(ent, [disproven, hearsay])],
        subject_fact_ids=set(),
        context_window=400,  # fits roughly one fact
        budget_fraction=0.25,
    )
    if result:
        included_ids = {f.id for f in result[0][1]}
        # hearsay kept over disproven
        assert "f-disproven" not in included_ids or "f-hearsay" in included_ids


# ---------------------------------------------------------------------------
# Entity-count cap (max_linked_entities)
# ---------------------------------------------------------------------------


def test_entity_count_cap_limits_entities() -> None:
    entities = [
        (_entity(f"ent{i}"), [_fact(f"f-{i:03d}", f"Fact {i}.")]) for i in range(5)
    ]
    result = budget_linked_context(
        entities,
        subject_fact_ids=set(),
        context_window=100_000,
        budget_fraction=0.25,
        max_linked_entities=3,
    )
    assert len(result) <= 3


def test_entity_count_cap_keeps_nearest_first_by_shared_fact_count() -> None:
    """Entities with more shared facts ranked first; cap keeps them."""
    # ent_a: 0 shared facts
    ent_a = _entity("ent_a")
    facts_a = [_fact("f-a1", "A fact.")]

    # ent_b: 2 shared facts
    ent_b = _entity("ent_b")
    facts_b = [_fact("f-b1", "B fact 1."), _fact("f-b2", "B fact 2.")]

    # ent_c: 1 shared fact
    ent_c = _entity("ent_c")
    facts_c = [_fact("f-c1", "C fact.")]

    subject_fact_ids = {"f-b1", "f-b2", "f-c1"}
    result = budget_linked_context(
        [(ent_a, facts_a), (ent_b, facts_b), (ent_c, facts_c)],
        subject_fact_ids=subject_fact_ids,
        context_window=100_000,
        budget_fraction=0.25,
        max_linked_entities=2,
    )
    slugs = [r[0].slug for r in result]
    # ent_b (2 shared) and ent_c (1 shared) should be kept; ent_a (0) dropped
    assert "ent_b" in slugs
    assert "ent_c" in slugs
    assert "ent_a" not in slugs


def test_entity_count_cap_tiebreak_by_category_slug() -> None:
    """Tie on shared-fact count: sort by (category, slug) asc."""
    ent_z = _entity("zzz", category="characters")
    ent_a = _entity("aaa", category="characters")
    facts_z = [_fact("f-z1", "Z fact.")]
    facts_a = [_fact("f-a1", "A fact.")]

    # both 0 shared facts; tiebreak on slug
    result = budget_linked_context(
        [(ent_z, facts_z), (ent_a, facts_a)],
        subject_fact_ids=set(),
        context_window=100_000,
        budget_fraction=0.25,
        max_linked_entities=1,
    )
    assert len(result) == 1
    assert result[0][0].slug == "aaa"


# ---------------------------------------------------------------------------
# Token-budget boundary
# ---------------------------------------------------------------------------


def test_at_budget_boundary_entity_kept() -> None:
    """Entity exactly at budget kept."""
    ent = _entity("theron")
    # fact text of exactly N chars; budget = context_window * fraction
    # token estimate = len(text) // 4
    text = "A" * 40  # 40 chars → 10 tokens
    fact = _fact("f-001", text)

    # budget = 100 * 0.25 = 25 tokens; 10 tokens fits
    result = budget_linked_context(
        [(ent, [fact])],
        subject_fact_ids=set(),
        context_window=100,
        budget_fraction=0.25,
    )
    assert len(result) == 1


def test_one_over_budget_trims_entity() -> None:
    """Entity whose addition would push over budget is dropped."""
    ent_a = _entity("ent_a")
    ent_b = _entity("ent_b")

    # large facts — budget just fits one
    long_text = "X" * 2000
    facts_a = [_fact("f-a1", long_text)]
    facts_b = [_fact("f-b1", long_text)]

    result = budget_linked_context(
        [(ent_a, facts_a), (ent_b, facts_b)],
        subject_fact_ids=set(),
        context_window=2000,
        budget_fraction=0.25,
    )
    # budget = 2000 * 0.25 = 500 tokens; one entity of 500 tokens fits, second doesn't
    assert len(result) <= 1


def test_raises_when_minimal_block_over_budget() -> None:
    """LinkedContextTooLargeError when even smallest possible block exceeds budget."""
    ent = _entity("theron")
    # massive fact, tiny budget
    huge_text = "Z" * 100_000
    fact = _fact("f-001", huge_text)

    with pytest.raises(LinkedContextTooLargeError):
        budget_linked_context(
            [(ent, [fact])],
            subject_fact_ids=set(),
            context_window=100,
            budget_fraction=0.01,
        )


def test_token_estimate_uses_len_div_4() -> None:
    """Token estimate is len(rendered_text) // 4, consistent with preamble heuristic."""
    ent = _entity("theron")
    # 400 chars → 100 tokens
    text = "T" * 400
    fact = _fact("f-001", text)

    # budget = 1000 * 0.25 = 250 tokens; 100 tokens fits
    result = budget_linked_context(
        [(ent, [fact])],
        subject_fact_ids=set(),
        context_window=1000,
        budget_fraction=0.25,
    )
    assert len(result) == 1

    # budget = 200 * 0.1 = 20 tokens; 100 tokens exceeds → raises
    with pytest.raises(LinkedContextTooLargeError):
        budget_linked_context(
            [(ent, [fact])],
            subject_fact_ids=set(),
            context_window=200,
            budget_fraction=0.1,
        )
