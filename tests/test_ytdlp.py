"""Tests for ytdlp.py Python-API wrapper."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import imageio_ffmpeg
import pytest
from yt_dlp.utils import DownloadError

from auto_lorebook.ytdlp import NoSubtitlesError, YtDlpError, fetch
from tests._ytdlp_fakes import make_fake_youtubedl

if TYPE_CHECKING:
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


class TestFetch:
    def test_success(self, tmp_path: Path) -> None:
        info = {"id": "vid_ok", "title": "My Video", "duration": 123}
        fake_ydl, _ = make_fake_youtubedl(
            info=info, subs={"vid_ok.en.srt": _SAMPLE_SRT}
        )
        with patch("auto_lorebook.ytdlp.YoutubeDL", fake_ydl):
            result = fetch("https://youtu.be/vid_ok", tmp_path)
        assert result.video_id == "vid_ok"
        assert result.title == "My Video"
        assert result.duration == pytest.approx(123.0)
        assert result.srt_path.exists()
        assert result.srt_path.read_text(encoding="utf-8") == _SAMPLE_SRT

    def test_download_error_raises_ytdlp_error(self, tmp_path: Path) -> None:
        fake_ydl, _ = make_fake_youtubedl(
            info=None,
            raises=DownloadError("ERROR: video unavailable"),
        )
        with (
            patch("auto_lorebook.ytdlp.YoutubeDL", fake_ydl),
            pytest.raises(YtDlpError, match="video unavailable"),
        ):
            fetch("https://youtu.be/bad", tmp_path)

    def test_extract_info_returns_none_raises(self, tmp_path: Path) -> None:
        fake_ydl, _ = make_fake_youtubedl(info=None)
        with (
            patch("auto_lorebook.ytdlp.YoutubeDL", fake_ydl),
            pytest.raises(YtDlpError),
        ):
            fetch("https://youtu.be/x", tmp_path)

    def test_extract_info_missing_fields_raises(self, tmp_path: Path) -> None:
        fake_ydl, _ = make_fake_youtubedl(info={"id": "x"})  # no title/duration
        with (
            patch("auto_lorebook.ytdlp.YoutubeDL", fake_ydl),
            pytest.raises(YtDlpError),
        ):
            fetch("https://youtu.be/x", tmp_path)

    def test_no_subtitles_written(self, tmp_path: Path) -> None:
        info = {"id": "v1", "title": "T", "duration": 10}
        fake_ydl, _ = make_fake_youtubedl(info=info)  # no subs written
        with (
            patch("auto_lorebook.ytdlp.YoutubeDL", fake_ydl),
            pytest.raises(NoSubtitlesError),
        ):
            fetch("https://youtu.be/v1", tmp_path)

    def test_float_duration(self, tmp_path: Path) -> None:
        info = {"id": "vf", "title": "T", "duration": 123.5}
        fake_ydl, _ = make_fake_youtubedl(info=info, subs={"vf.en.srt": _SAMPLE_SRT})
        with patch("auto_lorebook.ytdlp.YoutubeDL", fake_ydl):
            result = fetch("https://youtu.be/vf", tmp_path)
        assert result.duration == pytest.approx(123.5)

    def test_prefers_manual_subs_over_auto(self, tmp_path: Path) -> None:
        info = {"id": "vm", "title": "T", "duration": 5}
        fake_ydl, _ = make_fake_youtubedl(
            info=info,
            subs={"vm.en.srt": "manual", "vm.en.auto.srt": "auto"},
        )
        with patch("auto_lorebook.ytdlp.YoutubeDL", fake_ydl):
            result = fetch("https://youtu.be/vm", tmp_path)
        assert result.srt_path.name == "vm.en.srt"

    def test_ydl_opts_include_bundled_ffmpeg(self, tmp_path: Path) -> None:
        info = {"id": "vff", "title": "T", "duration": 10}
        fake_ydl, captured_opts = make_fake_youtubedl(
            info=info, subs={"vff.en.srt": _SAMPLE_SRT}
        )
        with patch("auto_lorebook.ytdlp.YoutubeDL", fake_ydl):
            fetch("https://youtu.be/vff", tmp_path)
        assert captured_opts["ffmpeg_location"] == imageio_ffmpeg.get_ffmpeg_exe()

    def test_cookies_from_browser_passed_through(self, tmp_path: Path) -> None:
        info = {"id": "vc", "title": "T", "duration": 10}
        fake_ydl, captured_opts = make_fake_youtubedl(
            info=info, subs={"vc.en.srt": _SAMPLE_SRT}
        )
        with patch("auto_lorebook.ytdlp.YoutubeDL", fake_ydl):
            fetch("https://youtu.be/vc", tmp_path, cookies_from_browser="firefox")
        assert captured_opts["cookiesfrombrowser"] == ("firefox",)

    def test_cookies_from_browser_omitted_by_default(self, tmp_path: Path) -> None:
        info = {"id": "vd", "title": "T", "duration": 10}
        fake_ydl, captured_opts = make_fake_youtubedl(
            info=info, subs={"vd.en.srt": _SAMPLE_SRT}
        )
        with patch("auto_lorebook.ytdlp.YoutubeDL", fake_ydl):
            fetch("https://youtu.be/vd", tmp_path)
        assert "cookiesfrombrowser" not in captured_opts
