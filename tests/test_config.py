"""Tests for config module."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from auto_lorebook.config import (
    SCHEMA_VERSION,
    Config,
    ModelParams,
    load_config,
    save_config,
)


def test_load_config_missing_file_returns_defaults(tmp_path: Path) -> None:
    """Missing config file returns all defaults."""
    config = load_config(tmp_path / "config.yaml")
    assert config.schema_version == SCHEMA_VERSION
    assert config.wiki_repo_path is None
    assert config.model == "openrouter/anthropic/claude-sonnet-4"
    assert config.model_params.temperature == pytest.approx(1.0)
    assert config.model_params.max_tokens == 4096
    assert config.preamble_budget_fraction == pytest.approx(0.80)


def test_load_config_empty_file_returns_defaults(tmp_path: Path) -> None:
    """Empty config file returns all defaults."""
    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")
    config = load_config(path)
    assert config.schema_version == SCHEMA_VERSION
    assert config.wiki_repo_path is None


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    """Round-trip save/load preserves all fields."""
    path = tmp_path / "config.yaml"
    original = Config(
        wiki_repo_path=Path("/my/wiki"),
        model="anthropic/claude-opus-4",
        model_params=ModelParams(temperature=0.5, max_tokens=2048),
        preamble_budget_fraction=0.75,
    )
    save_config(original, path)
    loaded = load_config(path)
    assert loaded.wiki_repo_path == Path("/my/wiki")
    assert loaded.model == "anthropic/claude-opus-4"
    assert loaded.model_params.temperature == pytest.approx(0.5)
    assert loaded.model_params.max_tokens == 2048
    assert loaded.preamble_budget_fraction == pytest.approx(0.75)


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    """save_config creates parent directories if absent."""
    path = tmp_path / "deep" / "nested" / "config.yaml"
    save_config(Config(), path)
    assert path.exists()


def test_schema_version_present_in_saved_file(tmp_path: Path) -> None:
    """Saved config includes schema_version: 1."""
    path = tmp_path / "config.yaml"
    save_config(Config(), path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1


def test_load_config_null_wiki_path(tmp_path: Path) -> None:
    """wiki_repo_path: null in YAML yields None."""
    path = tmp_path / "config.yaml"
    path.write_text("schema_version: 1\nwiki_repo_path: null\n", encoding="utf-8")
    config = load_config(path)
    assert config.wiki_repo_path is None
