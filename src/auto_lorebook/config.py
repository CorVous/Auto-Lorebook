"""Config loader for ~/.auto-lorebook/config.yaml + last-context.yaml."""

from __future__ import annotations

import getpass
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from auto_lorebook._io import atomic_write_text
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
        """Resolve API key. Env var wins; falls back to credentials file."""
        env_value = os.environ.get(self.openrouter.api_key_env)
        if env_value:
            return env_value
        return _read_credentials()


@dataclass
class LastContext:
    """Persisted context defaults from the most recent ingest."""

    perspective: str | None = None
    source_nature: str | None = None


class ConfigError(ValueError):
    """Raised when config.yaml is missing or malformed."""


class MissingConfigError(ConfigError):
    """Raised specifically when config.yaml does not exist (first run)."""


def config_dir(home: Path | None = None) -> Path:
    """Resolve ~/.auto-lorebook, respecting AUTO_LOREBOOK_HOME override."""
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
    cfg_path = config_dir(home) / "config.yaml"
    if not cfg_path.exists():
        msg = (
            f"Config file not found: {cfg_path}\n"
            "Run an interactive command (e.g. `auto-lorebook ingest ...`) "
            "to create one, or write it by hand."
        )
        raise MissingConfigError(msg)
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
    path = config_dir(home) / "last-context.yaml"
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
    cfg_dir = config_dir(home)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "last-context.yaml"
    data: dict[str, Any] = {}
    if last.perspective is not None:
        data["perspective"] = last.perspective
    if last.source_nature is not None:
        data["source_nature"] = last.source_nature
    atomic_write_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


_DEFAULT_API_KEY_ENV = "OPENROUTER_API_KEY"
_DEFAULT_MODEL = "openrouter/anthropic/claude-sonnet-4-5"
_WIKI_SUBDIRS = ("characters", "locations", "factions", "events", "items", "concepts")
_CREDENTIALS_FILE = "credentials"


def _prompt(prompt_text: str, default: str | None = None) -> str:
    """Prompt for a value; blank → default. Re-prompt if no default + blank."""
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{prompt_text}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("(required)")  # noqa: T201


def _credentials_path(home: Path | None = None) -> Path:
    return config_dir(home) / _CREDENTIALS_FILE


def _read_credentials(home: Path | None = None) -> str | None:
    """Read API key from `<config_dir>/credentials`; missing/empty → None."""
    path = _credentials_path(home)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        _logger.warning("Could not read %s", path)
        return None


def _write_credentials(api_key: str, home: Path | None = None) -> Path:
    """Write API key to credentials file with mode 0600."""
    cfg_dir = config_dir(home)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = _credentials_path(home)
    atomic_write_text(path, api_key + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        # Windows / unusual filesystems may not support POSIX modes.
        _logger.warning("Could not chmod %s to 0600", path)
    return path


def interactive_setup(home: Path | None = None) -> Config:
    """Prompt the user for first-run config and write `config.yaml`.

    The API key is read with `getpass` (input hidden) and stored in
    `<config_dir>/credentials` (mode 0600). Blank skips the file and
    falls back to the `OPENROUTER_API_KEY` env var at runtime.

    Creates the wiki repo skeleton (entity dirs + `.wiki-context.yaml` /
    `.transcription-corrections.yaml` schema stubs) if it doesn't exist.

    :raises KeyboardInterrupt: user pressed Ctrl-C
    """
    cfg_dir = config_dir(home)
    cfg_path = cfg_dir / "config.yaml"

    print("First run: setting up ~/.auto-lorebook/config.yaml.")  # noqa: T201
    print()  # noqa: T201

    wiki_raw = _prompt("Wiki repository directory")
    wiki = Path(wiki_raw).expanduser().resolve()
    api_key = getpass.getpass(
        "OpenRouter API key (input hidden; leave blank to use "
        f"${_DEFAULT_API_KEY_ENV}): "
    ).strip()
    model = _prompt(
        "Primary model slug (used for both reading substages)",
        default=_DEFAULT_MODEL,
    )

    data: dict[str, Any] = {
        "schema_version": 1,
        "wiki_repo_path": str(wiki),
        "openrouter": {"api_key_env": _DEFAULT_API_KEY_ENV},
        "models": {"primary": model},
    }
    cfg_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        cfg_path,
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    )

    cred_path: Path | None = None
    if api_key:
        cred_path = _write_credentials(api_key, home=home)

    _bootstrap_wiki(wiki)

    print()  # noqa: T201
    print(f"Wrote {cfg_path}")  # noqa: T201
    if cred_path is not None:
        print(f"Wrote API key to {cred_path} (mode 0600)")  # noqa: T201
    elif not os.environ.get(_DEFAULT_API_KEY_ENV):
        print(  # noqa: T201
            f"Reminder: export {_DEFAULT_API_KEY_ENV}=<your OpenRouter key> "
            "before running `generate-reading`."
        )

    return load_config(home=home)


def _bootstrap_wiki(wiki: Path) -> None:
    """Create the wiki entity dirs and tolerant-yaml stubs if absent."""
    wiki.mkdir(parents=True, exist_ok=True)
    for sub in _WIKI_SUBDIRS:
        (wiki / sub).mkdir(exist_ok=True)
    for fname in (".wiki-context.yaml", ".transcription-corrections.yaml"):
        path = wiki / fname
        if not path.exists():
            atomic_write_text(path, "schema_version: 1\n")
