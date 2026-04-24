"""Tests for ytdlp.py subprocess wrapper."""

from __future__ import annotations

import json
import subprocess  # noqa: S404
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from auto_lorebook.ytdlp import (
    NoSubtitlesError,
    YtDlpError,
    YtDlpNotFoundError,
    fetch,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_SAMPLE_SRT = (
    "1\n"
    "00:00:01,000 --> 00:00:02,000\n"
    "hello\n"
    "\n"
    "2\n"
    "00:00:03,000 --> 00:00:04,000\n"
    "world\n"
)


def _fake_run_ok(
    tmp_path: Path, video_id: str = "abc123"
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a patched subprocess.run that simulates a successful fetch."""
    info = {"id": video_id, "title": "My Video", "duration": 123}

    def side_effect(
        cmd: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        # yt-dlp writes info.json and .en.srt into cwd (the target_dir)
        (tmp_path / f"{video_id}.info.json").write_text(
            json.dumps(info), encoding="utf-8"
        )
        (tmp_path / f"{video_id}.en.srt").write_text(_SAMPLE_SRT, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return side_effect


class TestFetch:
    def test_success(self, tmp_path: Path) -> None:
        with patch(
            "auto_lorebook.ytdlp.subprocess.run",
            side_effect=_fake_run_ok(tmp_path, "vid_ok"),
        ):
            result = fetch("https://youtu.be/vid_ok", tmp_path)
        assert result.video_id == "vid_ok"
        assert result.title == "My Video"
        assert result.duration == pytest.approx(123.0)
        assert result.srt_path.exists()
        assert result.srt_path.read_text(encoding="utf-8") == _SAMPLE_SRT

    def test_yt_dlp_not_installed(self, tmp_path: Path) -> None:
        with (
            patch(
                "auto_lorebook.ytdlp.subprocess.run",
                side_effect=FileNotFoundError("yt-dlp"),
            ),
            pytest.raises(YtDlpNotFoundError),
        ):
            fetch("https://youtu.be/x", tmp_path)

    def test_subprocess_nonzero_exit(self, tmp_path: Path) -> None:
        with (
            patch(
                "auto_lorebook.ytdlp.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 1, stdout="", stderr="ERROR: video unavailable"
                ),
            ),
            pytest.raises(YtDlpError, match="video unavailable"),
        ):
            fetch("https://youtu.be/bad", tmp_path)

    def test_no_subtitles_written(self, tmp_path: Path) -> None:
        info = {"id": "v1", "title": "T", "duration": 10}

        def side_effect(
            cmd: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            (tmp_path / "v1.info.json").write_text(json.dumps(info), encoding="utf-8")
            # no .srt file
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with (
            patch("auto_lorebook.ytdlp.subprocess.run", side_effect=side_effect),
            pytest.raises(NoSubtitlesError),
        ):
            fetch("https://youtu.be/v1", tmp_path)

    def test_info_json_missing(self, tmp_path: Path) -> None:
        def side_effect(
            cmd: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with (
            patch("auto_lorebook.ytdlp.subprocess.run", side_effect=side_effect),
            pytest.raises(YtDlpError),
        ):
            fetch("https://youtu.be/nope", tmp_path)

    def test_float_duration(self, tmp_path: Path) -> None:
        info = {"id": "vf", "title": "T", "duration": 123.5}

        def side_effect(
            cmd: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            (tmp_path / "vf.info.json").write_text(json.dumps(info), encoding="utf-8")
            (tmp_path / "vf.en.srt").write_text(_SAMPLE_SRT, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("auto_lorebook.ytdlp.subprocess.run", side_effect=side_effect):
            result = fetch("https://youtu.be/vf", tmp_path)
        assert result.duration == pytest.approx(123.5)

    def test_prefers_manual_subs_over_auto(self, tmp_path: Path) -> None:
        info = {"id": "vm", "title": "T", "duration": 5}

        def side_effect(
            cmd: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            (tmp_path / "vm.info.json").write_text(json.dumps(info), encoding="utf-8")
            # yt-dlp writes both; wrapper prefers the non-auto one
            (tmp_path / "vm.en.srt").write_text("manual", encoding="utf-8")
            (tmp_path / "vm.en.auto.srt").write_text("auto", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("auto_lorebook.ytdlp.subprocess.run", side_effect=side_effect):
            result = fetch("https://youtu.be/vm", tmp_path)
        assert result.srt_path.name == "vm.en.srt"
