"""Tolerant reader for .transcription-corrections.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from auto_lorebook.schema import read_tolerant_yaml

if TYPE_CHECKING:
    from pathlib import Path

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


def read(path: Path) -> Corrections:
    """Load .transcription-corrections.yaml tolerantly.

    Missing or empty file returns an empty Corrections.
    Missing schema_version logs a warning instead of raising.
    Unknown keys are ignored.
    """
    raw = read_tolerant_yaml(path, _FILE_LABEL, max_supported=_MAX_SCHEMA)
    if raw is None:
        return Corrections()

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
