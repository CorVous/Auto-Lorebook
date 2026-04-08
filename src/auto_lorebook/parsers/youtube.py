"""YouTube subtitle downloader via yt-dlp."""

from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from auto_lorebook.models import SourceMetadata

_YOUTUBE_HOSTS = frozenset({
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
})


class YouTubeSubtitleError(RuntimeError):
    """Raised when YouTube subtitles cannot be downloaded or found."""


_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _validate_youtube_url(url: str) -> None:
    """Raise ValueError if url is not a recognised YouTube URL.

    Validates both the scheme (http/https only) and the host against the
    YouTube domain whitelist to prevent SSRF via exotic schemes.

    :param url: URL string to validate.
    :raises ValueError: If url is not a YouTube URL.
    """
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.netloc.lower()
    except Exception:  # noqa: BLE001
        scheme = ""
        host = ""
    if scheme not in _ALLOWED_SCHEMES or host not in _YOUTUBE_HOSTS:
        msg = f"Not a YouTube URL: {url!r}"
        raise ValueError(msg)


def _yt_dlp_download(url: str, out_dir: Path, lang: str) -> None:
    """Invoke yt-dlp to download subtitles into out_dir.

    Downloads both manual and auto-generated subtitles in SRT format.

    :param url: YouTube URL.
    :param out_dir: Directory to write subtitle files into.
    :param lang: Subtitle language code.
    """
    ydl_opts = {
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [lang],
        "subtitlesformat": "srt",
        "skip_download": True,
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


def fetch_youtube_transcript(
    url: str,
    *,
    lang: str = "en",
) -> tuple[str, SourceMetadata]:
    """Download YouTube subtitles and return SRT content with source metadata.

    Downloads manual subtitles if available, falling back to auto-generated
    captions. The returned SRT content can be passed directly to
    ``parse_srt()`` for further processing.

    :param url: YouTube video URL (youtube.com or youtu.be).
    :param lang: Subtitle language code (default: ``"en"``).
    :return: Tuple of ``(srt_content, SourceMetadata)``.
    :raises ValueError: If the URL is not a recognised YouTube URL.
    :raises YouTubeSubtitleError: If no subtitles are found or download fails.
    """
    _validate_youtube_url(url)

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        _yt_dlp_download(url, out_dir, lang)

        # yt-dlp names subtitle files as VIDEO_ID.LANG.srt
        srt_files = sorted(out_dir.glob(f"*.{lang}.srt"))
        if not srt_files:
            # Fallback: any .srt file in the directory
            srt_files = sorted(out_dir.glob("*.srt"))
        if not srt_files:
            msg = f"No {lang!r} subtitles found for {url!r}"
            raise YouTubeSubtitleError(msg)

        content = srt_files[0].read_text(encoding="utf-8")
        filename = srt_files[0].name

    return content, SourceMetadata(filename=filename, source_url=url)
