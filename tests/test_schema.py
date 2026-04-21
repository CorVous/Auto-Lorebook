"""Tests for schema version utility."""

from __future__ import annotations

import logging

import pytest

from auto_lorebook.schema import (
    TOOL_SCHEMA_VERSION,
    SchemaVersionError,
    check_schema_version,
)


def test_current_version_passes() -> None:
    """Current tool version is accepted without error."""
    check_schema_version({"schema_version": TOOL_SCHEMA_VERSION}, "test")


def test_older_version_passes() -> None:
    """Older schema version accepted (backward compatible)."""
    check_schema_version({"schema_version": 0}, "test")


def test_future_version_raises() -> None:
    """Version exceeding tool max raises SchemaVersionError."""
    with pytest.raises(SchemaVersionError, match="exceeds"):
        check_schema_version(
            {"schema_version": TOOL_SCHEMA_VERSION + 1},
            "test_file.yaml",
        )


def test_missing_version_tool_written_raises() -> None:
    """Missing schema_version on tool-written file raises SchemaVersionError."""
    with pytest.raises(SchemaVersionError, match="missing"):
        check_schema_version({}, "test_file.yaml")


def test_missing_version_hand_maintained_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing schema_version on hand-maintained file warns and returns."""
    with caplog.at_level(logging.WARNING):
        check_schema_version({}, "hand_file.yaml", hand_maintained=True)
    assert "missing schema_version" in caplog.text


def test_missing_version_hand_maintained_no_error() -> None:
    """Hand-maintained missing version does not raise."""
    check_schema_version({}, "hand.yaml", hand_maintained=True)


def test_error_message_contains_source_description() -> None:
    """SchemaVersionError message includes the source_description."""
    with pytest.raises(SchemaVersionError, match=r"my_file\.yaml"):
        check_schema_version(
            {"schema_version": TOOL_SCHEMA_VERSION + 99},
            "my_file.yaml",
        )
