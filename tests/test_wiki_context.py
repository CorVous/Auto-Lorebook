"""Tests for wiki_context.py."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

from auto_lorebook.wiki_context import WikiContext, read


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    wc = read(tmp_path / ".wiki-context.yaml")
    assert wc.setting.name is None
    assert wc.naming_conventions is None


def test_empty_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / ".wiki-context.yaml"
    path.write_text("", encoding="utf-8")
    wc = read(path)
    assert wc.setting.name is None


def test_schema_version_only(tmp_path: Path) -> None:
    path = tmp_path / ".wiki-context.yaml"
    path.write_text("schema_version: 1\n", encoding="utf-8")
    wc = read(path)
    assert isinstance(wc, WikiContext)
    assert wc.setting.name is None


def test_missing_schema_version_logs_warning_and_reads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / ".wiki-context.yaml"
    path.write_text("setting:\n  name: Aether Chronicles\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        wc = read(path)
    assert wc.setting.name == "Aether Chronicles"
    assert "missing schema_version" in caplog.text


def test_full_file(tmp_path: Path) -> None:
    path = tmp_path / ".wiki-context.yaml"
    path.write_text(
        "schema_version: 1\n"
        "setting:\n"
        "  name: Aether Chronicles\n"
        "  description: A high-fantasy setting.\n"
        "naming_conventions: 'Characters by first name'\n"
        "interpretation_defaults: 'DM is authoritative'\n"
        "recurring_speakers:\n"
        "  - name: Cor\n"
        "    role: player\n",
        encoding="utf-8",
    )
    wc = read(path)
    assert wc.setting.name == "Aether Chronicles"
    assert wc.setting.description == "A high-fantasy setting."
    assert wc.naming_conventions == "Characters by first name"
    assert wc.interpretation_defaults == "DM is authoritative"
    assert len(wc.recurring_speakers) == 1
    assert wc.recurring_speakers[0]["name"] == "Cor"


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    path = tmp_path / ".wiki-context.yaml"
    path.write_text("schema_version: 1\nfuture_field: something\n", encoding="utf-8")
    wc = read(path)
    assert isinstance(wc, WikiContext)


def test_malformed_yaml_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / ".wiki-context.yaml"
    path.write_text(":::invalid:::\n", encoding="utf-8")
    wc = read(path)
    assert wc.setting.name is None
