"""Config loader: ~/.auto-lorebook/config.yaml + <wiki>/.wiki-state/last-context."""

from __future__ import annotations

import getpass
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from auto_lorebook import wiki_bootstrap as wiki_bootstrap_mod
from auto_lorebook import wiki_state as wiki_state_mod
from auto_lorebook._io import atomic_write_text
from auto_lorebook.schema import SchemaVersionError, read_schema_version
from auto_lorebook.wiki_registry import WikiEntry

_logger = logging.getLogger(__name__)

_MAX_SCHEMA = 2
_CONFIG_DIR_ENV = "AUTO_LOREBOOK_HOME"


@dataclass
class OpenRouterConfig:
    """OpenRouter section of config.yaml."""

    api_key_env: str = "OPENROUTER_API_KEY"


@dataclass
class ModelsConfig:
    """Models section of config.yaml."""

    primary: str = "anthropic/claude-sonnet-4-5"
    primary_context_window: int = 200_000
    extractor: str | None = None
    planner: str | None = None


@dataclass
class PreambleConfig:
    """Preamble section of config.yaml."""

    budget_fraction: float = 0.8


@dataclass
class Config:
    """Loaded ~/.auto-lorebook/config.yaml."""

    wikis: list[WikiEntry]
    active_wiki: str | None
    openrouter: OpenRouterConfig = field(default_factory=OpenRouterConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    preamble: PreambleConfig = field(default_factory=PreambleConfig)

    def get_api_key(self) -> str | None:
        """Resolve API key. Env var wins; falls back to credentials file."""
        env_value = os.environ.get(self.openrouter.api_key_env)
        if env_value:
            return env_value
        return _read_credentials()

    def resolve_active_wiki(self, override: str | None) -> Path:
        """Resolve the active wiki path.

        Precedence: override > active_wiki.

        :raises ConfigError: override unknown, active unset, or path missing on disk
        """
        known: dict[str, Path] = {e.nickname: e.path for e in self.wikis}
        nick = override if override is not None else self.active_wiki
        if nick is None:
            msg = "No active wiki configured. Set active_wiki in config.yaml."
            raise ConfigError(msg)
        if nick not in known:
            msg = f"Unknown wiki nickname: {nick!r}. Check config.yaml."
            raise ConfigError(msg)
        path = known[nick]
        if not path.exists():
            msg = (
                f"Wiki {nick!r} path does not exist: {path}. "
                "Update or remove the entry in config.yaml."
            )
            raise ConfigError(msg)
        return path


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

    # v1 hard error: schema_version == 1 or wiki_repo_path key present
    if raw.get("schema_version") == 1 or "wiki_repo_path" in raw:
        msg = (
            f"{cfg_path}: schema_version 1 is not supported. "
            "Delete this file and re-run `auto-lorebook ingest` "
            "to create a v2 config."
        )
        raise ConfigError(msg)

    try:
        read_schema_version(raw, str(cfg_path), max_supported=_MAX_SCHEMA)
    except SchemaVersionError as e:
        raise ConfigError(str(e)) from e

    wikis_raw = raw.get("wikis")
    if not wikis_raw:
        msg = f"{cfg_path}: wikis is required"
        raise ConfigError(msg)

    wikis = [WikiEntry(d["nickname"], Path(d["path"])) for d in wikis_raw]
    active_wiki: str | None = raw.get("active_wiki") or None

    known = {e.nickname for e in wikis}
    if active_wiki is not None and active_wiki not in known:
        msg = f"{cfg_path}: active_wiki {active_wiki!r} not found in wikis"
        raise ConfigError(msg)

    or_raw: dict[str, Any] = raw.get("openrouter") or {}
    models_raw: dict[str, Any] = raw.get("models") or {}
    preamble_raw: dict[str, Any] = raw.get("preamble") or {}

    openrouter = OpenRouterConfig(
        api_key_env=or_raw.get("api_key_env", "OPENROUTER_API_KEY"),
    )
    models = ModelsConfig(
        primary=models_raw.get("primary", "anthropic/claude-sonnet-4-5"),
        primary_context_window=int(models_raw.get("primary_context_window", 200_000)),
        extractor=models_raw.get("extractor"),
        planner=models_raw.get("planner"),
    )
    preamble = PreambleConfig(
        budget_fraction=float(preamble_raw.get("budget_fraction", 0.8)),
    )

    return Config(
        wikis=wikis,
        active_wiki=active_wiki,
        openrouter=openrouter,
        models=models,
        preamble=preamble,
    )


def load_last_context(
    home: Path | None = None,
    wiki_root: Path | None = None,
) -> LastContext:
    """Load last-context.yaml; missing/corrupt → empty.

    If `wiki_root` is given, reads from `<wiki>/.wiki-state/last-context.yaml`.
    Otherwise falls back to `<config_dir>/last-context.yaml` (legacy).
    """
    if wiki_root is not None:
        path = wiki_state_mod.last_context_path(wiki_root)
    else:
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


def save_last_context(
    last: LastContext,
    home: Path | None = None,
    wiki_root: Path | None = None,
) -> None:
    """Atomically write perspective and source_nature to last-context.yaml.

    If `wiki_root` is given, writes to `<wiki>/.wiki-state/last-context.yaml`
    (creating `.wiki-state/` if needed). Otherwise writes to `<config_dir>/`.
    """
    if wiki_root is not None:
        state_dir = wiki_state_mod.wiki_state_dir(wiki_root)
        state_dir.mkdir(parents=True, exist_ok=True)
        path = wiki_state_mod.last_context_path(wiki_root)
    else:
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
_DEFAULT_MODEL = "anthropic/claude-sonnet-4-5"
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

    nick = _prompt("Wiki nickname")
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
        "schema_version": 2,
        "active_wiki": nick,
        "wikis": [{"nickname": nick, "path": str(wiki)}],
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

    wiki_bootstrap_mod.bootstrap(wiki)

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


def save_config(cfg: Config, home: Path | None = None) -> None:
    """Atomically write cfg back to config.yaml.

    Preserves schema_version=2 and all sections. Does not write
    the credentials file (managed separately).
    """
    cfg_dir = config_dir(home)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.yaml"
    data: dict[str, Any] = {
        "schema_version": 2,
        "active_wiki": cfg.active_wiki,
        "wikis": [{"nickname": e.nickname, "path": str(e.path)} for e in cfg.wikis],
        "openrouter": {"api_key_env": cfg.openrouter.api_key_env},
        "models": {
            "primary": cfg.models.primary,
            "primary_context_window": cfg.models.primary_context_window,
        },
        "preamble": {"budget_fraction": cfg.preamble.budget_fraction},
    }
    if cfg.models.extractor is not None:
        data["models"]["extractor"] = cfg.models.extractor
    if cfg.models.planner is not None:
        data["models"]["planner"] = cfg.models.planner
    atomic_write_text(
        cfg_path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    )
