"""Tests for commands/ingest.py."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from auto_lorebook.commands import ingest

if TYPE_CHECKING:
    from pathlib import Path


def _mock_config(wiki: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.wiki_repo_path = wiki
    cfg.models.primary_context_window = 200000
    cfg.preamble.budget_fraction = 0.8
    return cfg


def _args(**kwargs: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "url_or_path": "notes.txt",
        "source_url": None,
        "source_id": None,
        "session_date": None,
        "perspective": None,
        "source_nature": None,
        "setting": None,
        "notes": None,
        "no_interactive": True,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def test_url_with_source_id_still_errors(tmp_wiki: Path) -> None:
    """URL positional always errors — --source-id is not a fetch bypass."""
    args = _args(
        url_or_path="https://youtube.com/watch?v=abc123", source_id="yt-abc123"
    )
    with patch(
        "auto_lorebook.commands.ingest.cfg_mod.load_config",
        return_value=_mock_config(tmp_wiki),
    ):
        result = ingest.run(args)
    assert result == 1


def test_unreadable_info_yaml_returns_error(tmp_path: Path, tmp_wiki: Path) -> None:
    """Corrupt info.yaml causes ingest to return 1, not silently recreate."""
    src = tmp_path / "notes.txt"
    src.write_bytes(b"hello world")

    source_id = "txt-testid0001"
    source_dir = tmp_wiki / "sources" / source_id
    source_dir.mkdir(parents=True)
    # No transcript.txt here so copy_transcript will proceed.
    # info.yaml has future schema_version → InfoError on read.
    (source_dir / "info.yaml").write_text(
        "schema_version: 99\nsource_id: x\nsource_type: text\nfetched_at: now\n",
        encoding="utf-8",
    )

    args = _args(url_or_path=str(src), source_id=source_id)
    with patch(
        "auto_lorebook.commands.ingest.cfg_mod.load_config",
        return_value=_mock_config(tmp_wiki),
    ):
        result = ingest.run(args)
    assert result == 1
