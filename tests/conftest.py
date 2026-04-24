"""Shared test fixtures and configuration."""

from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def package_name() -> str:
    """Return the package name (snake_case for imports)."""
    return __package__.split(".")[0] if __package__ else "python_template"


@pytest.fixture
def cli_name() -> str:
    """Return the CLI command name (kebab-case)."""
    # Dynamically get from package metadata if possible
    try:
        return importlib.metadata.metadata("python-template")["Name"]
    except (importlib.metadata.PackageNotFoundError, KeyError):
        return "python-template"


@pytest.fixture
def tmp_wiki(tmp_path: Path) -> Path:
    """Wiki repo with schema_version stubs and empty entity dirs."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / ".wiki-context.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (wiki / ".transcription-corrections.yaml").write_text(
        "schema_version: 1\n", encoding="utf-8"
    )
    for cat in ("characters", "locations", "factions", "events", "items", "concepts"):
        (wiki / cat).mkdir()
    return wiki
