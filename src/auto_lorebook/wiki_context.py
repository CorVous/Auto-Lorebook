"""Tolerant reader for .wiki-context.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from auto_lorebook.schema import read_tolerant_yaml

if TYPE_CHECKING:
    from pathlib import Path

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


def read(path: Path) -> WikiContext:
    """Load .wiki-context.yaml tolerantly.

    Missing or empty file returns an empty WikiContext.
    Missing schema_version logs a warning instead of raising.
    Unknown keys are ignored.
    """
    raw = read_tolerant_yaml(path, _FILE_LABEL, max_supported=_MAX_SCHEMA)
    if raw is None:
        return WikiContext()

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
