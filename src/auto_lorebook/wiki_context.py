"""Tolerant reader for .wiki-context.yaml."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import yaml

from auto_lorebook.schema import read_schema_version

_logger = logging.getLogger(__name__)

_MAX_SCHEMA = 1
_FILE_LABEL = ".wiki-context.yaml"


@dataclass
class SettingContext:
    """Setting sub-object from .wiki-context.yaml."""

    name: str | None = None
    description: str | None = None


@dataclass
class WikiContext:
    """Parsed .wiki-context.yaml."""

    setting: SettingContext = field(default_factory=SettingContext)
    naming_conventions: str | None = None
    interpretation_defaults: str | None = None
    recurring_speakers: list[dict[str, Any]] = field(default_factory=list)


def _empty() -> WikiContext:
    return WikiContext()


def read(path: Path) -> WikiContext:
    """Load .wiki-context.yaml tolerantly.

    Missing or empty file returns an empty WikiContext.
    Missing schema_version logs a warning instead of raising.
    Unknown keys are ignored.
    """
    if not path.exists():
        return _empty()
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return _empty()
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError:
        _logger.warning("%s: could not parse YAML; using empty context", _FILE_LABEL)
        return _empty()
    if not isinstance(raw, dict):
        return _empty()

    if "schema_version" not in raw:
        _logger.warning(
            "%s: missing schema_version; treating as 1. "
            "Add 'schema_version: 1' to suppress.",
            _FILE_LABEL,
        )
        raw["schema_version"] = 1
    try:
        read_schema_version(raw, _FILE_LABEL, max_supported=_MAX_SCHEMA)
    except Exception:  # noqa: BLE001
        _logger.warning(
            "%s: unrecognised schema_version; using empty context", _FILE_LABEL
        )
        return _empty()

    setting_raw: dict[str, Any] = raw.get("setting") or {}
    setting = SettingContext(
        name=setting_raw.get("name") or None,
        description=setting_raw.get("description") or None,
    )
    return WikiContext(
        setting=setting,
        naming_conventions=raw.get("naming_conventions") or None,
        interpretation_defaults=raw.get("interpretation_defaults") or None,
        recurring_speakers=raw.get("recurring_speakers") or [],
    )
