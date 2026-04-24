"""Config loader for ~/.auto-lorebook/config.yaml + last-context.yaml."""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from auto_lorebook.schema import SchemaVersionError, read_schema_version

_logger = logging.getLogger(__name__)

_MAX_SCHEMA = 1
_CONFIG_DIR_ENV = "AUTO_LOREBOOK_HOME"


@dataclass
class OpenRouterConfig:
    """OpenRouter section of config.yaml."""

    api_key_env: str = "OPENROUTER_API_KEY"


@dataclass
class ModelsConfig:
    """Models section of config.yaml."""

    primary: str = "openrouter/anthropic/claude-sonnet-4-5"
    primary_context_window: int = 200_000
    extractor: str | None = None


@dataclass
class PreambleConfig:
    """Preamble section of config.yaml."""

    budget_fraction: float = 0.8


@dataclass
class Config:
    """Loaded ~/.auto-lorebook/config.yaml."""

    wiki_repo_path: Path
    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    preamble: PreambleConfig = field(default_factory=PreambleConfig)

    def get_api_key(self) -> str | None:
        """Read API key from environment variable named in config."""
        return os.environ.get(self.openrouter.api_key_env)


@dataclass
class LastContext:
    """Persisted context defaults from the most recent ingest."""

    perspective: str | None = None
    source_nature: str | None = None


class ConfigError(ValueError):
    """Raised when config.yaml is missing or malformed."""


def _config_dir(home: Path | None = None) -> Path:
    if home is not None:
        return home
    env = os.environ.get(_CONFIG_DIR_ENV)
    if env:
        return Path(env)
    return Path.home() / ".auto-lorebook"


def load_config(home: Path | None = None) -> Config:
    """Load and validate ~/.auto-lorebook/config.yaml.

    :param home: override for the config directory (for tests)
    :raises ConfigError: if file is missing or malformed
    """
    cfg_path = _config_dir(home) / "config.yaml"
    if not cfg_path.exists():
        msg = (
            f"Config file not found: {cfg_path}\n"
            "Create it with at minimum:\n"
            "  schema_version: 1\n"
            "  wiki_repo_path: /path/to/your/wiki\n"
            "  openrouter:\n"
            "    api_key_env: OPENROUTER_API_KEY\n"
            "  models:\n"
            "    primary: openrouter/anthropic/claude-sonnet-4-5\n"
        )
        raise ConfigError(msg)
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{cfg_path}: expected a YAML mapping, got {type(raw).__name__}"
        raise ConfigError(msg)
    try:
        read_schema_version(raw, str(cfg_path), max_supported=_MAX_SCHEMA)
    except SchemaVersionError as e:
        raise ConfigError(str(e)) from e

    wiki_repo_path_raw = raw.get("wiki_repo_path")
    if not wiki_repo_path_raw:
        msg = f"{cfg_path}: wiki_repo_path is required"
        raise ConfigError(msg)

    or_raw: dict[str, Any] = raw.get("openrouter") or {}
    models_raw: dict[str, Any] = raw.get("models") or {}
    preamble_raw: dict[str, Any] = raw.get("preamble") or {}

    openrouter = OpenRouterConfig(
        api_key_env=or_raw.get("api_key_env", "OPENROUTER_API_KEY"),
    )
    models = ModelsConfig(
        primary=models_raw.get("primary", "openrouter/anthropic/claude-sonnet-4-5"),
        primary_context_window=int(models_raw.get("primary_context_window", 200_000)),
        extractor=models_raw.get("extractor"),
    )
    preamble = PreambleConfig(
        budget_fraction=float(preamble_raw.get("budget_fraction", 0.8)),
    )

    return Config(
        wiki_repo_path=Path(wiki_repo_path_raw),
        openrouter=openrouter,
        models=models,
        preamble=preamble,
    )


def load_last_context(home: Path | None = None) -> LastContext:
    """Load ~/.auto-lorebook/last-context.yaml; missing/corrupt → empty."""
    path = _config_dir(home) / "last-context.yaml"
    if not path.exists():
        return LastContext()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return LastContext()
        return LastContext(
            perspective=raw.get("perspective") or None,
            source_nature=raw.get("source_nature") or None,
        )
    except Exception:  # noqa: BLE001
        _logger.warning("Could not read last-context.yaml; ignoring")
        return LastContext()


def save_last_context(last: LastContext, home: Path | None = None) -> None:
    """Atomically write perspective and source_nature to last-context.yaml."""
    cfg_dir = _config_dir(home)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "last-context.yaml"
    data: dict[str, Any] = {}
    if last.perspective is not None:
        data["perspective"] = last.perspective
    if last.source_nature is not None:
        data["source_nature"] = last.source_nature
    _atomic_write(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def _atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically via tempfile + os.replace."""
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        Path(tmp).replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
