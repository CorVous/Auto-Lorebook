"""Shared schema_version read/validate helper."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)


class SchemaVersionError(ValueError):
    """Raised when schema_version is missing, invalid, or unsupported."""


def read_schema_version(
    data: dict[str, Any],
    file_label: str,
    *,
    max_supported: int,
) -> int:
    """Validate and return schema_version from a parsed YAML dict.

    :param data: parsed YAML dict
    :param file_label: human-readable name for error messages
    :param max_supported: ceiling for this file type; raises if exceeded
    :raises SchemaVersionError: missing, non-int, or future version
    """
    if "schema_version" not in data:
        msg = f"{file_label}: missing schema_version"
        raise SchemaVersionError(msg)
    v = data["schema_version"]
    if not isinstance(v, int) or v < 1:
        msg = f"{file_label}: schema_version must be a positive integer, got {v!r}"
        raise SchemaVersionError(msg)
    if v > max_supported:
        msg = (
            f"{file_label}: schema_version {v} exceeds max supported "
            f"{max_supported}; upgrade the tool"
        )
        raise SchemaVersionError(msg)
    return v


def read_tolerant_yaml(
    path: Path,
    file_label: str,
    *,
    max_supported: int,
) -> dict[str, Any] | None:
    """Load a tolerant YAML mapping; None if missing/empty/malformed.

    Missing schema_version logs a warning and is treated as 1.
    Parse errors, non-mapping roots, and unsupported schema_version
    all log a warning and return None.
    """
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError:
        _logger.warning("%s: could not parse YAML; ignoring", file_label)
        return None
    if not isinstance(raw, dict):
        return None

    if "schema_version" not in raw:
        _logger.warning(
            "%s: missing schema_version; treating as 1. "
            "Add 'schema_version: 1' to suppress.",
            file_label,
        )
        raw["schema_version"] = 1
    try:
        read_schema_version(raw, file_label, max_supported=max_supported)
    except SchemaVersionError:
        _logger.warning("%s: unrecognised schema_version; ignoring", file_label)
        return None
    return raw
