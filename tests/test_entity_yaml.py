"""Tests for entity_yaml.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook.entity_yaml import (
    Alias,
    Entity,
    EntityError,
    normalize_alias_name,
    read,
    slugify,
    write,
)
from auto_lorebook.schema import SchemaVersionError

if TYPE_CHECKING:
    from pathlib import Path


def _write_yaml(path: Path, data: dict[str, object]) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _minimal_dict(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": 1,
        "entity": "Aldara",
        "category": "locations",
        "slug": "aldara",
    }
    base.update(overrides)
    return base


def test_read_missing_file(tmp_path: Path) -> None:
    with pytest.raises(EntityError, match="file not found"):
        read(tmp_path / "nope.yaml")


def test_read_non_mapping_root(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    p.write_text("- just a list\n", encoding="utf-8")
    with pytest.raises(EntityError, match="expected a YAML mapping"):
        read(p)


def test_read_missing_schema_version(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    _write_yaml(p, {"entity": "X", "category": "characters", "slug": "x"})
    with pytest.raises(EntityError, match="schema_version"):
        read(p)


def test_read_future_schema_version(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    _write_yaml(p, _minimal_dict(schema_version=99))
    with pytest.raises(EntityError):
        read(p)


def test_read_missing_required_field(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    _write_yaml(p, {"schema_version": 1, "entity": "X", "category": "characters"})
    with pytest.raises(EntityError, match="slug"):
        read(p)


def test_read_bad_category(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    _write_yaml(p, _minimal_dict(category="weapons"))
    with pytest.raises(EntityError, match="category"):
        read(p)


def test_read_bad_aliases_type(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    _write_yaml(p, _minimal_dict(aliases="not a list"))
    with pytest.raises(EntityError, match="aliases"):
        read(p)


def test_read_minimal_entity(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    _write_yaml(p, _minimal_dict())
    e = read(p)
    assert e.entity == "Aldara"
    assert e.category == "locations"
    assert e.slug == "aldara"
    assert e.aliases == []
    assert e.facts == []
    assert e.extra == {}


def test_read_full_entity(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    _write_yaml(
        p,
        _minimal_dict(
            aliases=[
                {
                    "name": "Kingdom of Aldara",
                    "added_by_ingest": "ingest-001",
                    "added_at": "2026-01-16T14:32:11Z",
                    "source": "hand-edited",
                },
            ],
            superseded_by=None,
            created_at="2026-01-16T14:32:11Z",
            created_by_ingest="ingest-001",
            updated_at="2026-02-03T19:14:55Z",
            facts=[{"id": "aldara-f001", "text": "founded in the Second Age"}],
        ),
    )
    e = read(p)
    assert len(e.aliases) == 1
    assert e.aliases[0].name == "Kingdom of Aldara"
    assert e.aliases[0].source == "hand-edited"
    assert e.created_by_ingest == "ingest-001"
    assert e.facts[0]["id"] == "aldara-f001"


def test_read_drops_malformed_alias(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    _write_yaml(
        p,
        _minimal_dict(
            aliases=[
                {"name": "valid"},
                "not-a-dict",
                {"missing_name": True},
                {"name": ""},
            ],
        ),
    )
    e = read(p)
    assert [a.name for a in e.aliases] == ["valid"]


def test_read_dedups_aliases_keeping_earliest(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    _write_yaml(
        p,
        _minimal_dict(
            aliases=[
                {"name": "the Realm", "added_by_ingest": "ingest-A"},
                {"name": "  the realm ", "added_by_ingest": "ingest-B"},
                {"name": "THE REALM", "added_by_ingest": "ingest-C"},
            ],
        ),
    )
    e = read(p)
    assert len(e.aliases) == 1
    assert e.aliases[0].name == "the Realm"
    assert e.aliases[0].added_by_ingest == "ingest-A"


def test_read_preserves_unknown_keys(tmp_path: Path) -> None:
    p = tmp_path / "e.yaml"
    _write_yaml(
        p,
        _minimal_dict(future_field={"foo": 1}, another_unknown=[1, 2, 3]),
    )
    e = read(p)
    assert e.extra == {"future_field": {"foo": 1}, "another_unknown": [1, 2, 3]}


def test_write_round_trip(tmp_path: Path) -> None:
    src = Entity(
        entity="Theron",
        category="characters",
        slug="theron",
        aliases=[Alias(name="King Theron", source="hand-edited")],
        created_at="2026-01-16T14:32:11Z",
    )
    p = tmp_path / "theron.yaml"
    write(src, p)
    got = read(p)
    assert got.entity == "Theron"
    assert got.aliases[0].name == "King Theron"


def test_write_schema_version_first(tmp_path: Path) -> None:
    e = Entity(entity="X", category="characters", slug="x")
    p = tmp_path / "x.yaml"
    write(e, p)
    text = p.read_text(encoding="utf-8")
    first_line = text.splitlines()[0]
    assert first_line.startswith("schema_version:")


def test_write_preserves_unknown_keys(tmp_path: Path) -> None:
    e = Entity(
        entity="X",
        category="characters",
        slug="x",
        extra={"future_facts_v2": [{"a": 1}]},
    )
    p = tmp_path / "x.yaml"
    write(e, p)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert raw["future_facts_v2"] == [{"a": 1}]
    # round-trip stays preserved
    got = read(p)
    assert got.extra == {"future_facts_v2": [{"a": 1}]}


def test_write_dedups_aliases(tmp_path: Path) -> None:
    e = Entity(
        entity="X",
        category="characters",
        slug="x",
        aliases=[Alias(name="Foo"), Alias(name="foo"), Alias(name="FOO")],
    )
    p = tmp_path / "x.yaml"
    write(e, p)
    got = read(p)
    assert len(got.aliases) == 1


def test_write_atomic(tmp_path: Path) -> None:
    e = Entity(entity="X", category="characters", slug="x")
    nested = tmp_path / "deep" / "deeper"
    p = nested / "x.yaml"
    write(e, p)  # should create parents
    assert p.exists()


def test_normalize_alias_name_casefold_and_strip() -> None:
    assert normalize_alias_name("  The Realm ") == "the realm"
    assert normalize_alias_name("THE REALM") == "the realm"


def test_schema_version_error_subclass() -> None:
    # SchemaVersionError underlies our errors; this is just a sanity check
    # that it's importable from where we expect.
    assert issubclass(SchemaVersionError, ValueError)


def test_slugify_basic() -> None:
    assert slugify("Aldara") == "aldara"
    assert slugify("War of the Dusk") == "war-of-the-dusk"


def test_slugify_punctuation_collapsed() -> None:
    assert slugify("Théron's Sword!") == "th-ron-s-sword"
    assert slugify("--Multi  Spaces--") == "multi-spaces"


def test_slugify_empty_or_pure_punctuation() -> None:
    assert not slugify("")
    assert not slugify("   ")
    assert not slugify("???")


def test_slugify_idempotent() -> None:
    once = slugify("The Second Age")
    assert slugify(once) == once
