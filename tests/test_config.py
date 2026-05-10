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
    OpenRouterConfig,
    ModelsConfig,
    PreambleConfig,
    interactive_setup,
    load_config,
    load_last_context,
    save_config,
    save_last_context,
)
from auto_lorebook.wiki_registry import WikiEntry


def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _cfg(wiki_path: Path) -> Config:
    """Build a minimal v2 Config for tests."""
    return Config(
        wikis=[WikiEntry("test", wiki_path)],
        active_wiki="test",
    )


# ---------------------------------------------------------------------------
# load_config — v2 schema
# ---------------------------------------------------------------------------


def test_load_config_minimal(tmp_path: Path) -> None:
    wiki_path = str(tmp_path / "wiki")
    _write_config(
        tmp_path / "config.yaml",
        {
            "schema_version": 2,
            "wikis": [{"nickname": "home", "path": wiki_path}],
            "active_wiki": "home",
            "openrouter": {"api_key_env": "OPENROUTER_API_KEY"},
            "models": {"primary": "anthropic/claude-sonnet-4-5"},
        },
    )
    cfg = load_config(home=tmp_path)
    assert cfg.wikis == [WikiEntry("home", Path(wiki_path))]
    assert cfg.active_wiki == "home"
    assert cfg.openrouter.api_key_env == "OPENROUTER_API_KEY"
    assert cfg.models.primary == "anthropic/claude-sonnet-4-5"
    assert cfg.models.primary_context_window == 200_000
    assert cfg.preamble.budget_fraction == pytest.approx(0.8)


def test_load_config_with_all_fields(tmp_path: Path) -> None:
    wiki_path = str(tmp_path / "mywiki")
    _write_config(
        tmp_path / "config.yaml",
        {
            "schema_version": 2,
            "wikis": [{"nickname": "main", "path": wiki_path}],
            "active_wiki": "main",
            "openrouter": {"api_key_env": "MY_KEY"},
            "models": {
                "primary": "anthropic/claude-opus-4-7",
                "primary_context_window": 100_000,
                "extractor": "anthropic/claude-haiku-4-5",
                "planner": "anthropic/claude-sonnet-4-5",
            },
            "preamble": {"budget_fraction": 0.6},
        },
    )
    cfg = load_config(home=tmp_path)
    assert cfg.models.primary_context_window == 100_000
    assert cfg.models.extractor == "anthropic/claude-haiku-4-5"
    assert cfg.models.planner == "anthropic/claude-sonnet-4-5"
    assert cfg.preamble.budget_fraction == pytest.approx(0.6)


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(MissingConfigError, match="not found"):
        load_config(home=tmp_path)


def test_load_config_v1_hard_errors(tmp_path: Path) -> None:
    wiki_path = str(tmp_path / "wiki")
    _write_config(
        tmp_path / "config.yaml",
        {"schema_version": 1, "wiki_repo_path": wiki_path},
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(home=tmp_path)
    msg = str(exc_info.value)
    assert str(tmp_path / "config.yaml") in msg
    assert "delete" in msg.lower()
    assert "re-run" in msg.lower()


def test_load_config_v1_detected_by_wiki_repo_path_key(tmp_path: Path) -> None:
    """wiki_repo_path key without schema_version also triggers v1 error."""
    wiki_path = str(tmp_path / "wiki")
    _write_config(
        tmp_path / "config.yaml",
        {"schema_version": 2, "wiki_repo_path": wiki_path},
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(home=tmp_path)
    msg = str(exc_info.value)
    assert "delete" in msg.lower()


def test_load_config_missing_wikis_raises(tmp_path: Path) -> None:
    _write_config(
        tmp_path / "config.yaml",
        {"schema_version": 2, "active_wiki": "home"},
    )
    with pytest.raises(ConfigError, match="wikis"):
        load_config(home=tmp_path)


def test_load_config_unknown_active_wiki_raises(tmp_path: Path) -> None:
    wiki_path = str(tmp_path / "wiki")
    _write_config(
        tmp_path / "config.yaml",
        {
            "schema_version": 2,
            "wikis": [{"nickname": "home", "path": wiki_path}],
            "active_wiki": "nonexistent",
        },
    )
    with pytest.raises(ConfigError, match="nonexistent"):
        load_config(home=tmp_path)


def test_load_config_bad_schema_version_raises(tmp_path: Path) -> None:
    wiki_path = str(tmp_path / "wiki")
    _write_config(
        tmp_path / "config.yaml",
        {
            "schema_version": 99,
            "wikis": [{"nickname": "home", "path": wiki_path}],
            "active_wiki": "home",
        },
    )
    with pytest.raises(ConfigError, match="exceeds max supported"):
        load_config(home=tmp_path)


def test_load_config_missing_schema_version_raises(tmp_path: Path) -> None:
    wiki_path = str(tmp_path / "wiki")
    _write_config(
        tmp_path / "config.yaml",
        {
            "wikis": [{"nickname": "home", "path": wiki_path}],
            "active_wiki": "home",
        },
    )
    with pytest.raises(ConfigError, match="missing schema_version"):
        load_config(home=tmp_path)


# ---------------------------------------------------------------------------
# Config.resolve_active_wiki
# ---------------------------------------------------------------------------


def test_resolve_active_wiki_returns_active_path(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    cfg = _cfg(wiki)
    assert cfg.resolve_active_wiki(None) == wiki


def test_resolve_active_wiki_override_wins(tmp_path: Path) -> None:
    wiki1 = tmp_path / "wiki1"
    wiki1.mkdir()
    wiki2 = tmp_path / "wiki2"
    wiki2.mkdir()
    cfg = Config(
        wikis=[WikiEntry("main", wiki1), WikiEntry("alt", wiki2)],
        active_wiki="main",
    )
    assert cfg.resolve_active_wiki("alt") == wiki2


def test_resolve_active_wiki_unknown_override_raises(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    cfg = _cfg(wiki)
    with pytest.raises(ConfigError, match="nope"):
        cfg.resolve_active_wiki("nope")


def test_resolve_active_wiki_unset_raises(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    cfg = Config(wikis=[WikiEntry("home", wiki)], active_wiki=None)
    with pytest.raises(ConfigError):
        cfg.resolve_active_wiki(None)


def test_resolve_active_wiki_missing_path_raises(tmp_path: Path) -> None:
    wiki = tmp_path / "missing"
    # don't create the directory
    cfg = _cfg(wiki)
    with pytest.raises(ConfigError, match="test"):
        cfg.resolve_active_wiki(None)


# ---------------------------------------------------------------------------
# interactive_setup — v2
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
    nick = "mywiki"
    _patch_setup_inputs(monkeypatch, inputs=[nick, str(wiki), ""])

    cfg = interactive_setup(home=home)

    raw = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert raw["active_wiki"] == nick
    assert raw["wikis"][0] == {"nickname": nick, "path": str(wiki.resolve())}
    assert cfg.resolve_active_wiki(None) == wiki.resolve()
    assert cfg.openrouter.api_key_env == "OPENROUTER_API_KEY"
    assert cfg.models.primary == "anthropic/claude-sonnet-4-5"
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
        monkeypatch, inputs=["main", str(wiki), "anthropic/claude-opus-4-7"]
    )

    cfg = interactive_setup(home=home)

    assert cfg.openrouter.api_key_env == "OPENROUTER_API_KEY"
    assert cfg.models.primary == "anthropic/claude-opus-4-7"


def test_interactive_setup_reprompts_on_blank_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    wiki = tmp_path / "wiki"
    # blank nickname, then valid nickname; then wiki path; default model
    _patch_setup_inputs(monkeypatch, inputs=["", "home", str(wiki), ""])

    cfg = interactive_setup(home=home)
    assert cfg.resolve_active_wiki(None) == wiki.resolve()


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

    _patch_setup_inputs(monkeypatch, inputs=["home", str(wiki), ""])

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
    _patch_setup_inputs(
        monkeypatch, inputs=["home", str(wiki), ""], api_key="sk-or-v1-test"
    )

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
    _patch_setup_inputs(monkeypatch, inputs=["home", str(wiki), ""], api_key="")

    interactive_setup(home=home)

    assert not (home / "credentials").exists()


def test_get_api_key_falls_back_to_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / "credentials").write_text("sk-or-v1-fromfile\n", encoding="utf-8")
    cfg = _cfg(tmp_path / "wiki")
    assert cfg.get_api_key() == "sk-or-v1-fromfile"


def test_get_api_key_env_var_wins_over_credentials_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-fromenv")
    (tmp_path / "credentials").write_text("sk-or-v1-fromfile", encoding="utf-8")
    cfg = _cfg(tmp_path / "wiki")
    assert cfg.get_api_key() == "sk-or-v1-fromenv"


def test_get_api_key_returns_none_when_neither_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(tmp_path))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = _cfg(tmp_path / "wiki")
    assert cfg.get_api_key() is None


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


# ---------------------------------------------------------------------------
# save_config
# ---------------------------------------------------------------------------


def test_save_config_roundtrip(tmp_path: Path) -> None:
    """save_config writes valid config.yaml; reload preserves all fields."""
    wiki = tmp_path / "mywiki"
    wiki.mkdir()
    cfg = Config(
        wikis=[
            WikiEntry("main", wiki),
            WikiEntry("alt", tmp_path / "alt"),
        ],
        active_wiki="main",
        openrouter=OpenRouterConfig(api_key_env="MY_KEY"),
        models=ModelsConfig(
            primary="anthropic/claude-opus-4-7",
            primary_context_window=100_000,
            extractor="anthropic/claude-haiku-4-5",
            planner="anthropic/claude-sonnet-4-5",
        ),
        preamble=PreambleConfig(budget_fraction=0.6),
    )
    save_config(cfg, home=tmp_path)

    raw = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert raw["active_wiki"] == "main"
    assert raw["wikis"] == [
        {"nickname": "main", "path": str(wiki)},
        {"nickname": "alt", "path": str(tmp_path / "alt")},
    ]
    assert raw["openrouter"]["api_key_env"] == "MY_KEY"
    assert raw["models"]["primary"] == "anthropic/claude-opus-4-7"
    assert raw["models"]["primary_context_window"] == 100_000
    assert raw["models"]["extractor"] == "anthropic/claude-haiku-4-5"
    assert raw["models"]["planner"] == "anthropic/claude-sonnet-4-5"
    assert raw["preamble"]["budget_fraction"] == pytest.approx(0.6)

    # confirm load_config can reload it (skip path-exist check for alt)
    wiki2 = tmp_path / "alt"
    wiki2.mkdir()
    reloaded = load_config(home=tmp_path)
    assert reloaded.active_wiki == "main"
    assert len(reloaded.wikis) == 2
    assert reloaded.openrouter.api_key_env == "MY_KEY"
    assert reloaded.models.primary == "anthropic/claude-opus-4-7"
    assert reloaded.preamble.budget_fraction == pytest.approx(0.6)
