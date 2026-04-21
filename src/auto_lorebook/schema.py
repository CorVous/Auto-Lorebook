"""Shared schema-version utility for YAML artifact readers."""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

TOOL_SCHEMA_VERSION = 1


class SchemaVersionError(ValueError):
    """Incompatible or missing schema_version in a YAML artifact."""

    __slots__ = ()


def check_schema_version(
    data: dict[str, object],
    source_description: str,
    *,
    hand_maintained: bool = False,
) -> None:
    """Validate schema_version field in a loaded YAML dict.

    :param data: loaded YAML data
    :param source_description: human-readable source name for error messages
    :param hand_maintained: if True, missing version warns rather than errors
    :raises SchemaVersionError: version too new, or missing on tool-written file
    """
    raw = data.get("schema_version")
    if raw is None:
        if hand_maintained:
            _logger.warning(
                "%s: missing schema_version, assuming 1",
                source_description,
            )
            return
        msg = f"{source_description}: missing schema_version (file may be corrupt)"
        raise SchemaVersionError(msg)
    version = int(str(raw))
    if version > TOOL_SCHEMA_VERSION:
        msg = (
            f"{source_description}: schema_version {version} exceeds "
            f"tool max {TOOL_SCHEMA_VERSION} — upgrade the tool"
        )
        raise SchemaVersionError(msg)
