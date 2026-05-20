"""Shared test fixtures and configuration."""

from __future__ import annotations

import importlib.metadata
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Generator
    from pathlib import Path


_LIVE_OPT = "--run-live"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register --run-live to opt in to live integration tests."""
    parser.addoption(
        _LIVE_OPT,
        action="store_true",
        default=False,
        help="run @pytest.mark.live tests (real external services; costs money)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip @pytest.mark.live tests unless --run-live passed."""
    if config.getoption(_LIVE_OPT):
        return
    skip_live = pytest.mark.skip(reason=f"need {_LIVE_OPT} to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


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
def db_conn() -> Generator[sqlite3.Connection]:
    """In-memory wiki.db for entity tests; closed after each test.

    Yields:
        open in-memory connection.

    """
    from auto_lorebook import db  # noqa: PLC0415

    conn = db.open(":memory:")
    yield conn
    conn.close()


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
