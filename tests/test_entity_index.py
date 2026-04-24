"""Tests for entity_index.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from pathlib import Path

from auto_lorebook.entity_index import build


def _write_entity(
    wiki: Path,
    category: str,
    slug: str,
    entity: str,
    aliases: list[str] | None = None,
    superseded_by: str | None = None,
) -> None:
    (wiki / category).mkdir(exist_ok=True)
    data = {
        "schema_version": 1,
        "entity": entity,
        "category": category,
        "slug": slug,
        "aliases": [{"name": a} for a in (aliases or [])],
        "superseded_by": superseded_by,
    }
    (wiki / category / f"{slug}.yaml").write_text(
        yaml.safe_dump(data), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def test_empty_wiki_returns_empty_index(tmp_wiki: Path) -> None:
    idx = build(tmp_wiki)
    assert idx.render_for_preamble() == "(no entities yet)"


def test_single_entity_no_aliases(tmp_wiki: Path) -> None:
    _write_entity(tmp_wiki, "locations", "aldara", "Aldara")
    idx = build(tmp_wiki)
    rendered = idx.render_for_preamble()
    assert "Locations:" in rendered
    assert "  - Aldara" in rendered


def test_entity_with_aliases(tmp_wiki: Path) -> None:
    _write_entity(
        tmp_wiki, "characters", "theron", "Theron", aliases=["King Theron", "Theron IV"]
    )
    idx = build(tmp_wiki)
    rendered = idx.render_for_preamble()
    assert "  - Theron (aliases: King Theron, Theron IV)" in rendered


def test_superseded_entity_excluded(tmp_wiki: Path) -> None:
    _write_entity(
        tmp_wiki, "characters", "old", "OldName", superseded_by="characters/new"
    )
    _write_entity(tmp_wiki, "characters", "new", "NewName")
    idx = build(tmp_wiki)
    rendered = idx.render_for_preamble()
    assert "OldName" not in rendered
    assert "NewName" in rendered


def test_multiple_categories_sorted(tmp_wiki: Path) -> None:
    _write_entity(tmp_wiki, "locations", "aldara", "Aldara")
    _write_entity(tmp_wiki, "characters", "theron", "Theron")
    rendered = build(tmp_wiki).render_for_preamble()
    char_pos = rendered.index("Characters:")
    loc_pos = rendered.index("Locations:")
    assert char_pos < loc_pos  # alphabetical


def test_entities_within_category_sorted(tmp_wiki: Path) -> None:
    _write_entity(tmp_wiki, "characters", "zeus", "Zeus")
    _write_entity(tmp_wiki, "characters", "aelindra", "Aelindra")
    rendered = build(tmp_wiki).render_for_preamble()
    aelindra_pos = rendered.index("Aelindra")
    zeus_pos = rendered.index("Zeus")
    assert aelindra_pos < zeus_pos


def test_missing_category_dir_skipped(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    idx = build(wiki)
    assert idx.render_for_preamble() == "(no entities yet)"


def test_aliases_sorted_in_render(tmp_wiki: Path) -> None:
    _write_entity(
        tmp_wiki,
        "locations",
        "aldara",
        "Aldara",
        aliases=["the Realm", "Kingdom of Aldara"],
    )
    rendered = build(tmp_wiki).render_for_preamble()
    assert "(aliases: Kingdom of Aldara, the Realm)" in rendered


def test_render_deterministic(tmp_wiki: Path) -> None:
    _write_entity(tmp_wiki, "characters", "a", "Alpha")
    _write_entity(tmp_wiki, "locations", "b", "Beta")
    r1 = build(tmp_wiki).render_for_preamble()
    r2 = build(tmp_wiki).render_for_preamble()
    assert r1 == r2
