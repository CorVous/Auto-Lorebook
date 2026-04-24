"""auto-lorebook ingest subcommand."""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import (
    corrections,
    entity_index,
    info_yaml,
    interactive,
    source_store,
    wiki_context,
)
from auto_lorebook import preamble as preamble_mod
from auto_lorebook import source_id as sid_mod
from auto_lorebook.source_id import _extract_video_id

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

    # Refuse bare URL (fetch not implemented)
    if args.url_or_path.startswith(("http://", "https://")) and not args.source_id:
        _logger.error(
            "Fetching transcripts from URLs is not yet implemented. "
            "Download the transcript manually and pass the local file."
        )
        return 1

    # Derive source ID
    try:
        source_id = sid_mod.derive(args.url_or_path, args.source_id, args.source_url)
    except sid_mod.SourceIdError as e:
        _logger.error("%s", e)
        return 1

    # Validate local path
    local_path = Path(args.url_or_path)
    if not local_path.exists():
        _logger.error("File not found: %s", local_path)
        return 1

    source_type = _derive_source_type(local_path, args.source_url)

    # Copy transcript
    try:
        dest, transcript_filename = source_store.copy_transcript(
            local_path, source_id, source_type, wiki_repo
        )
    except source_store.DuplicateSourceError as e:
        _logger.error("%s", e)
        return 2
    except source_store.CollisionError as e:
        _logger.error("%s", e)
        return 2

    _logger.info("Transcript stored as %s", dest)

    # Load or create info.yaml
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

    wc = wiki_context.read(wiki_repo / ".wiki-context.yaml")
    cors = corrections.read(wiki_repo / ".transcription-corrections.yaml")
    last_ctx = cfg_mod.load_last_context()

    flags = {
        "session_date": args.session_date,
        "perspective": args.perspective,
        "source_nature": args.source_nature,
        "setting": args.setting,
        "notes": None,
    }
    try:
        info = interactive.gather_context(
            info,
            flags,
            wc,
            last_ctx,
            interactive=not args.no_interactive,
            save_path=info_path,
        )
    except KeyboardInterrupt:
        return 130

    info_yaml.write(info, info_path)
    print(f"Context saved to {info_path}")  # noqa: T201

    cfg_mod.save_last_context(
        cfg_mod.LastContext(
            perspective=info.context.perspective,
            source_nature=info.context.source_nature,
        )
    )

    idx = entity_index.build(wiki_repo)
    try:
        p = preamble_mod.assemble(info, wc, cors, idx, reduced=False)
        p.check_budget(
            context_window=cfg.models.primary_context_window,
            budget_fraction=cfg.preamble.budget_fraction,
        )
    except preamble_mod.PreambleTooLargeError as e:
        _logger.error("%s", e)
        return 1

    char_count = len(p.text)
    token_approx = char_count // 4
    print(f"Preamble: {char_count} chars (~{token_approx} tokens) — budget OK")  # noqa: T201
    return 0


def _derive_source_type(local_path: Path, source_url: str | None) -> str:
    suffix = local_path.suffix.lower()
    if source_url and _extract_video_id(source_url):
        return "youtube"
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
    title = Path(args.url_or_path).stem
    fetched_at = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return info_yaml.Info(
        source_id=source_id,
        source_type=source_type,
        fetched_at=fetched_at,
        source_url=args.source_url,
        title=title,
        transcript_filename=transcript_filename,
        context=info_yaml.SourceContext(),
    )
