"""auto-lorebook ingest subcommand."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import info_yaml, source_store, ytdlp
from auto_lorebook import source_id as sid_mod
from auto_lorebook.commands._shared import finalize_context
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    import argparse

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the ingest subcommand."""
    parser = subparsers.add_parser(
        "ingest",
        parents=[common_parser],
        help="Ingest a source (local file or YouTube URL)",
        description=(
            "Fetch a source, gather context, and store it in the wiki. "
            "Local files (.srt, .txt, .md) are supported. "
            "YouTube URLs are fetched via yt-dlp (bundled)."
        ),
    )
    parser.add_argument(
        "url_or_path",
        help="Local file path (.srt/.txt/.md) or YouTube URL",
    )
    parser.add_argument(
        "--source-url",
        dest="source_url",
        default=None,
        help="Source URL (required when positional is a local file from a URL)",
    )
    parser.add_argument(
        "--source-id",
        dest="source_id",
        default=None,
        help="Explicit source ID override",
    )
    parser.add_argument(
        "--session-date",
        dest="session_date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Session date",
    )
    parser.add_argument(
        "--perspective",
        dest="perspective",
        default=None,
        help="Perspective (e.g. 'Cor playing Kiki')",
    )
    parser.add_argument(
        "--source-nature",
        dest="source_nature",
        default=None,
        help=(
            "Source nature "
            "(actual-play/dm-lore/worldbuilding-video/interview/notes/other)"
        ),
    )
    parser.add_argument(
        "--setting",
        dest="setting",
        default=None,
        help="Setting name",
    )
    parser.add_argument(
        "--notes",
        dest="notes",
        default=None,
        help="One-line notes for this source",
    )
    parser.add_argument(
        "--no-interactive",
        dest="no_interactive",
        action="store_true",
        default=False,
        help="Skip all interactive prompts; use flags only",
    )
    parser.add_argument(
        "--cookies-from-browser",
        dest="cookies_from_browser",
        default=None,
        metavar="BROWSER",
        help=(
            "Load cookies from this browser for YouTube fetches "
            "(e.g. chrome, firefox, safari, edge, brave). Helps avoid "
            "HTTP 429 rate limits."
        ),
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the ingest command."""
    try:
        cfg = _load_or_create_config(no_interactive=args.no_interactive)
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1
    except KeyboardInterrupt:
        return 130

    wiki_repo = cfg.resolve_active_wiki(None)

    if args.url_or_path.startswith(("http://", "https://")):
        return _run_from_url(args, cfg, wiki_repo)
    return _run_from_local(args, cfg, wiki_repo)


def _load_or_create_config(*, no_interactive: bool) -> cfg_mod.Config:
    try:
        return cfg_mod.load_config()
    except cfg_mod.MissingConfigError:
        if no_interactive:
            raise
        return cfg_mod.interactive_setup()


@dataclass
class _ResolvedSource:
    """Local file + initial metadata produced by resolving the positional arg."""

    local_path: Path
    source_url: str | None
    source_type: str
    fetched_title: str | None = None
    fetched_duration: float | None = None
    caption_type: str | None = None


def _run_from_local(
    args: argparse.Namespace,
    cfg: cfg_mod.Config,
    wiki_repo: Path,
) -> int:
    try:
        source_id = sid_mod.derive(args.url_or_path, args.source_id, args.source_url)
    except sid_mod.SourceIdError as e:
        _logger.error("%s", e)
        return 1

    local_path = Path(args.url_or_path)
    if not local_path.exists():
        _logger.error("File not found: %s", local_path)
        return 1

    resolved = _ResolvedSource(
        local_path=local_path,
        source_url=args.source_url,
        source_type=_derive_source_type(local_path, args.source_url),
    )
    return _store_and_finalize(args, cfg, wiki_repo, source_id, resolved)


def _run_from_url(
    args: argparse.Namespace,
    cfg: cfg_mod.Config,
    wiki_repo: Path,
) -> int:
    video_id = sid_mod.extract_video_id(args.url_or_path)
    if not video_id:
        _logger.error(
            "Only YouTube URLs are supported for remote ingest. "
            "Download non-YouTube sources manually and pass the local file."
        )
        return 1

    source_id = args.source_id or f"yt-{video_id}"

    with tempfile.TemporaryDirectory(prefix="auto-lorebook-yt-") as tmp:
        try:
            fetched = ytdlp.fetch(
                args.url_or_path,
                Path(tmp),
                cookies_from_browser=args.cookies_from_browser,
            )
        except ytdlp.YtDlpError as e:
            _logger.error("%s", e)
            return 1

        caption_type = "auto" if ".auto." in fetched.srt_path.name else "manual"
        resolved = _ResolvedSource(
            local_path=fetched.srt_path,
            source_url=args.url_or_path,
            source_type="youtube",
            fetched_title=fetched.title,
            fetched_duration=fetched.duration,
            caption_type=caption_type,
        )
        return _store_and_finalize(args, cfg, wiki_repo, source_id, resolved)


def _store_and_finalize(
    args: argparse.Namespace,
    cfg: cfg_mod.Config,
    wiki_repo: Path,
    source_id: str,
    resolved: _ResolvedSource,
) -> int:
    try:
        dest, transcript_filename = source_store.copy_transcript(
            resolved.local_path, source_id, resolved.source_type, wiki_repo
        )
    except (source_store.DuplicateSourceError, source_store.CollisionError) as e:
        _logger.error("%s", e)
        return 2

    _logger.info("Transcript stored as %s", dest)

    info_path = wiki_repo / "sources" / source_id / "info.yaml"
    if info_path.exists():
        try:
            info = info_yaml.read(info_path)
        except info_yaml.InfoError as e:
            _logger.error(
                "Existing info.yaml is unreadable (%s). "
                "Fix or delete it, then re-run `ingest`.",
                e,
            )
            return 1
    else:
        info = _new_info(source_id, resolved, args, transcript_filename)

    info.transcript_filename = transcript_filename

    return finalize_context(info, info_path, cfg, args)


def _derive_source_type(local_path: Path, source_url: str | None) -> str:
    if source_url and sid_mod.extract_video_id(source_url):
        return "youtube"
    suffix = local_path.suffix.lower()
    if suffix == ".srt":
        return "srt"
    if suffix == ".md":
        return "markdown"
    return "text"


def _new_info(
    source_id: str,
    resolved: _ResolvedSource,
    args: argparse.Namespace,
    transcript_filename: str,
) -> info_yaml.Info:
    fetched_at = format_iso_now()
    title = resolved.fetched_title or Path(args.url_or_path).stem
    duration = (
        int(resolved.fetched_duration)
        if resolved.fetched_duration is not None
        else None
    )
    return info_yaml.Info(
        source_id=source_id,
        source_type=resolved.source_type,
        fetched_at=fetched_at,
        source_url=resolved.source_url,
        title=title,
        duration_seconds=duration,
        caption_type=resolved.caption_type,
        transcript_filename=transcript_filename,
        context=info_yaml.SourceContext(),
    )
