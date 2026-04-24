"""Tests for schema.py."""

from __future__ import annotations

import pytest

from auto_lorebook.schema import SchemaVersionError, read_schema_version


def test_present_version_returns_value() -> None:
    assert read_schema_version({"schema_version": 1}, "test.yaml", max_supported=1) == 1


def test_present_version_within_ceiling() -> None:
    assert read_schema_version({"schema_version": 2}, "test.yaml", max_supported=5) == 2


def test_missing_version_raises() -> None:
    with pytest.raises(SchemaVersionError, match="missing schema_version"):
        read_schema_version({}, "test.yaml", max_supported=1)


def test_non_int_version_raises() -> None:
    with pytest.raises(SchemaVersionError, match="positive integer"):
        read_schema_version({"schema_version": "1"}, "test.yaml", max_supported=1)


def test_zero_version_raises() -> None:
    with pytest.raises(SchemaVersionError, match="positive integer"):
        read_schema_version({"schema_version": 0}, "test.yaml", max_supported=1)


def test_float_version_raises() -> None:
    with pytest.raises(SchemaVersionError, match="positive integer"):
        read_schema_version({"schema_version": 1.0}, "test.yaml", max_supported=1)


def test_future_version_raises() -> None:
    with pytest.raises(SchemaVersionError, match="exceeds max supported"):
        read_schema_version({"schema_version": 2}, "test.yaml", max_supported=1)


def test_ceiling_per_type() -> None:
    # Different file types can have different ceilings.
    assert read_schema_version({"schema_version": 3}, "a.yaml", max_supported=3) == 3
    with pytest.raises(SchemaVersionError):
        read_schema_version({"schema_version": 3}, "b.yaml", max_supported=2)


def test_file_label_in_error() -> None:
    with pytest.raises(SchemaVersionError, match=r"myfile\.yaml"):
        read_schema_version({}, "myfile.yaml", max_supported=1)
