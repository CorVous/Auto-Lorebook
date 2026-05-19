"""Tests for entities.py DB-backed store."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook import entities

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def test_normalize_strips_and_casefolds() -> None:
    assert entities.normalize_name("  Hello World  ") == "hello world"


def test_normalize_collapses_whitespace() -> None:
    assert entities.normalize_name("Hello   World") == "hello world"


def test_normalize_nfkc_combining_chars() -> None:
    # LATIN CAPITAL A + combining grave → normalized precomposed
    import unicodedata  # noqa: PLC0415

    raw = "À"  # A + combining grave
    expected = unicodedata.normalize("NFKC", raw).casefold().strip()
    assert entities.normalize_name(raw) == expected


def test_normalize_idempotent() -> None:
    name = "  Hello World  "
    once = entities.normalize_name(name)
    assert entities.normalize_name(once) == once


def test_normalize_empty() -> None:
    assert not entities.normalize_name("")
    assert not entities.normalize_name("   ")


def test_normalize_fullwidth_unicode() -> None:
    # fullwidth A-l-d-a-r-a: NFKC → "aldara"
    fullwidth = "Ａｌｄａｒａ"  # noqa: RUF001
    assert entities.normalize_name(fullwidth) == "aldara"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_and_get_entity(db_conn: sqlite3.Connection) -> None:
    row = entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="ingest-001",
        when="2026-01-01T00:00:00Z",
    )
    assert row.category == "characters"
    assert row.slug == "theron"
    assert row.canonical_name == "Theron"
    assert row.superseded_by_category is None
    assert row.superseded_by_slug is None
    assert row.created_by_ingest == "ingest-001"

    fetched = entities.get_entity(db_conn, "characters", "theron")
    assert fetched == row


def test_create_duplicate_pk_raises(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    with pytest.raises(entities.EntityError):
        entities.create_entity(
            db_conn,
            category="characters",
            slug="theron",
            canonical_name="Other",
            ingest_id="i",
        )


def test_create_bad_category_raises(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(entities.EntityError):
        entities.create_entity(
            db_conn,
            category="dragons",
            slug="smaug",
            canonical_name="Smaug",
            ingest_id="i",
        )


def test_get_missing_returns_none(db_conn: sqlite3.Connection) -> None:
    assert entities.get_entity(db_conn, "characters", "nobody") is None


def test_list_empty(db_conn: sqlite3.Connection) -> None:
    assert entities.list_entities(db_conn) == []


def test_list_sorted_by_category_then_name(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="locations",
        slug="aldara",
        canonical_name="Aldara",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="characters",
        slug="zeus",
        canonical_name="Zeus",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="characters",
        slug="aelindra",
        canonical_name="Aelindra",
        ingest_id="i",
    )
    rows = entities.list_entities(db_conn)
    cats = [r.category for r in rows]
    assert cats == sorted(cats)
    char_rows = [r for r in rows if r.category == "characters"]
    names = [r.canonical_name for r in char_rows]
    assert names == sorted(names, key=str.casefold)


def test_list_filter_by_category(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="locations",
        slug="aldara",
        canonical_name="Aldara",
        ingest_id="i",
    )
    rows = entities.list_entities(db_conn, "characters")
    assert all(r.category == "characters" for r in rows)
    assert len(rows) == 1


def test_list_excludes_superseded_by_default(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="old",
        canonical_name="OldName",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="characters",
        slug="new",
        canonical_name="NewName",
        ingest_id="i",
    )
    entities.supersede(
        db_conn,
        category="characters",
        slug="old",
        by_category="characters",
        by_slug="new",
    )
    rows = entities.list_entities(db_conn)
    assert all(r.slug != "old" for r in rows)


def test_list_includes_superseded_when_flag_set(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="old",
        canonical_name="OldName",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="characters",
        slug="new",
        canonical_name="NewName",
        ingest_id="i",
    )
    entities.supersede(
        db_conn,
        category="characters",
        slug="old",
        by_category="characters",
        by_slug="new",
    )
    rows = entities.list_entities(db_conn, include_superseded=True)
    slugs = {r.slug for r in rows}
    assert "old" in slugs


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------


def test_add_alias_basic(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    alias = entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="King Theron",
        ingest_id="i",
        source="hand-edited",
    )
    assert alias is not None
    assert alias.name == "King Theron"
    assert alias.name_normalized == entities.normalize_name("King Theron")


def test_add_alias_idempotent_same_entity(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="King Theron",
        ingest_id="i",
        source="hand-edited",
    )
    result = entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="King Theron",
        ingest_id="i",
        source="hand-edited",
    )
    assert result is None  # idempotent


def test_add_alias_dedup_casing(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="king theron",
        ingest_id="i",
        source="hand-edited",
    )
    result = entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="KING THERON",
        ingest_id="i",
        source="hand-edited",
    )
    assert result is None  # same normalized name on same entity


def test_add_alias_dedup_nfkc(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    # fullwidth and ASCII normalize to same thing
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="Aldara",
        ingest_id="i",
        source="hand-edited",
    )
    result = entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="Ａｌｄａｒａ",  # noqa: RUF001  # fullwidth A-l-d-a-r-a
        ingest_id="i",
        source="hand-edited",
    )
    assert result is None


def test_add_alias_cross_entity_collision_allowed(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="locations",
        slug="theron-realm",
        canonical_name="Theron Realm",
        ingest_id="i",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="Theron Realm",
        ingest_id="i",
        source="hand-edited",
    )
    result = entities.add_alias(
        db_conn,
        category="locations",
        slug="theron-realm",
        name="Theron Realm",
        ingest_id="i",
        source="hand-edited",
    )
    # cross-entity same normalized name is allowed
    assert result is not None


def test_add_alias_bad_source_raises(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    with pytest.raises(entities.EntityError):
        entities.add_alias(
            db_conn,
            category="characters",
            slug="theron",
            name="King Theron",
            ingest_id="i",
            source="not-a-valid-source",
        )


def test_remove_alias_present_returns_true(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="King Theron",
        ingest_id="i",
        source="hand-edited",
    )
    normalized = entities.normalize_name("King Theron")
    assert entities.remove_alias(db_conn, "characters", "theron", normalized) is True


def test_remove_alias_absent_returns_false(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    assert entities.remove_alias(db_conn, "characters", "theron", "nobody") is False


def test_list_aliases_sorted(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="Zeus",
        ingest_id="i",
        source="hand-edited",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="Aelindra",
        ingest_id="i",
        source="hand-edited",
    )
    aliases = entities.list_aliases(db_conn, "characters", "theron")
    normalized_names = [a.name_normalized for a in aliases]
    assert normalized_names == sorted(normalized_names)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def test_get_by_alias_normalized(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="King Theron",
        ingest_id="i",
        source="hand-edited",
    )
    found = entities.get_by_alias(db_conn, entities.normalize_name("King Theron"))
    assert found is not None
    assert found.slug == "theron"


def test_get_by_alias_missing_returns_none(db_conn: sqlite3.Connection) -> None:
    assert entities.get_by_alias(db_conn, "nobody") is None


def test_get_by_alias_ambiguous_no_category_returns_none(
    db_conn: sqlite3.Connection,
) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="aldara-c",
        canonical_name="Aldara",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="locations",
        slug="aldara-l",
        canonical_name="Aldara Place",
        ingest_id="i",
    )
    norm = entities.normalize_name("Aldara")
    entities.add_alias(
        db_conn,
        category="characters",
        slug="aldara-c",
        name="Aldara",
        ingest_id="i",
        source="hand-edited",
    )
    entities.add_alias(
        db_conn,
        category="locations",
        slug="aldara-l",
        name="Aldara",
        ingest_id="i",
        source="hand-edited",
    )
    assert entities.get_by_alias(db_conn, norm) is None  # ambiguous


def test_get_by_alias_with_category_disambiguates(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="aldara-c",
        canonical_name="Aldara",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="locations",
        slug="aldara-l",
        canonical_name="Aldara Place",
        ingest_id="i",
    )
    norm = entities.normalize_name("Aldara")
    entities.add_alias(
        db_conn,
        category="characters",
        slug="aldara-c",
        name="Aldara",
        ingest_id="i",
        source="hand-edited",
    )
    entities.add_alias(
        db_conn,
        category="locations",
        slug="aldara-l",
        name="Aldara",
        ingest_id="i",
        source="hand-edited",
    )
    found = entities.get_by_alias(db_conn, norm, category="characters")
    assert found is not None
    assert found.slug == "aldara-c"


def test_lookup_by_planner_name_canonical(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    found = entities.lookup_by_planner_name(db_conn, "Theron")
    assert found is not None
    assert found.slug == "theron"


def test_lookup_by_planner_name_alias_fallback(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="King Theron",
        ingest_id="i",
        source="hand-edited",
    )
    found = entities.lookup_by_planner_name(db_conn, "king theron")
    assert found is not None
    assert found.slug == "theron"


def test_lookup_by_planner_name_unknown_returns_none(
    db_conn: sqlite3.Connection,
) -> None:
    assert entities.lookup_by_planner_name(db_conn, "unknown entity") is None


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------


def test_rename_updates_canonical_name(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
        when="2026-01-01T00:00:00Z",
    )
    updated = entities.rename(
        db_conn, "characters", "theron", "Lord Theron", when="2026-06-01T00:00:00Z"
    )
    assert updated.canonical_name == "Lord Theron"
    assert updated.updated_at == "2026-06-01T00:00:00Z"


def test_rename_does_not_touch_slug(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    updated = entities.rename(db_conn, "characters", "theron", "Lord Theron")
    assert updated.slug == "theron"


def test_rename_does_not_touch_aliases(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="King Theron",
        ingest_id="i",
        source="hand-edited",
    )
    entities.rename(db_conn, "characters", "theron", "Lord Theron")
    aliases = entities.list_aliases(db_conn, "characters", "theron")
    assert any(a.name == "King Theron" for a in aliases)


def test_rename_missing_entity_raises(db_conn: sqlite3.Connection) -> None:
    with pytest.raises(entities.EntityNotFoundError):
        entities.rename(db_conn, "characters", "nobody", "Name")


# ---------------------------------------------------------------------------
# Supersession
# ---------------------------------------------------------------------------


def test_supersede_sets_columns(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="old",
        canonical_name="OldName",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="characters",
        slug="new",
        canonical_name="NewName",
        ingest_id="i",
    )
    updated = entities.supersede(
        db_conn,
        category="characters",
        slug="old",
        by_category="characters",
        by_slug="new",
    )
    assert updated.superseded_by_category == "characters"
    assert updated.superseded_by_slug == "new"


def test_supersede_self_reference_raises(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    with pytest.raises(entities.EntityError):
        entities.supersede(
            db_conn,
            category="characters",
            slug="theron",
            by_category="characters",
            by_slug="theron",
        )


def test_supersede_cycle_raises(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn, category="characters", slug="a", canonical_name="A", ingest_id="i"
    )
    entities.create_entity(
        db_conn, category="characters", slug="b", canonical_name="B", ingest_id="i"
    )
    entities.supersede(
        db_conn, category="characters", slug="a", by_category="characters", by_slug="b"
    )
    with pytest.raises(entities.EntityError):
        entities.supersede(
            db_conn,
            category="characters",
            slug="b",
            by_category="characters",
            by_slug="a",
        )


def test_resolve_no_supersession_returns_self(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    found = entities.resolve(db_conn, "characters", "theron")
    assert found.slug == "theron"


def test_resolve_one_hop(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="old",
        canonical_name="OldName",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="characters",
        slug="new",
        canonical_name="NewName",
        ingest_id="i",
    )
    entities.supersede(
        db_conn,
        category="characters",
        slug="old",
        by_category="characters",
        by_slug="new",
    )
    found = entities.resolve(db_conn, "characters", "old")
    assert found.slug == "new"


def test_resolve_multi_hop(db_conn: sqlite3.Connection) -> None:
    for s, name in [("a", "A"), ("b", "B"), ("c", "C")]:
        entities.create_entity(
            db_conn, category="characters", slug=s, canonical_name=name, ingest_id="i"
        )
    entities.supersede(
        db_conn, category="characters", slug="a", by_category="characters", by_slug="b"
    )
    entities.supersede(
        db_conn, category="characters", slug="b", by_category="characters", by_slug="c"
    )
    found = entities.resolve(db_conn, "characters", "a")
    assert found.slug == "c"


def test_resolve_cycle_past_max_hops_raises(db_conn: sqlite3.Connection) -> None:
    # bypass normal cycle detection by directly inserting a cycle via SQL

    for s, name in [("x", "X"), ("y", "Y")]:
        entities.create_entity(
            db_conn, category="characters", slug=s, canonical_name=name, ingest_id="i"
        )
    # manually set x→y, y→x bypassing the Python guard
    db_conn.execute(
        "UPDATE entities SET superseded_by_category='characters',"
        " superseded_by_slug='y' WHERE slug='x'"
    )
    db_conn.execute(
        "UPDATE entities SET superseded_by_category='characters',"
        " superseded_by_slug='x' WHERE slug='y'"
    )
    with pytest.raises(entities.EntityError, match="exceeded"):
        entities.resolve(db_conn, "characters", "x", max_hops=4)


def test_list_excludes_superseded_intermediates(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn, category="characters", slug="a", canonical_name="A", ingest_id="i"
    )
    entities.create_entity(
        db_conn, category="characters", slug="b", canonical_name="B", ingest_id="i"
    )
    entities.create_entity(
        db_conn, category="characters", slug="c", canonical_name="C", ingest_id="i"
    )
    entities.supersede(
        db_conn, category="characters", slug="a", by_category="characters", by_slug="b"
    )
    entities.supersede(
        db_conn, category="characters", slug="b", by_category="characters", by_slug="c"
    )
    rows = entities.list_entities(db_conn)
    slugs = {r.slug for r in rows}
    assert "a" not in slugs
    assert "b" not in slugs
    assert "c" in slugs


# ---------------------------------------------------------------------------
# Preamble rendering
# ---------------------------------------------------------------------------


def test_render_preamble_empty_db(db_conn: sqlite3.Connection) -> None:
    result = entities.render_for_preamble(db_conn)
    assert result == "(no entities yet)"


def test_render_preamble_groups_by_category(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="locations",
        slug="aldara",
        canonical_name="Aldara",
        ingest_id="i",
    )
    result = entities.render_for_preamble(db_conn)
    assert "Characters:" in result
    assert "Locations:" in result
    assert result.index("Characters:") < result.index("Locations:")


def test_render_preamble_sorts_within_category(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="zeus",
        canonical_name="Zeus",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="characters",
        slug="aelindra",
        canonical_name="Aelindra",
        ingest_id="i",
    )
    result = entities.render_for_preamble(db_conn)
    assert result.index("Aelindra") < result.index("Zeus")


def test_render_preamble_includes_aliases_sorted(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="the Realm",
        ingest_id="i",
        source="hand-edited",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="Kingdom of Theron",
        ingest_id="i",
        source="hand-edited",
    )
    result = entities.render_for_preamble(db_conn)
    assert "(aliases: Kingdom of Theron, the Realm)" in result


def test_render_preamble_excludes_superseded(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="old",
        canonical_name="OldName",
        ingest_id="i",
    )
    entities.create_entity(
        db_conn,
        category="characters",
        slug="new",
        canonical_name="NewName",
        ingest_id="i",
    )
    entities.supersede(
        db_conn,
        category="characters",
        slug="old",
        by_category="characters",
        by_slug="new",
    )
    result = entities.render_for_preamble(db_conn)
    assert "OldName" not in result
    assert "NewName" in result


# ---------------------------------------------------------------------------
# In-session visibility
# ---------------------------------------------------------------------------


def test_create_entity_immediately_listable(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    rows = entities.list_entities(db_conn)
    assert any(r.slug == "theron" for r in rows)


def test_add_alias_immediately_resolvable(db_conn: sqlite3.Connection) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="theron",
        canonical_name="Theron",
        ingest_id="i",
    )
    entities.add_alias(
        db_conn,
        category="characters",
        slug="theron",
        name="King Theron",
        ingest_id="i",
        source="hand-edited",
    )
    norm = entities.normalize_name("King Theron")
    found = entities.get_by_alias(db_conn, norm)
    assert found is not None


# ---------------------------------------------------------------------------
# Backfill (lazy YAML→DB on first list_entities call)
# ---------------------------------------------------------------------------


def _write_entity_yaml(
    wiki: Path,
    category: str,
    slug: str,
    name: str,
    aliases: list[str] | None = None,
    superseded_by: str | None = None,
) -> None:
    (wiki / category).mkdir(exist_ok=True)
    data: dict[str, object] = {
        "schema_version": 1,
        "entity": name,
        "category": category,
        "slug": slug,
        "aliases": [{"name": a, "source": "hand-edited"} for a in (aliases or [])],
        "superseded_by": superseded_by,
    }
    (wiki / category / f"{slug}.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def test_backfill_from_yaml_on_empty_db(
    db_conn: sqlite3.Connection, tmp_wiki: Path
) -> None:
    _write_entity_yaml(
        tmp_wiki, "characters", "theron", "Theron", aliases=["King Theron"]
    )
    rows = entities.list_entities(db_conn, wiki_repo=tmp_wiki)
    assert any(r.slug == "theron" for r in rows)


def test_backfill_idempotent(db_conn: sqlite3.Connection, tmp_wiki: Path) -> None:
    _write_entity_yaml(tmp_wiki, "characters", "theron", "Theron")
    entities.list_entities(db_conn, wiki_repo=tmp_wiki)
    # second call should not insert duplicates
    rows = entities.list_entities(db_conn, wiki_repo=tmp_wiki)
    theron_rows = [r for r in rows if r.slug == "theron"]
    assert len(theron_rows) == 1


def test_no_backfill_when_db_not_empty(
    db_conn: sqlite3.Connection, tmp_wiki: Path
) -> None:
    entities.create_entity(
        db_conn,
        category="characters",
        slug="existing",
        canonical_name="Existing",
        ingest_id="i",
    )
    # YAML has a different entity; if backfill fires, we'd see "theron"
    _write_entity_yaml(tmp_wiki, "characters", "theron", "Theron")
    rows = entities.list_entities(db_conn, wiki_repo=tmp_wiki)
    slugs = {r.slug for r in rows}
    assert "existing" in slugs
    assert "theron" not in slugs
