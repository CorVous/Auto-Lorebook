"""Tests for commands/ingest.py."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from auto_lorebook import info_yaml, ytdlp
from auto_lorebook.commands import ingest
from tests._ytdlp_fakes import make_fake_youtubedl

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
        "cookies_from_browser": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


_SAMPLE_SRT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "hello\n"
    "\n"
    "2\n"
    "00:00:03,000 --> 00:00:04,000\n"
    "world\n"
)


def test_youtube_url_ingests_via_ytdlp(tmp_wiki: Path) -> None:
    args = _args(url_or_path="https://youtube.com/watch?v=abc12345678")
    info = {"id": "abc12345678", "title": "The Video", "duration": 7200}
    fake_ydl, _ = make_fake_youtubedl(
        info=info, subs={"abc12345678.en.srt": _SAMPLE_SRT}
    )
    with (
        patch(
            "auto_lorebook.commands.ingest.cfg_mod.load_config",
            return_value=_mock_config(tmp_wiki),
        ),
        patch("auto_lorebook.ytdlp.YoutubeDL", fake_ydl),
    ):
        result = ingest.run(args)
    assert result == 0

    source_dir = tmp_wiki / "sources" / "yt-abc12345678"
    assert (source_dir / "transcript.en.srt").exists()
    info_data = info_yaml.read(source_dir / "info.yaml")
    assert info_data.source_type == "youtube"
    assert info_data.title == "The Video"
    assert info_data.duration_seconds == 7200
    assert info_data.caption_type == "manual"
    assert info_data.source_url == "https://youtube.com/watch?v=abc12345678"


def test_non_youtube_url_errors(tmp_wiki: Path) -> None:
    args = _args(url_or_path="https://example.com/some.srt")
    with patch(
        "auto_lorebook.commands.ingest.cfg_mod.load_config",
        return_value=_mock_config(tmp_wiki),
    ):
        result = ingest.run(args)
    assert result == 1


def test_ytdlp_failure_returns_error(tmp_wiki: Path) -> None:
    args = _args(url_or_path="https://youtube.com/watch?v=abcdefghijk")
    with (
        patch(
            "auto_lorebook.commands.ingest.cfg_mod.load_config",
            return_value=_mock_config(tmp_wiki),
        ),
        patch(
            "auto_lorebook.ytdlp.fetch",
            side_effect=ytdlp.NoSubtitlesError("no subs"),
        ),
    ):
        result = ingest.run(args)
    assert result == 1


def test_first_run_triggers_interactive_setup(
    tmp_path: Path, tmp_wiki: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing config.yaml invokes interactive_setup when interactive."""
    home = tmp_path / "home"
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    src = tmp_path / "notes.txt"
    src.write_bytes(b"hello world")

    setup_mock = MagicMock(return_value=_mock_config(tmp_wiki))
    args = _args(url_or_path=str(src), no_interactive=False)
    with patch("auto_lorebook.commands.ingest.cfg_mod.interactive_setup", setup_mock):
        ingest.run(args)
    assert setup_mock.called


def test_first_run_no_interactive_propagates_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--no-interactive` surfaces the missing-config error without prompting."""
    home = tmp_path / "home"
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    src = tmp_path / "notes.txt"
    src.write_bytes(b"hello world")

    args = _args(url_or_path=str(src), no_interactive=True)
    result = ingest.run(args)
    assert result == 1
    assert not (home / "config.yaml").exists()


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
