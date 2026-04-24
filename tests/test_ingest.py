"""Tests for commands/ingest.py."""

from __future__ import annotations

import argparse
import json
import subprocess  # noqa: S404
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from auto_lorebook import info_yaml, ytdlp
from auto_lorebook.commands import ingest

if TYPE_CHECKING:
    from collections.abc import Callable
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


_SAMPLE_SRT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "hello\n"
    "\n"
    "2\n"
    "00:00:03,000 --> 00:00:04,000\n"
    "world\n"
)


def _fake_yt_run(
    video_id: str, title: str, duration: float
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Patch target for subprocess.run that fakes yt-dlp output."""

    def side_effect(
        cmd: list[str], cwd: Path, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        info = {"id": video_id, "title": title, "duration": duration}
        (cwd / f"{video_id}.info.json").write_text(json.dumps(info), encoding="utf-8")
        (cwd / f"{video_id}.en.srt").write_text(_SAMPLE_SRT, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return side_effect


def test_youtube_url_ingests_via_ytdlp(tmp_wiki: Path) -> None:
    args = _args(url_or_path="https://youtube.com/watch?v=abc12345678")
    with (
        patch(
            "auto_lorebook.commands.ingest.cfg_mod.load_config",
            return_value=_mock_config(tmp_wiki),
        ),
        patch(
            "auto_lorebook.ytdlp.subprocess.run",
            side_effect=_fake_yt_run("abc12345678", "The Video", 7200),
        ),
    ):
        result = ingest.run(args)
    assert result == 0

    source_dir = tmp_wiki / "sources" / "yt-abc12345678"
    assert (source_dir / "transcript.en.srt").exists()
    info = info_yaml.read(source_dir / "info.yaml")
    assert info.source_type == "youtube"
    assert info.title == "The Video"
    assert info.duration_seconds == 7200
    assert info.caption_type == "manual"
    assert info.source_url == "https://youtube.com/watch?v=abc12345678"


def test_non_youtube_url_errors(tmp_wiki: Path) -> None:
    args = _args(url_or_path="https://example.com/some.srt")
    with patch(
        "auto_lorebook.commands.ingest.cfg_mod.load_config",
        return_value=_mock_config(tmp_wiki),
    ):
        result = ingest.run(args)
    assert result == 1


def test_ytdlp_missing_returns_error(tmp_wiki: Path) -> None:
    args = _args(url_or_path="https://youtube.com/watch?v=abcdefghijk")
    with (
        patch(
            "auto_lorebook.commands.ingest.cfg_mod.load_config",
            return_value=_mock_config(tmp_wiki),
        ),
        patch(
            "auto_lorebook.ytdlp.subprocess.run",
            side_effect=FileNotFoundError("yt-dlp"),
        ),
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
