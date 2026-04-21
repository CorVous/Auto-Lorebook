"""yt-dlp subprocess wrapper with transcript cache."""

from __future__ import annotations

import json
import subprocess  # noqa: S404
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class YtDlpResult:
    """Result of a YouTube transcript fetch."""

    srt_text: str
    title: str
    duration_seconds: float
    caption_type: str  # "manual" | "auto-generated"
    source_id: str


def _fetch_from_ytdlp(url: str, output_dir: Path) -> tuple[str, str, float, str]:
    """Invoke yt-dlp and return (srt_text, title, duration, caption_type).

    Writes subtitle + info JSON files to output_dir.

    :param url: YouTube URL
    :param output_dir: writable directory for yt-dlp output
    :raises RuntimeError: subprocess fails or no SRT produced
    """
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            "en",
            "--sub-format",
            "srt",
            "--write-info-json",
            "--no-playlist",
            "--quiet",
            "-o",
            str(output_dir / "%(id)s"),
            url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = f"yt-dlp failed (exit {result.returncode}): {result.stderr}"
        raise RuntimeError(msg)
    info_files = sorted(output_dir.glob("*.info.json"))
    if not info_files:
        msg = "yt-dlp produced no info JSON"
        raise RuntimeError(msg)
    raw_info = json.loads(info_files[0].read_text(encoding="utf-8"))
    title = str(raw_info.get("title", ""))
    duration = float(str(raw_info.get("duration", 0)))
    has_manual = bool(raw_info.get("subtitles", {}).get("en"))
    caption_type = "manual" if has_manual else "auto-generated"
    srt_files = sorted(output_dir.glob("*.en*.srt"))
    if not srt_files:
        srt_files = sorted(output_dir.glob("*.srt"))
    if not srt_files:
        msg = "yt-dlp produced no SRT subtitle file"
        raise RuntimeError(msg)
    srt_text = srt_files[0].read_text(encoding="utf-8")
    return srt_text, title, duration, caption_type


def fetch_transcript(url: str, source_id: str, sources_dir: Path) -> YtDlpResult:
    """Return transcript for url, using cached file if available.

    Prints notice on cache hit; prints caption-type message on miss.

    :param url: YouTube URL
    :param source_id: e.g. yt-abc12345
    :param sources_dir: wiki repo sources/ directory
    :raises RuntimeError: yt-dlp fails or no SRT found
    """
    transcript_path = sources_dir / source_id / "transcript.en.srt"
    meta_path = sources_dir / source_id / ".yt_meta.json"
    if transcript_path.exists() and meta_path.exists():
        print(f"Using cached transcript for {source_id}.")  # noqa: T201
        raw_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return YtDlpResult(
            srt_text=transcript_path.read_text(encoding="utf-8"),
            title=str(raw_meta.get("title", "")),
            duration_seconds=float(str(raw_meta.get("duration_seconds", 0))),
            caption_type=str(raw_meta.get("caption_type", "unknown")),
            source_id=source_id,
        )
    with tempfile.TemporaryDirectory() as tmpdir:
        srt_text, title, duration, caption_type = _fetch_from_ytdlp(url, Path(tmpdir))
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(srt_text, encoding="utf-8")
    meta: dict[str, object] = {
        "title": title,
        "duration_seconds": duration,
        "caption_type": caption_type,
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    if caption_type == "manual":
        print(f"Manual captions retrieved for {source_id}.")  # noqa: T201
    else:
        print(f"Warning: auto-generated captions for {source_id}.")  # noqa: T201
    return YtDlpResult(
        srt_text=srt_text,
        title=title,
        duration_seconds=duration,
        caption_type=caption_type,
        source_id=source_id,
    )
