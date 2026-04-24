"""auto-lorebook ingest subcommand."""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import info_yaml, source_store
from auto_lorebook import source_id as sid_mod
from auto_lorebook.commands._shared import finalize_context

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
            "YouTube URLs require yt-dlp (fetch not yet implemented)."
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
        "--no-interactive",
        dest="no_interactive",
        action="store_true",
        default=False,
        help="Skip all interactive prompts; use flags only",
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the ingest command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    wiki_repo = cfg.wiki_repo_path

    if args.url_or_path.startswith(("http://", "https://")) and not args.source_id:
        _logger.error(
            "Fetching transcripts from URLs is not yet implemented. "
            "Download the transcript manually and pass the local file."
        )
        return 1

    try:
        source_id = sid_mod.derive(args.url_or_path, args.source_id, args.source_url)
    except sid_mod.SourceIdError as e:
        _logger.error("%s", e)
        return 1

    local_path = Path(args.url_or_path)
    if not local_path.exists():
        _logger.error("File not found: %s", local_path)
        return 1

    source_type = _derive_source_type(local_path, args.source_url)

    try:
        dest, transcript_filename = source_store.copy_transcript(
            local_path, source_id, source_type, wiki_repo
        )
    except (source_store.DuplicateSourceError, source_store.CollisionError) as e:
        _logger.error("%s", e)
        return 2

    _logger.info("Transcript stored as %s", dest)

    info_path = wiki_repo / "sources" / source_id / "info.yaml"
    if info_path.exists():
        try:
            info = info_yaml.read(info_path)
        except info_yaml.InfoError:
            _logger.warning("Existing info.yaml unreadable; recreating")
            info = _new_info(source_id, source_type, args, transcript_filename)
    else:
        info = _new_info(source_id, source_type, args, transcript_filename)

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
    source_type: str,
    args: argparse.Namespace,
    transcript_filename: str,
) -> info_yaml.Info:
    fetched_at = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return info_yaml.Info(
        source_id=source_id,
        source_type=source_type,
        fetched_at=fetched_at,
        source_url=args.source_url,
        title=Path(args.url_or_path).stem,
        transcript_filename=transcript_filename,
        context=info_yaml.SourceContext(),
    )
