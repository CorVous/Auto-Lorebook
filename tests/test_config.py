"""Tests for config.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from auto_lorebook.config import (
    ConfigError,
    LastContext,
    MissingConfigError,
    interactive_setup,
    load_config,
    load_last_context,
    save_last_context,
)


def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_minimal(tmp_path: Path) -> None:
    wiki_path = str(tmp_path / "wiki")
    _write_config(
        tmp_path / "config.yaml",
        {
            "schema_version": 1,
            "wiki_repo_path": wiki_path,
            "openrouter": {"api_key_env": "OPENROUTER_API_KEY"},
            "models": {"primary": "openrouter/anthropic/claude-sonnet-4-5"},
        },
    )
    cfg = load_config(home=tmp_path)
    assert cfg.wiki_repo_path == Path(wiki_path)
    assert cfg.openrouter.api_key_env == "OPENROUTER_API_KEY"
    assert cfg.models.primary == "openrouter/anthropic/claude-sonnet-4-5"
    assert cfg.models.primary_context_window == 200_000
    assert cfg.preamble.budget_fraction == pytest.approx(0.8)


def test_load_config_with_all_fields(tmp_path: Path) -> None:
    wiki_path = str(tmp_path / "mywiki")
    _write_config(
        tmp_path / "config.yaml",
        {
            "schema_version": 1,
            "wiki_repo_path": wiki_path,
            "openrouter": {"api_key_env": "MY_KEY"},
            "models": {
                "primary": "openrouter/anthropic/claude-opus-4-7",
                "primary_context_window": 100_000,
                "extractor": "openrouter/anthropic/claude-haiku-4-5",
            },
            "preamble": {"budget_fraction": 0.6},
        },
    )
    cfg = load_config(home=tmp_path)
    assert cfg.models.primary_context_window == 100_000
    assert cfg.models.extractor == "openrouter/anthropic/claude-haiku-4-5"
    assert cfg.preamble.budget_fraction == pytest.approx(0.6)


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(MissingConfigError, match="not found"):
        load_config(home=tmp_path)


# ---------------------------------------------------------------------------
# interactive_setup
# ---------------------------------------------------------------------------


def test_interactive_setup_writes_config_and_skeleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    wiki = tmp_path / "wiki"
    answers = iter([str(wiki), "", ""])  # accept defaults for env + model

    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cfg = interactive_setup(home=home)

    assert (home / "config.yaml").exists()
    assert cfg.wiki_repo_path == wiki.resolve()
    assert cfg.openrouter.api_key_env == "OPENROUTER_API_KEY"
    assert cfg.models.primary == "openrouter/anthropic/claude-sonnet-4-5"
    # wiki skeleton created
    assert (wiki / "characters").is_dir()
    assert (wiki / "concepts").is_dir()
    assert (wiki / ".wiki-context.yaml").exists()
    assert (wiki / ".transcription-corrections.yaml").exists()


def test_interactive_setup_custom_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    wiki = tmp_path / "mywiki"
    answers = iter([str(wiki), "MY_KEY", "openrouter/anthropic/claude-opus-4-7"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cfg = interactive_setup(home=home)

    assert cfg.openrouter.api_key_env == "MY_KEY"
    assert cfg.models.primary == "openrouter/anthropic/claude-opus-4-7"


def test_interactive_setup_reprompts_on_blank_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    wiki = tmp_path / "wiki"
    # first answer blank, second valid; defaults accepted for the rest
    answers = iter(["", str(wiki), "", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    cfg = interactive_setup(home=home)
    assert cfg.wiki_repo_path == wiki.resolve()


def test_interactive_setup_preserves_existing_wiki_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    existing = wiki / ".wiki-context.yaml"
    existing.write_text(
        "schema_version: 1\nsetting:\n  name: Aether\n", encoding="utf-8"
    )

    answers = iter([str(wiki), "", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    interactive_setup(home=home)
    # existing content preserved
    assert "Aether" in existing.read_text(encoding="utf-8")


def test_load_config_missing_wiki_repo_raises(tmp_path: Path) -> None:
    _write_config(tmp_path / "config.yaml", {"schema_version": 1})
    with pytest.raises(ConfigError, match="wiki_repo_path"):
        load_config(home=tmp_path)


def test_load_config_bad_schema_version_raises(tmp_path: Path) -> None:
    wiki_path = str(tmp_path / "wiki")
    _write_config(
        tmp_path / "config.yaml",
        {"schema_version": 99, "wiki_repo_path": wiki_path},
    )
    with pytest.raises(ConfigError, match="exceeds max supported"):
        load_config(home=tmp_path)


def test_load_config_missing_schema_version_raises(tmp_path: Path) -> None:
    _write_config(tmp_path / "config.yaml", {"wiki_repo_path": str(tmp_path / "wiki")})
    with pytest.raises(ConfigError, match="missing schema_version"):
        load_config(home=tmp_path)


# ---------------------------------------------------------------------------
# last-context.yaml
# ---------------------------------------------------------------------------


def test_load_last_context_missing_returns_empty(tmp_path: Path) -> None:
    lc = load_last_context(home=tmp_path)
    assert lc.perspective is None
    assert lc.source_nature is None


def test_save_and_load_last_context(tmp_path: Path) -> None:
    lc = LastContext(perspective="Cor playing Kiki", source_nature="actual-play")
    save_last_context(lc, home=tmp_path)
    loaded = load_last_context(home=tmp_path)
    assert loaded.perspective == "Cor playing Kiki"
    assert loaded.source_nature == "actual-play"


def test_save_last_context_partial(tmp_path: Path) -> None:
    lc = LastContext(perspective="foo")
    save_last_context(lc, home=tmp_path)
    loaded = load_last_context(home=tmp_path)
    assert loaded.perspective == "foo"
    assert loaded.source_nature is None


def test_load_last_context_corrupt_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "last-context.yaml").write_text(":::invalid:::", encoding="utf-8")
    lc = load_last_context(home=tmp_path)
    assert lc.perspective is None


def test_save_last_context_atomic(tmp_path: Path) -> None:
    """Written file should be readable immediately after save."""
    save_last_context(
        LastContext(perspective="x", source_nature="notes"), home=tmp_path
    )
    path = tmp_path / "last-context.yaml"
    assert path.exists()
    raw = yaml.safe_load(path.read_text())
    assert raw["perspective"] == "x"
