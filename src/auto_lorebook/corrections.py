"""Tolerant reader for .transcription-corrections.yaml."""

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
_FILE_LABEL = ".transcription-corrections.yaml"


@dataclass
class Correction:
    """Single phonetic correction entry."""

    wrong: str
    right: str
    first_seen_in: str | None = None
    also_seen_in: list[str] = field(default_factory=list)


@dataclass
class Corrections:
    """Parsed .transcription-corrections.yaml."""

    corrections: list[Correction] = field(default_factory=list)


def _empty() -> Corrections:
    return Corrections()


def read(path: Path) -> Corrections:
    """Load .transcription-corrections.yaml tolerantly.

    Missing or empty file returns an empty Corrections.
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
        _logger.warning(
            "%s: could not parse YAML; using empty corrections", _FILE_LABEL
        )
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
            "%s: unrecognised schema_version; using empty corrections", _FILE_LABEL
        )
        return _empty()

    items: list[dict[str, Any]] = raw.get("corrections") or []
    result: list[Correction] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        wrong = item.get("wrong") or ""
        right = item.get("right") or ""
        if not wrong or not right:
            continue
        result.append(
            Correction(
                wrong=wrong,
                right=right,
                first_seen_in=item.get("first_seen_in") or None,
                also_seen_in=item.get("also_seen_in") or [],
            )
        )
    return Corrections(corrections=result)
