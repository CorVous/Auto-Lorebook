"""yt-dlp subprocess wrapper.

One call to fetch a YouTube source's info JSON and English SRT. The
wrapper is intentionally thin — it shells out, surfaces errors with
actionable messages, and returns a small dataclass.
"""

from __future__ import annotations

import json
import logging
import subprocess  # noqa: S404
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)

_YT_DLP = "yt-dlp"


class YtDlpError(RuntimeError):
    """yt-dlp failed or produced unexpected output."""


class YtDlpNotFoundError(YtDlpError):
    """yt-dlp binary not installed / not on PATH."""


class NoSubtitlesError(YtDlpError):
    """yt-dlp ran but no English subtitles were written."""


@dataclass(frozen=True)
class FetchResult:
    """Returned from `fetch`. Durations in seconds."""

    video_id: str
    title: str
    duration: float
    srt_path: Path
    info_json_path: Path


def fetch(url: str, target_dir: Path) -> FetchResult:
    """Fetch title, duration, and English SRT for a YouTube URL.

    Writes `<id>.info.json` and `<id>.en[.auto].srt` into `target_dir`.

    :raises YtDlpNotFoundError: yt-dlp binary missing
    :raises NoSubtitlesError: no English subtitles available
    :raises YtDlpError: subprocess non-zero exit or malformed output
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        _YT_DLP,
        "--skip-download",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        "en.*,en",
        "--convert-subs",
        "srt",
        "-o",
        "%(id)s.%(ext)s",
        url,
    ]
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=target_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        msg = (
            "yt-dlp not found on PATH. Install it "
            "(e.g. `pipx install yt-dlp`) and retry."
        )
        raise YtDlpNotFoundError(msg) from e

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() or "(no stderr)"
        msg = f"yt-dlp exited {proc.returncode}: {stderr}"
        raise YtDlpError(msg)

    info_path = _find_info_json(target_dir)
    if info_path is None:
        msg = "yt-dlp succeeded but no *.info.json was written"
        raise YtDlpError(msg)
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        msg = f"could not read {info_path.name}: {e}"
        raise YtDlpError(msg) from e

    video_id = info.get("id")
    title = info.get("title")
    duration = info.get("duration")
    if not video_id or not title or duration is None:
        msg = f"info.json missing id/title/duration: {info_path.name}"
        raise YtDlpError(msg)

    srt_path = _pick_srt(target_dir, video_id)
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
        info_json_path=info_path,
    )


def _find_info_json(target_dir: Path) -> Path | None:
    matches = sorted(target_dir.glob("*.info.json"))
    return matches[0] if matches else None


def _pick_srt(target_dir: Path, video_id: str) -> Path | None:
    # prefer manual subs (<id>.en.srt) over auto (<id>.en.auto.srt)
    preferred = target_dir / f"{video_id}.en.srt"
    if preferred.exists():
        return preferred
    candidates = sorted(target_dir.glob(f"{video_id}.en*.srt"))
    return candidates[0] if candidates else None
