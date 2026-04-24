"""Shared schema_version read/validate helper."""

from __future__ import annotations

from typing import Any


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
