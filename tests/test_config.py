"""Tests for config.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from auto_lorebook.config import (
    Config,
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


def _patch_setup_inputs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    inputs: list[str],
    api_key: str = "",
) -> None:
    """Mock the visible-input answers and the hidden API-key prompt."""
    answers = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr("auto_lorebook.config.getpass.getpass", lambda _prompt: api_key)


def test_interactive_setup_writes_config_and_skeleton(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    wiki = tmp_path / "wiki"
    _patch_setup_inputs(monkeypatch, inputs=[str(wiki), ""])

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


def test_interactive_setup_custom_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    wiki = tmp_path / "mywiki"
    _patch_setup_inputs(
        monkeypatch, inputs=[str(wiki), "openrouter/anthropic/claude-opus-4-7"]
    )

    cfg = interactive_setup(home=home)

    assert cfg.openrouter.api_key_env == "OPENROUTER_API_KEY"
    assert cfg.models.primary == "openrouter/anthropic/claude-opus-4-7"


def test_interactive_setup_reprompts_on_blank_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    wiki = tmp_path / "wiki"
    # first answer blank for wiki, second valid; default accepted for model
    _patch_setup_inputs(monkeypatch, inputs=["", str(wiki), ""])

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

    _patch_setup_inputs(monkeypatch, inputs=[str(wiki), ""])

    interactive_setup(home=home)
    # existing content preserved
    assert "Aether" in existing.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# credentials file (~/.auto-lorebook/credentials)
# ---------------------------------------------------------------------------


def test_interactive_setup_writes_credentials_file_with_strict_perms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hidden API-key prompt → 0600 credentials file alongside config.yaml."""
    home = tmp_path / "home"
    wiki = tmp_path / "wiki"
    _patch_setup_inputs(monkeypatch, inputs=[str(wiki), ""], api_key="sk-or-v1-test")

    interactive_setup(home=home)

    cred_path = home / "credentials"
    assert cred_path.exists()
    assert cred_path.read_text(encoding="utf-8").strip() == "sk-or-v1-test"
    mode = cred_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_interactive_setup_blank_api_key_skips_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blank → no credentials file written; user expected to use env var."""
    home = tmp_path / "home"
    wiki = tmp_path / "wiki"
    _patch_setup_inputs(monkeypatch, inputs=[str(wiki), ""], api_key="")

    interactive_setup(home=home)

    assert not (home / "credentials").exists()


def test_get_api_key_falls_back_to_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / "credentials").write_text("sk-or-v1-fromfile\n", encoding="utf-8")
    cfg = Config(wiki_repo_path=tmp_path / "wiki")
    assert cfg.get_api_key() == "sk-or-v1-fromfile"


def test_get_api_key_env_var_wins_over_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-fromenv")
    (tmp_path / "credentials").write_text("sk-or-v1-fromfile", encoding="utf-8")
    cfg = Config(wiki_repo_path=tmp_path / "wiki")
    assert cfg.get_api_key() == "sk-or-v1-fromenv"


def test_get_api_key_returns_none_when_neither_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = Config(wiki_repo_path=tmp_path / "wiki")
    assert cfg.get_api_key() is None


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
