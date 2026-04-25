"""Tests for the `entities` subcommand group."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook.cli import create_parser
from auto_lorebook.commands import entities_cmd

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    return home


@pytest.fixture
def configured_wiki(tmp_wiki: Path, tmp_home: Path) -> Path:
    """tmp_wiki + a config.yaml pointing at it."""
    (tmp_home / "config.yaml").write_text(
        f"schema_version: 1\nwiki_repo_path: {tmp_wiki}\n",
        encoding="utf-8",
    )
    return tmp_wiki


def _write_entity(
    wiki: Path,
    *,
    category: str,
    slug: str,
    name: str,
    aliases: list[str] | None = None,
    created_by_ingest: str | None = None,
    superseded_by: str | None = None,
) -> None:
    data: dict[str, object] = {
        "schema_version": 1,
        "entity": name,
        "category": category,
        "slug": slug,
        "aliases": [{"name": a} for a in (aliases or [])],
        "superseded_by": superseded_by,
    }
    if created_by_ingest is not None:
        data["created_by_ingest"] = created_by_ingest
    (wiki / category / f"{slug}.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def _ns(action: str, **kwargs: object) -> argparse.Namespace:
    base = {
        "entities_action": action,
        "category": None,
        "created_by": None,
        "query": None,
        "name": None,
        "slug": None,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


class TestList:
    def test_empty_wiki(
        self,
        configured_wiki: Path,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = entities_cmd.run(_ns("list"))
        assert rc == 0
        assert "(no entities)" in capsys.readouterr().out

    def test_lists_entities(
        self,
        configured_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_entity(
            configured_wiki, category="characters", slug="theron", name="Theron"
        )
        _write_entity(
            configured_wiki, category="locations", slug="aldara", name="Aldara"
        )
        rc = entities_cmd.run(_ns("list"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "CATEGORY" in out
        assert "Theron" in out
        assert "Aldara" in out
        # category-then-name sort: characters/Theron before locations/Aldara
        assert out.index("Theron") < out.index("Aldara")

    def test_filter_by_category(
        self,
        configured_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_entity(
            configured_wiki, category="characters", slug="theron", name="Theron"
        )
        _write_entity(
            configured_wiki, category="locations", slug="aldara", name="Aldara"
        )
        rc = entities_cmd.run(_ns("list", category="characters"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Theron" in out
        assert "Aldara" not in out

    def test_filter_by_created_by(
        self,
        configured_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_entity(
            configured_wiki,
            category="characters",
            slug="alpha",
            name="Alpha",
            created_by_ingest="ingest-001",
        )
        _write_entity(
            configured_wiki,
            category="characters",
            slug="beta",
            name="Beta",
            created_by_ingest="ingest-002",
        )
        rc = entities_cmd.run(_ns("list", created_by="ingest-001"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Alpha" in out
        assert "Beta" not in out


class TestShow:
    def test_by_slug(
        self,
        configured_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_entity(
            configured_wiki,
            category="characters",
            slug="theron",
            name="Theron",
            aliases=["King Theron"],
        )
        rc = entities_cmd.run(_ns("show", query="theron"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "entity:    Theron" in out
        assert "King Theron" in out
        assert "no facts yet" in out

    def test_by_name_case_insensitive(
        self,
        configured_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_entity(
            configured_wiki, category="characters", slug="theron", name="Theron"
        )
        rc = entities_cmd.run(_ns("show", query="THERON"))
        assert rc == 0
        assert "Theron" in capsys.readouterr().out

    def test_by_alias(
        self,
        configured_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_entity(
            configured_wiki,
            category="locations",
            slug="aldara",
            name="Aldara",
            aliases=["Kingdom of Aldara"],
        )
        rc = entities_cmd.run(_ns("show", query="kingdom of aldara"))
        assert rc == 0
        assert "Aldara" in capsys.readouterr().out

    def test_miss(
        self,
        configured_wiki: Path,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = entities_cmd.run(_ns("show", query="nope"))
        assert rc == 1
        assert "No entity matching" in capsys.readouterr().out

    def test_ambiguous(
        self,
        configured_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_entity(
            configured_wiki,
            category="characters",
            slug="aldara-c",
            name="Aldara",
        )
        _write_entity(
            configured_wiki,
            category="locations",
            slug="aldara-l",
            name="Aldara",
        )
        rc = entities_cmd.run(_ns("show", query="Aldara"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "Multiple matches" in out
        assert "characters/aldara-c" in out
        assert "locations/aldara-l" in out


class TestNew:
    def test_default_slug(
        self,
        configured_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = entities_cmd.run(
            _ns("new", category="characters", name="King Theron the Brave")
        )
        assert rc == 0
        target = configured_wiki / "characters" / "king-theron-the-brave.yaml"
        assert target.exists()
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert data["entity"] == "King Theron the Brave"
        assert data["category"] == "characters"
        assert data["slug"] == "king-theron-the-brave"
        assert data["schema_version"] == 1
        assert data["created_at"]
        assert "created" in capsys.readouterr().out

    def test_explicit_slug(self, configured_wiki: Path) -> None:
        rc = entities_cmd.run(
            _ns("new", category="locations", name="Aldara", slug="my-aldara")
        )
        assert rc == 0
        assert (configured_wiki / "locations" / "my-aldara.yaml").exists()

    def test_collision_refused(
        self,
        configured_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_entity(
            configured_wiki, category="characters", slug="theron", name="Theron"
        )
        rc = entities_cmd.run(
            _ns("new", category="characters", name="Theron", slug="theron")
        )
        assert rc == 1
        assert "already exists" in capsys.readouterr().out

    def test_unslugifiable_name_rejected(
        self,
        configured_wiki: Path,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = entities_cmd.run(_ns("new", category="characters", name="!!!"))
        assert rc == 1
        assert "could not derive a slug" in capsys.readouterr().out


class TestRebuildIndex:
    def test_smoke(
        self,
        configured_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_entity(configured_wiki, category="characters", slug="x", name="X")
        rc = entities_cmd.run(_ns("rebuild-index"))
        assert rc == 0
        assert "no cache in use" in capsys.readouterr().out


def test_argparse_requires_subaction() -> None:
    parser = create_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["entities"])


def test_argparse_routes_list() -> None:
    parser = create_parser()
    args = parser.parse_args(["entities", "list", "--category", "characters"])
    assert args.entities_action == "list"
    assert args.category == "characters"
    assert args.func is entities_cmd.run
