"""Config file reader/writer for ~/.auto-lorebook/config.yaml."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import yaml

_logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_CONFIG_DIR = Path.home() / ".auto-lorebook"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"
_DEFAULT_MODEL = "openrouter/anthropic/claude-sonnet-4"
_DEFAULT_BUDGET_FRACTION = 0.80


@dataclass(slots=True)
class ModelParams:
    """LLM sampling parameters."""

    temperature: float = 1.0
    max_tokens: int = 4096


@dataclass(slots=True)
class Config:
    """Tool-level configuration."""

    schema_version: int = SCHEMA_VERSION
    wiki_repo_path: Path | None = None
    model: str = _DEFAULT_MODEL
    model_params: ModelParams = field(default_factory=ModelParams)
    preamble_budget_fraction: float = _DEFAULT_BUDGET_FRACTION


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load config from YAML, returning defaults if file absent.

    :param path: path to config.yaml
    :return: populated Config, all defaults if file missing
    """
    if not path.exists():
        return Config()
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not raw:
        return Config()
    data = cast("dict[str, object]", raw)
    wiki_raw = data.get("wiki_repo_path")
    wiki_path = Path(str(wiki_raw)) if wiki_raw is not None else None
    mp_raw = data.get("model_params")
    mp = ModelParams()
    if isinstance(mp_raw, dict):
        mp_data = cast("dict[str, object]", mp_raw)
        temp = mp_data.get("temperature")
        if temp is not None:
            mp.temperature = float(str(temp))
        max_t = mp_data.get("max_tokens")
        if max_t is not None:
            mp.max_tokens = int(str(max_t))
    schema_v = data.get("schema_version")
    model = data.get("model")
    budget = data.get("preamble_budget_fraction")
    return Config(
        schema_version=int(str(schema_v)) if schema_v is not None else SCHEMA_VERSION,
        wiki_repo_path=wiki_path,
        model=str(model) if model is not None else _DEFAULT_MODEL,
        model_params=mp,
        preamble_budget_fraction=(
            float(str(budget)) if budget is not None else _DEFAULT_BUDGET_FRACTION
        ),
    )


def save_config(config: Config, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Write config to YAML, creating parent dirs as needed.

    :param config: Config to persist
    :param path: destination path (default ~/.auto-lorebook/config.yaml)
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {
        "schema_version": config.schema_version,
        "wiki_repo_path": str(config.wiki_repo_path) if config.wiki_repo_path else None,
        "model": config.model,
        "model_params": {
            "temperature": config.model_params.temperature,
            "max_tokens": config.model_params.max_tokens,
        },
        "preamble_budget_fraction": config.preamble_budget_fraction,
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False)
