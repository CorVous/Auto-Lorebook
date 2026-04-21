"""Tests for yt-dlp subprocess wrapper."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from auto_lorebook.sources.ytdlp import (
    YtDlpResult,
    _fetch_from_ytdlp,  # noqa: PLC2701
    fetch_transcript,
)

if TYPE_CHECKING:
    from pathlib import Path

_URL = "https://www.youtube.com/watch?v=testId1234"
_SOURCE_ID = "yt-testId1234"


def _make_info_json(
    title: str = "Test", duration: float = 300.0, *, manual: bool = True
) -> str:
    subs: dict[str, object] = {"en": [{"ext": "srt"}]} if manual else {}
    return json.dumps({"title": title, "duration": duration, "subtitles": subs})


def _create_fake_ytdlp_output(output_dir: Path, *, manual: bool = True) -> None:
    """Write fake yt-dlp output files in output_dir."""
    (output_dir / "testId1234.en.srt").write_text(
        "1\n00:00:00,000 --> 00:00:05,000\nHello\n", encoding="utf-8"
    )
    (output_dir / "testId1234.info.json").write_text(
        _make_info_json(manual=manual), encoding="utf-8"
    )


# ── fetch_transcript ────────────────────────────────────────────────────────


def test_fetch_transcript_cache_hit_returns_cached(tmp_path: Path) -> None:
    """Cache hit: cached transcript returned without calling _fetch_from_ytdlp."""
    sources_dir = tmp_path / "sources"
    src_dir = sources_dir / _SOURCE_ID
    src_dir.mkdir(parents=True)
    (src_dir / "transcript.en.srt").write_text("cached srt", encoding="utf-8")
    (src_dir / ".yt_meta.json").write_text(
        json.dumps({
            "title": "Cached",
            "duration_seconds": 100.0,
            "caption_type": "manual",
        }),
        encoding="utf-8",
    )
    with patch("auto_lorebook.sources.ytdlp._fetch_from_ytdlp") as mock_fetch:
        result = fetch_transcript(_URL, _SOURCE_ID, sources_dir)
    mock_fetch.assert_not_called()
    assert result.srt_text == "cached srt"
    assert result.title == "Cached"
    assert result.source_id == _SOURCE_ID


def test_fetch_transcript_cache_hit_prints_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cache hit: prints Using cached transcript notice."""
    sources_dir = tmp_path / "sources"
    src_dir = sources_dir / _SOURCE_ID
    src_dir.mkdir(parents=True)
    (src_dir / "transcript.en.srt").write_text("x", encoding="utf-8")
    (src_dir / ".yt_meta.json").write_text(
        json.dumps({"title": "T", "duration_seconds": 1.0, "caption_type": "manual"}),
        encoding="utf-8",
    )
    with patch("auto_lorebook.sources.ytdlp._fetch_from_ytdlp"):
        fetch_transcript(_URL, _SOURCE_ID, sources_dir)
    assert f"Using cached transcript for {_SOURCE_ID}" in capsys.readouterr().out


def test_fetch_transcript_cache_miss_calls_fetch(tmp_path: Path) -> None:
    """Cache miss: _fetch_from_ytdlp is invoked."""
    sources_dir = tmp_path / "sources"
    with patch("auto_lorebook.sources.ytdlp._fetch_from_ytdlp") as mock_fetch:
        mock_fetch.return_value = ("srt", "Title", 300.0, "manual")
        fetch_transcript(_URL, _SOURCE_ID, sources_dir)
    mock_fetch.assert_called_once()


def test_fetch_transcript_cache_miss_writes_transcript(tmp_path: Path) -> None:
    """Cache miss: transcript written to sources/<source_id>/transcript.en.srt."""
    sources_dir = tmp_path / "sources"
    with patch("auto_lorebook.sources.ytdlp._fetch_from_ytdlp") as mock_fetch:
        mock_fetch.return_value = ("srt content", "Title", 300.0, "manual")
        fetch_transcript(_URL, _SOURCE_ID, sources_dir)
    transcript_path = sources_dir / _SOURCE_ID / "transcript.en.srt"
    assert transcript_path.read_text(encoding="utf-8") == "srt content"


def test_fetch_transcript_cache_miss_writes_meta(tmp_path: Path) -> None:
    """Cache miss: .yt_meta.json written alongside transcript."""
    sources_dir = tmp_path / "sources"
    with patch("auto_lorebook.sources.ytdlp._fetch_from_ytdlp") as mock_fetch:
        mock_fetch.return_value = ("srt", "My Title", 500.0, "auto-generated")
        fetch_transcript(_URL, _SOURCE_ID, sources_dir)
    meta_path = sources_dir / _SOURCE_ID / ".yt_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["title"] == "My Title"
    assert meta["duration_seconds"] == pytest.approx(500.0)


def test_fetch_transcript_returns_ytdlp_result(tmp_path: Path) -> None:
    """fetch_transcript returns a YtDlpResult."""
    sources_dir = tmp_path / "sources"
    with patch("auto_lorebook.sources.ytdlp._fetch_from_ytdlp") as mock_fetch:
        mock_fetch.return_value = ("srt", "T", 1.0, "manual")
        result = fetch_transcript(_URL, _SOURCE_ID, sources_dir)
    assert isinstance(result, YtDlpResult)
    assert result.source_id == _SOURCE_ID


# ── _fetch_from_ytdlp ───────────────────────────────────────────────────────


def test_fetch_from_ytdlp_reads_output_files(tmp_path: Path) -> None:
    """_fetch_from_ytdlp reads SRT and info JSON from output_dir."""
    _create_fake_ytdlp_output(tmp_path)
    with patch("auto_lorebook.sources.ytdlp.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        srt, title, duration, caption_type = _fetch_from_ytdlp(_URL, tmp_path)
    assert "Hello" in srt
    assert title == "Test"
    assert duration == pytest.approx(300.0)
    assert caption_type == "manual"


def test_fetch_from_ytdlp_auto_caption_type(tmp_path: Path) -> None:
    """caption_type is 'auto-generated' when no manual subtitles."""
    _create_fake_ytdlp_output(tmp_path, manual=False)
    with patch("auto_lorebook.sources.ytdlp.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _, _, _, caption_type = _fetch_from_ytdlp(_URL, tmp_path)
    assert caption_type == "auto-generated"


def test_fetch_from_ytdlp_failure_raises(tmp_path: Path) -> None:
    """Non-zero yt-dlp exit raises RuntimeError."""
    with patch("auto_lorebook.sources.ytdlp.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="some error")
        with pytest.raises(RuntimeError, match="yt-dlp failed"):
            _fetch_from_ytdlp(_URL, tmp_path)


def test_fetch_from_ytdlp_no_srt_raises(tmp_path: Path) -> None:
    """No SRT file in output_dir raises RuntimeError."""
    (tmp_path / "testId1234.info.json").write_text(_make_info_json(), encoding="utf-8")
    with patch("auto_lorebook.sources.ytdlp.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with pytest.raises(RuntimeError, match="no SRT"):
            _fetch_from_ytdlp(_URL, tmp_path)
