"""yt-dlp Python API wrapper.

One call to fetch a YouTube source's English SRT via the yt-dlp library.
Bundled ffmpeg (imageio-ffmpeg) handles subtitle format conversion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import imageio_ffmpeg
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)


class YtDlpError(RuntimeError):
    """yt-dlp failed or produced unexpected output."""


class NoSubtitlesError(YtDlpError):
    """yt-dlp ran but no English subtitles were written."""


@dataclass(frozen=True)
class FetchResult:
    """Returned from `fetch`. Durations in seconds."""

    video_id: str
    title: str
    duration: float
    srt_path: Path


def fetch(
    url: str,
    target_dir: Path,
    *,
    cookies_from_browser: str | None = None,
) -> FetchResult:
    """Fetch title, duration, and English SRT for a YouTube URL.

    Writes `<id>.en[.auto].srt` into `target_dir`.

    :param cookies_from_browser: browser name to load cookies from
        (e.g. "chrome", "firefox"); helps bypass YouTube rate limits
    :raises NoSubtitlesError: no English subtitles available
    :raises YtDlpError: download failed or info missing required fields
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    ydl_opts: dict[str, object] = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en.*", "en"],
        "subtitlesformat": "srt",
        "outtmpl": str(target_dir / "%(id)s.%(ext)s"),
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        # SABR/PO-Token gating can leave only image formats exposed; format
        # selection then aborts with "Requested format is not available"
        # *after* subtitles have already been written. Make it non-fatal.
        "ignore_no_formats_error": True,
        "quiet": True,
        "no_warnings": True,
    }
    if cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (cookies_from_browser,)
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as e:
        raise YtDlpError(str(e)) from e

    if not isinstance(info, dict):
        msg = f"yt-dlp returned no info for {url}"
        raise YtDlpError(msg)

    video_id = info.get("id")
    title = info.get("title")
    duration = info.get("duration")
    if not video_id or not title or duration is None:
        msg = f"yt-dlp info missing id/title/duration for {url}"
        raise YtDlpError(msg)

    srt_path = _pick_srt(target_dir, str(video_id))
    if srt_path is None:
        msg = (
            f"no English subtitles available for {url}. "
            "Video may lack captions or require a different language."
        )
        raise NoSubtitlesError(msg)

    return FetchResult(
        video_id=str(video_id),
        title=str(title),
        duration=float(duration),
        srt_path=srt_path,
    )


def _pick_srt(target_dir: Path, video_id: str) -> Path | None:
    # prefer manual subs (<id>.en.srt) over auto (<id>.en.auto.srt)
    preferred = target_dir / f"{video_id}.en.srt"
    if preferred.exists():
        return preferred
    candidates = sorted(target_dir.glob(f"{video_id}.en*.srt"))
    return candidates[0] if candidates else None
