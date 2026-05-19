"""Tests for wiki_bootstrap idempotent skeleton creation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from auto_lorebook import wiki_bootstrap, wiki_state

if TYPE_CHECKING:
    from pathlib import Path


def test_first_run_creates_entity_dirs(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki_bootstrap.bootstrap(wiki)
    for sub in wiki_bootstrap.WIKI_SUBDIRS:
        assert (wiki / sub).is_dir(), f"missing {sub}/"


def test_first_run_creates_dotted_yaml_stubs(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki_bootstrap.bootstrap(wiki)
    for fname in wiki_bootstrap.DOTTED_YAML_STUBS:
        path = wiki / fname
        assert path.exists(), f"missing {fname}"
        assert path.read_text(encoding="utf-8") == wiki_bootstrap.STUB_BODY


def test_first_run_creates_wiki_state_dir(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki_bootstrap.bootstrap(wiki)
    assert wiki_state.wiki_state_dir(wiki).is_dir()


def test_first_run_writes_gitignore(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki_bootstrap.bootstrap(wiki)
    gi = wiki_state.gitignore_path(wiki)
    assert gi.exists()
    assert "pending/" in gi.read_text(encoding="utf-8")


def test_second_run_is_noop(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki_bootstrap.bootstrap(wiki)

    gi = wiki_state.gitignore_path(wiki)
    stub = wiki / ".wiki-context.yaml"
    mtime_gi = gi.stat().st_mtime
    mtime_stub = stub.stat().st_mtime
    content_gi = gi.read_text(encoding="utf-8")
    content_stub = stub.read_text(encoding="utf-8")

    wiki_bootstrap.bootstrap(wiki)

    assert gi.stat().st_mtime == mtime_gi
    assert stub.stat().st_mtime == mtime_stub
    assert gi.read_text(encoding="utf-8") == content_gi
    assert stub.read_text(encoding="utf-8") == content_stub


def test_preserves_existing_wiki_context_yaml(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    custom = "schema_version: 1\nsetting:\n  name: Aether\n"
    (wiki / ".wiki-context.yaml").write_text(custom, encoding="utf-8")

    wiki_bootstrap.bootstrap(wiki)

    assert (wiki / ".wiki-context.yaml").read_text(encoding="utf-8") == custom


def test_preserves_existing_gitignore(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    state_dir = wiki_state.wiki_state_dir(wiki)
    state_dir.mkdir(parents=True)
    gi = wiki_state.gitignore_path(wiki)
    custom = "*.pyc\npending/\n"
    gi.write_text(custom, encoding="utf-8")

    wiki_bootstrap.bootstrap(wiki)

    content = gi.read_text(encoding="utf-8")
    # original content preserved
    assert "*.pyc" in content
    assert "pending/" in content
    # wiki.db lines appended
    assert "wiki.db" in content


def test_first_run_creates_wiki_db(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki_bootstrap.bootstrap(wiki)
    assert wiki_state.wiki_db_path(wiki).exists()


def test_first_run_gitignore_excludes_wiki_db(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki_bootstrap.bootstrap(wiki)
    gi = wiki_state.gitignore_path(wiki)
    content = gi.read_text(encoding="utf-8")
    assert "wiki.db" in content
    assert "wiki.db-wal" in content
    assert "wiki.db-shm" in content


def test_existing_gitignore_gets_wiki_db_lines_appended(tmp_path: Path) -> None:
    """Gitignore without wiki.db token → appended; not rewritten."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    state_dir = wiki_state.wiki_state_dir(wiki)
    state_dir.mkdir(parents=True)
    gi = wiki_state.gitignore_path(wiki)
    original = "*.pyc\n"
    gi.write_text(original, encoding="utf-8")

    wiki_bootstrap.bootstrap(wiki)

    content = gi.read_text(encoding="utf-8")
    assert content.startswith(original)  # original content at top
    assert "wiki.db" in content


def test_existing_gitignore_with_wiki_db_not_duplicated(tmp_path: Path) -> None:
    """If wiki.db already in gitignore, don't duplicate."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    state_dir = wiki_state.wiki_state_dir(wiki)
    state_dir.mkdir(parents=True)
    gi = wiki_state.gitignore_path(wiki)
    original = "pending/\nwiki.db\n"
    gi.write_text(original, encoding="utf-8")

    wiki_bootstrap.bootstrap(wiki)

    content = gi.read_text(encoding="utf-8")
    assert content.count("wiki.db") == 1
