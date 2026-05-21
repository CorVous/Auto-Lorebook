"""Tests for staleness_store: hash computation and DB round-trips."""

from __future__ import annotations

from typing import TYPE_CHECKING

from auto_lorebook import db
from auto_lorebook.entities import AliasRow, EntityRow
from auto_lorebook.facts import FactRow
from auto_lorebook.staleness_store import (
    compute_page_inputs_hash,
    get_page_hash,
    record_page_hash,
)

if TYPE_CHECKING:
    import sqlite3


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entity(category: str = "characters", slug: str = "theron") -> EntityRow:
    return EntityRow(
        category=category,
        slug=slug,
        canonical_name="Theron",
        superseded_by_category=None,
        superseded_by_slug=None,
        created_at="2026-01-01T00:00:00Z",
        created_by_ingest="ing-001",
        updated_at="2026-01-01T00:00:00Z",
    )


def _make_fact(
    fact_id: str = "f-001",
    text: str = "Theron founded the city.",
    status: str = "authoritative",
) -> FactRow:
    return FactRow(
        id=fact_id,
        text=text,
        raw_transcript_span="raw span",
        text_corrects_transcript=False,
        text_source=None,
        edited_by_human=False,
        edited_at=None,
        source_id="src-001",
        locator="0:04:32",
        speaker="DM",
        status=status,
        status_reason=None,
        session_date="2026-01-15",
        approved_at="2026-01-15T10:00:00Z",
        created_by_ingest="ing-001",
        claim_group_id="cg-001",
        corrections_applied=[],
        inputs_json=None,
    )


def _make_alias(name: str = "King Theron") -> AliasRow:
    return AliasRow(
        entity_category="characters",
        entity_slug="theron",
        name=name,
        name_normalized=name.lower(),
        added_by_ingest="ing-001",
        added_at="2026-01-01T00:00:00Z",
        source="stub-creation",
    )


def _base_hash(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "entity": _make_entity(),
        "aliases": [],
        "facts": [_make_fact()],
        "linked_facts": [],
        "entity_index": "Characters:\n  - Theron",
        "wiki_setting": "A fantasy world.",
        "model": "test/model",
        "model_params": {},
    }
    kwargs.update(overrides)
    return compute_page_inputs_hash(**kwargs)  # type: ignore[arg-type]


def _mem_conn() -> sqlite3.Connection:
    conn = db.open(":memory:")
    conn.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at, context_json)"
        " VALUES ('src-001', 'youtube', '2026-01-01T00:00:00Z', '{}')"
    )
    conn.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('ing-001', 'src-001', '2026-01-01T00:00:00Z', 'done')"
    )
    conn.execute(
        "INSERT INTO entities(category, slug, canonical_name, created_at,"
        " created_by_ingest, updated_at)"
        " VALUES ('characters', 'theron', 'Theron',"
        " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# compute_page_inputs_hash — determinism / order-independence
# ---------------------------------------------------------------------------


class TestComputePageInputsHash:
    def test_deterministic(self) -> None:
        h1 = _base_hash()
        h2 = _base_hash()
        assert h1 == h2

    def test_shuffled_facts_same_hash(self) -> None:
        f1 = _make_fact("f-001", "First fact.")
        f2 = _make_fact("f-002", "Second fact.")
        h1 = _base_hash(facts=[f1, f2])
        h2 = _base_hash(facts=[f2, f1])
        assert h1 == h2

    def test_shuffled_linked_entities_same_hash(self) -> None:
        le1 = _make_entity("locations", "aldara")
        le2 = _make_entity("factions", "guild")
        lf1 = _make_fact("f-n01", "Aldara founded.")
        lf2 = _make_fact("f-n02", "Guild formed.")
        h1 = _base_hash(linked_facts=[(le1, [lf1]), (le2, [lf2])])
        h2 = _base_hash(linked_facts=[(le2, [lf2]), (le1, [lf1])])
        assert h1 == h2

    def test_different_fact_text_different_hash(self) -> None:
        h1 = _base_hash(facts=[_make_fact(text="Original text.")])
        h2 = _base_hash(facts=[_make_fact(text="Changed text.")])
        assert h1 != h2

    def test_different_fact_status_different_hash(self) -> None:
        h1 = _base_hash(facts=[_make_fact(status="authoritative")])
        h2 = _base_hash(facts=[_make_fact(status="hearsay")])
        assert h1 != h2

    def test_different_entity_index_different_hash(self) -> None:
        h1 = _base_hash(entity_index="Characters:\n  - Theron")
        h2 = _base_hash(entity_index="Characters:\n  - Theron\n  - Aldara")
        assert h1 != h2

    def test_different_wiki_setting_different_hash(self) -> None:
        h1 = _base_hash(wiki_setting="A fantasy world.")
        h2 = _base_hash(wiki_setting="A sci-fi world.")
        assert h1 != h2

    def test_different_model_different_hash(self) -> None:
        h1 = _base_hash(model="model/a")
        h2 = _base_hash(model="model/b")
        assert h1 != h2

    def test_different_linked_facts_different_hash(self) -> None:
        le = _make_entity("locations", "aldara")
        h1 = _base_hash(linked_facts=[])
        h2 = _base_hash(linked_facts=[(le, [_make_fact("f-n01", "Linked fact.")])])
        assert h1 != h2

    def test_returns_hex_string(self) -> None:
        h = _base_hash()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# get_page_hash / record_page_hash — DB round-trips
# ---------------------------------------------------------------------------


class TestPageHashRoundTrip:
    def test_get_returns_none_for_unknown(self) -> None:
        conn = _mem_conn()
        result = get_page_hash(conn, "characters", "theron")
        conn.close()
        assert result is None

    def test_record_then_get(self) -> None:
        conn = _mem_conn()
        record_page_hash(conn, "characters", "theron", "abc123def456")
        conn.commit()
        result = get_page_hash(conn, "characters", "theron")
        conn.close()
        assert result == "abc123def456"

    def test_upsert_overwrites(self) -> None:
        conn = _mem_conn()
        record_page_hash(conn, "characters", "theron", "hash-v1")
        conn.commit()
        record_page_hash(conn, "characters", "theron", "hash-v2")
        conn.commit()
        result = get_page_hash(conn, "characters", "theron")
        conn.close()
        assert result == "hash-v2"

    def test_explicit_generated_at(self) -> None:
        conn = _mem_conn()
        ts = "2026-05-01T12:00:00Z"
        record_page_hash(conn, "characters", "theron", "somehash", generated_at=ts)
        conn.commit()
        row = conn.execute(
            "SELECT generated_at FROM entity_page_staleness"
            " WHERE category='characters' AND slug='theron'"
        ).fetchone()
        conn.close()
        assert row[0] == ts

    def test_default_generated_at_is_set(self) -> None:
        conn = _mem_conn()
        record_page_hash(conn, "characters", "theron", "somehash")
        conn.commit()
        row = conn.execute(
            "SELECT generated_at FROM entity_page_staleness"
            " WHERE category='characters' AND slug='theron'"
        ).fetchone()
        conn.close()
        assert row[0] is not None
        assert len(row[0]) > 0
