"""Tests for the `wiki` subcommand group and `--wiki` override flag."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook import config as cfg_mod
from auto_lorebook.cli import create_parser
from auto_lorebook.commands import wiki_cmd
from auto_lorebook.commands._shared import resolve_wiki  # noqa: PLC2701
from auto_lorebook.config import ConfigError

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    return home


@pytest.fixture
def configured_wiki(tmp_wiki: Path, tmp_home: Path) -> Path:
    """tmp_wiki + config.yaml pointing at it as 'main'."""
    data = {
        "schema_version": 2,
        "active_wiki": "main",
        "wikis": [{"nickname": "main", "path": str(tmp_wiki)}],
    }
    (tmp_home / "config.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    return tmp_wiki


@pytest.fixture
def two_wiki_config(tmp_path: Path, tmp_home: Path) -> tuple[Path, Path]:
    """Two wiki dirs + config with both registered, 'main' active."""
    wiki1 = tmp_path / "wiki1"
    wiki1.mkdir()
    for cat in ("characters", "locations", "factions", "events", "items", "concepts"):
        (wiki1 / cat).mkdir()
    (wiki1 / ".wiki-context.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (wiki1 / ".transcription-corrections.yaml").write_text(
        "schema_version: 1\n", encoding="utf-8"
    )

    wiki2 = tmp_path / "wiki2"
    wiki2.mkdir()
    for cat in ("characters", "locations", "factions", "events", "items", "concepts"):
        (wiki2 / cat).mkdir()
    (wiki2 / ".wiki-context.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (wiki2 / ".transcription-corrections.yaml").write_text(
        "schema_version: 1\n", encoding="utf-8"
    )

    data = {
        "schema_version": 2,
        "active_wiki": "main",
        "wikis": [
            {"nickname": "main", "path": str(wiki1)},
            {"nickname": "alt", "path": str(wiki2)},
        ],
    }
    (tmp_home / "config.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    return wiki1, wiki2


def _ns(action: str, **kwargs: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "wiki_action": action,
        "nickname": None,
        "path": None,
        "old": None,
        "new": None,
        "name": None,
        "target": None,
        "wiki": None,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# Phase 2 — wiki list
# ---------------------------------------------------------------------------


class TestList:
    def test_lists_with_active_marker(
        self,
        two_wiki_config: tuple[Path, Path],  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = wiki_cmd.run(_ns("list"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "* main" in out
        assert "alt" in out
        # active marked, other not
        assert "* alt" not in out

    def test_single_wiki_shows_active(
        self,
        tmp_home: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        wiki = tmp_home / "mywiki"
        wiki.mkdir()
        data = {
            "schema_version": 2,
            "active_wiki": "sole",
            "wikis": [{"nickname": "sole", "path": str(wiki)}],
        }
        (tmp_home / "config.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )
        rc = wiki_cmd.run(_ns("list"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "* sole" in out


def test_argparse_routes_list() -> None:
    parser = create_parser()
    args = parser.parse_args(["wiki", "list"])
    assert args.wiki_action == "list"
    assert args.func is wiki_cmd.run


# ---------------------------------------------------------------------------
# Phase 3 — wiki add
# ---------------------------------------------------------------------------


class TestAdd:
    def test_argparse_routes_add(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["wiki", "add", "mynick", "/some/path"])
        assert args.wiki_action == "add"
        assert args.nickname == "mynick"
        assert args.path == "/some/path"

    def test_registers_without_switching(
        self,
        configured_wiki: Path,  # noqa: ARG002
        tmp_path: Path,
        tmp_home: Path,
    ) -> None:
        new_wiki = tmp_path / "newwiki"
        new_wiki.mkdir()
        rc = wiki_cmd.run(_ns("add", nickname="new", path=str(new_wiki)))
        assert rc == 0
        # active_wiki unchanged
        raw = yaml.safe_load((tmp_home / "config.yaml").read_text(encoding="utf-8"))
        assert raw["active_wiki"] == "main"
        assert any(w["nickname"] == "new" for w in raw["wikis"])

    def test_path_must_exist(
        self,
        configured_wiki: Path,  # noqa: ARG002
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = wiki_cmd.run(_ns("add", nickname="bad", path=str(tmp_path / "no-such")))
        assert rc == 1
        assert "does not exist" in capsys.readouterr().out

    def test_duplicate_nickname_errors(
        self,
        configured_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = wiki_cmd.run(_ns("add", nickname="main", path=str(configured_wiki)))
        assert rc == 1
        assert "already registered" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Phase 4 — wiki remove
# ---------------------------------------------------------------------------


class TestRemove:
    def test_argparse_routes_remove(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["wiki", "remove", "mynick"])
        assert args.wiki_action == "remove"
        assert args.nickname == "mynick"

    def test_removes_non_active(
        self,
        two_wiki_config: tuple[Path, Path],  # noqa: ARG002
        tmp_home: Path,
    ) -> None:
        rc = wiki_cmd.run(_ns("remove", nickname="alt"))
        assert rc == 0
        raw = yaml.safe_load((tmp_home / "config.yaml").read_text(encoding="utf-8"))
        assert all(w["nickname"] != "alt" for w in raw["wikis"])

    def test_refuses_active(
        self,
        configured_wiki: Path,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = wiki_cmd.run(_ns("remove", nickname="main"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "active" in out.lower()

    def test_unknown_nickname_errors(
        self,
        configured_wiki: Path,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = wiki_cmd.run(_ns("remove", nickname="nope"))
        assert rc == 1
        assert "nope" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Phase 5 — wiki rename
# ---------------------------------------------------------------------------


class TestRename:
    def test_argparse_routes_rename(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["wiki", "rename", "old", "new"])
        assert args.wiki_action == "rename"
        assert args.old == "old"
        assert args.new == "new"

    def test_renames_in_place(
        self,
        configured_wiki: Path,  # noqa: ARG002
        tmp_home: Path,
    ) -> None:
        rc = wiki_cmd.run(_ns("rename", old="main", new="renamed"))
        assert rc == 0
        raw = yaml.safe_load((tmp_home / "config.yaml").read_text(encoding="utf-8"))
        assert raw["active_wiki"] == "renamed"
        assert any(w["nickname"] == "renamed" for w in raw["wikis"])
        assert all(w["nickname"] != "main" for w in raw["wikis"])

    def test_rename_active_updates_pointer(
        self,
        two_wiki_config: tuple[Path, Path],  # noqa: ARG002
        tmp_home: Path,
    ) -> None:
        rc = wiki_cmd.run(_ns("rename", old="main", new="primary"))
        assert rc == 0
        raw = yaml.safe_load((tmp_home / "config.yaml").read_text(encoding="utf-8"))
        assert raw["active_wiki"] == "primary"

    def test_rename_collision_errors(
        self,
        two_wiki_config: tuple[Path, Path],  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = wiki_cmd.run(_ns("rename", old="main", new="alt"))
        assert rc == 1
        assert "already registered" in capsys.readouterr().out

    def test_rename_unknown_old_errors(
        self,
        configured_wiki: Path,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = wiki_cmd.run(_ns("rename", old="nope", new="other"))
        assert rc == 1
        assert "nope" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Phase 6 — wiki use
# ---------------------------------------------------------------------------


class TestUse:
    def test_argparse_routes_use(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["wiki", "use", "main"])
        assert args.wiki_action == "use"
        assert args.target == "main"

    def test_argparse_use_with_name_flag(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["wiki", "use", "/some/path", "--name", "mynick"])
        assert args.name == "mynick"

    def test_known_nickname_switches(
        self,
        two_wiki_config: tuple[Path, Path],  # noqa: ARG002
        tmp_home: Path,
    ) -> None:
        rc = wiki_cmd.run(_ns("use", target="alt"))
        assert rc == 0
        raw = yaml.safe_load((tmp_home / "config.yaml").read_text(encoding="utf-8"))
        assert raw["active_wiki"] == "alt"

    def test_known_nickname_does_not_rebootstrap(
        self,
        two_wiki_config: tuple[Path, Path],
        tmp_home: Path,  # noqa: ARG002
    ) -> None:
        """Switching to known nickname doesn't clobber existing wiki files."""
        wiki1, _ = two_wiki_config
        sentinel = wiki1 / "characters" / "existing.yaml"
        sentinel.write_text("schema_version: 1\n", encoding="utf-8")
        rc = wiki_cmd.run(_ns("use", target="main"))
        assert rc == 0
        # file untouched — no re-bootstrap
        assert sentinel.exists()

    def test_path_auto_registers_with_basename(
        self,
        configured_wiki: Path,  # noqa: ARG002
        tmp_path: Path,
        tmp_home: Path,
    ) -> None:
        new_wiki = tmp_path / "scifi"
        new_wiki.mkdir()
        rc = wiki_cmd.run(_ns("use", target=str(new_wiki)))
        assert rc == 0
        raw = yaml.safe_load((tmp_home / "config.yaml").read_text(encoding="utf-8"))
        assert raw["active_wiki"] == "scifi"
        assert any(w["nickname"] == "scifi" for w in raw["wikis"])
        # bootstrap ran
        assert (new_wiki / "characters").is_dir()
        assert (new_wiki / ".wiki-context.yaml").exists()

    def test_path_explicit_name_override(
        self,
        configured_wiki: Path,  # noqa: ARG002
        tmp_path: Path,
        tmp_home: Path,
    ) -> None:
        new_wiki = tmp_path / "mywiki"
        new_wiki.mkdir()
        rc = wiki_cmd.run(_ns("use", target=str(new_wiki), name="myname"))
        assert rc == 0
        raw = yaml.safe_load((tmp_home / "config.yaml").read_text(encoding="utf-8"))
        assert raw["active_wiki"] == "myname"

    def test_path_basename_collision_errors(
        self,
        configured_wiki: Path,  # noqa: ARG002
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # 'main' is already registered; a path whose basename is 'main' should error
        new_wiki = tmp_path / "main"
        new_wiki.mkdir()
        rc = wiki_cmd.run(_ns("use", target=str(new_wiki)))
        assert rc == 1
        out = capsys.readouterr().out
        assert "--name" in out

    def test_unknown_arg_errors(
        self,
        configured_wiki: Path,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = wiki_cmd.run(_ns("use", target="nope"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "nope" in out

    def test_use_initialises_db_for_new_wiki(
        self,
        configured_wiki: Path,  # noqa: ARG002
        tmp_path: Path,
    ) -> None:
        from auto_lorebook import wiki_state  # noqa: PLC0415

        new_wiki = tmp_path / "newwiki"
        new_wiki.mkdir()
        rc = wiki_cmd.run(_ns("use", target=str(new_wiki)))
        assert rc == 0
        assert wiki_state.wiki_db_path(new_wiki).exists()

    def test_use_initialises_db_for_known_nickname_without_db(
        self,
        two_wiki_config: tuple[Path, Path],
    ) -> None:
        from auto_lorebook import wiki_state  # noqa: PLC0415

        wiki1, _ = two_wiki_config
        db_path = wiki_state.wiki_db_path(wiki1)
        # ensure db doesn't exist yet
        if db_path.exists():
            db_path.unlink()
        rc = wiki_cmd.run(_ns("use", target="main"))
        assert rc == 0
        assert db_path.exists()


# ---------------------------------------------------------------------------
# Phase 7 — top-level --wiki flag
# ---------------------------------------------------------------------------


class TestWikiOverrideFlag:
    def test_parser_has_wiki_flag(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["--wiki", "home", "version"])
        assert args.wiki == "home"

    def test_parser_no_wiki_defaults_to_none(self) -> None:
        parser = create_parser()
        args = parser.parse_args(["version"])
        assert args.wiki is None

    def test_override_precedence(
        self,
        two_wiki_config: tuple[Path, Path],
    ) -> None:
        """resolve_wiki(cfg, args) returns the override path, not active."""
        cfg = cfg_mod.load_config()
        args = argparse.Namespace(wiki="alt")
        _, wiki2 = two_wiki_config
        resolved = resolve_wiki(cfg, args)
        assert resolved == wiki2

    def test_path_string_rejected(
        self,
        two_wiki_config: tuple[Path, Path],  # noqa: ARG002
    ) -> None:
        cfg = cfg_mod.load_config()
        args = argparse.Namespace(wiki="/some/path")
        with pytest.raises(ConfigError, match="nickname"):
            resolve_wiki(cfg, args)

    def test_tilde_path_rejected(
        self,
        two_wiki_config: tuple[Path, Path],  # noqa: ARG002
    ) -> None:
        cfg = cfg_mod.load_config()
        args = argparse.Namespace(wiki="~/wikis/foo")
        with pytest.raises(ConfigError, match="nickname"):
            resolve_wiki(cfg, args)

    def test_unknown_nickname_rejected(
        self,
        two_wiki_config: tuple[Path, Path],  # noqa: ARG002
    ) -> None:
        cfg = cfg_mod.load_config()
        args = argparse.Namespace(wiki="unknown")
        with pytest.raises(ConfigError, match="unknown"):
            resolve_wiki(cfg, args)

    def test_none_wiki_uses_active(
        self,
        two_wiki_config: tuple[Path, Path],
    ) -> None:
        cfg = cfg_mod.load_config()
        args = argparse.Namespace(wiki=None)
        wiki1, _ = two_wiki_config
        resolved = resolve_wiki(cfg, args)
        assert resolved == wiki1

    def test_does_not_mutate_registry(
        self,
        two_wiki_config: tuple[Path, Path],  # noqa: ARG002
        tmp_home: Path,
    ) -> None:
        cfg = cfg_mod.load_config()
        before = yaml.safe_load((tmp_home / "config.yaml").read_text(encoding="utf-8"))
        args = argparse.Namespace(wiki="alt")
        resolve_wiki(cfg, args)
        after = yaml.safe_load((tmp_home / "config.yaml").read_text(encoding="utf-8"))
        assert before == after


# ---------------------------------------------------------------------------
# Phase 8 — wiki rebuild
# ---------------------------------------------------------------------------


def _make_rebuild_ns(**kwargs: object) -> argparse.Namespace:
    base: dict[str, object] = {"wiki_action": "rebuild", "wiki": None}
    base.update(kwargs)
    return argparse.Namespace(**base)


def _seed_wiki_db(wiki_repo: Path) -> None:
    """Initialise wiki DB with two entities and stub facts."""
    from auto_lorebook import db, wiki_state  # noqa: PLC0415
    from auto_lorebook.facts import create_fact_with_target  # noqa: PLC0415

    conn = db.open(wiki_state.wiki_db_path(wiki_repo))
    try:
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
        conn.execute(
            "INSERT INTO entities(category, slug, canonical_name, created_at,"
            " created_by_ingest, updated_at)"
            " VALUES ('locations', 'aldara', 'Aldara',"
            " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
        )
        create_fact_with_target(
            conn,
            fact_id="f-001",
            text="Theron founded the city.",
            raw_transcript_span="raw",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:04:32",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            entity_category="characters",
            entity_slug="theron",
            section="biography",
            by="tester",
        )
        conn.commit()
    finally:
        conn.close()


class TestRebuild:
    def test_rebuild_subcommand_registered(self) -> None:
        """Parser accepts `wiki rebuild` and sets func."""
        parser = create_parser()
        args = parser.parse_args(["wiki", "rebuild"])
        assert args.wiki_action == "rebuild"
        assert args.func is wiki_cmd.run

    def test_rebuild_regenerates_all_pages(
        self,
        configured_wiki: Path,
        tmp_home: Path,  # noqa: ARG002
    ) -> None:
        """Rebuild regenerates .md for every entity."""
        from unittest.mock import MagicMock, patch  # noqa: PLC0415

        _seed_wiki_db(configured_wiki)

        mock_client = MagicMock()
        mock_client.complete.return_value = MagicMock(
            text='{"prose": "Some generated prose."}'
        )

        with (
            patch(
                "auto_lorebook.commands.wiki.OpenRouterClient", return_value=mock_client
            ),
            patch(
                "auto_lorebook.commands.wiki.cfg_mod.Config.get_api_key",
                return_value="sk-test",
            ),
        ):
            rc = wiki_cmd.run(_make_rebuild_ns())

        assert rc == 0
        assert (configured_wiki / "characters" / "theron.md").exists()

    def test_rebuild_removes_orphan_md(
        self,
        configured_wiki: Path,
        tmp_home: Path,  # noqa: ARG002
    ) -> None:
        """Orphan .md with no matching entity is deleted after rebuild."""
        from unittest.mock import MagicMock, patch  # noqa: PLC0415

        _seed_wiki_db(configured_wiki)

        # pre-create orphan: no matching entity in DB
        orphan = configured_wiki / "characters" / "ghost.md"
        orphan.write_text("# Ghost\n", encoding="utf-8")
        assert orphan.exists()

        mock_client = MagicMock()
        mock_client.complete.return_value = MagicMock(
            text='{"prose": "Some generated prose."}'
        )

        with (
            patch(
                "auto_lorebook.commands.wiki.OpenRouterClient", return_value=mock_client
            ),
            patch(
                "auto_lorebook.commands.wiki.cfg_mod.Config.get_api_key",
                return_value="sk-test",
            ),
        ):
            rc = wiki_cmd.run(_make_rebuild_ns())

        assert rc == 0
        assert not orphan.exists()
        assert (configured_wiki / "characters" / "theron.md").exists()

    def test_rebuild_recoverable_after_partial(
        self,
        configured_wiki: Path,
        tmp_home: Path,  # noqa: ARG002
    ) -> None:
        """Rebuild succeeds even with a stale/partial .md present."""
        from unittest.mock import MagicMock, patch  # noqa: PLC0415

        _seed_wiki_db(configured_wiki)

        # partial/stale .md exists with wrong content
        stale = configured_wiki / "characters" / "theron.md"
        stale.write_text("# PARTIAL\n", encoding="utf-8")

        mock_client = MagicMock()
        mock_client.complete.return_value = MagicMock(text='{"prose": "Fresh prose."}')

        with (
            patch(
                "auto_lorebook.commands.wiki.OpenRouterClient", return_value=mock_client
            ),
            patch(
                "auto_lorebook.commands.wiki.cfg_mod.Config.get_api_key",
                return_value="sk-test",
            ),
        ):
            rc = wiki_cmd.run(_make_rebuild_ns())

        assert rc == 0
        content = stale.read_text(encoding="utf-8")
        # page was regenerated, not left as stale
        assert "PARTIAL" not in content

    def test_rebuild_no_api_key_returns_1(
        self,
        configured_wiki: Path,
        tmp_home: Path,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Rebuild prints error and returns 1 when no API key."""
        from unittest.mock import patch  # noqa: PLC0415

        _seed_wiki_db(configured_wiki)

        with patch(
            "auto_lorebook.commands.wiki.cfg_mod.Config.get_api_key",
            return_value=None,
        ):
            rc = wiki_cmd.run(_make_rebuild_ns())

        assert rc == 1
        assert "api key" in capsys.readouterr().out.lower()

    def test_rebuild_no_active_wiki_returns_1(
        self,
        tmp_home: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Rebuild prints error and returns 1 when no active wiki is set."""
        # config has a wiki registered but active_wiki is null
        wiki_dir = tmp_path / "mywiki"
        wiki_dir.mkdir()
        data = {
            "schema_version": 2,
            "active_wiki": None,
            "wikis": [{"nickname": "other", "path": str(wiki_dir)}],
        }
        (tmp_home / "config.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
        )

        rc = wiki_cmd.run(_make_rebuild_ns())

        assert rc == 1
        assert "wiki" in capsys.readouterr().out.lower()
