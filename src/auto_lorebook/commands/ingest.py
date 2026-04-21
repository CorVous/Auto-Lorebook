"""ingest subcommand: ingest a transcript source."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from auto_lorebook.config import load_config
from auto_lorebook.context.gather import (
    GatherDefaults,
    gather_context,
    load_last_context,
    save_last_context,
)
from auto_lorebook.context.wiki_context import read_wiki_context
from auto_lorebook.sources.info_yaml import make_info_yaml, write_info_yaml
from auto_lorebook.sources.ingest import ingest_local_srt, is_youtube_url
from auto_lorebook.state import get_state_dir

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the ingest subcommand."""
    parser = subparsers.add_parser(
        "ingest",
        parents=[common_parser],
        help="Ingest a transcript source (YouTube URL or local SRT file)",
        description="Ingest a transcript, gather context, and write info.yaml.",
    )
    parser.add_argument("url_or_path", help="YouTube URL or local .srt file path")
    parser.add_argument("--source-url", default=None, dest="source_url")
    parser.add_argument("--title", default=None)
    parser.add_argument("--session-date", default=None, dest="session_date")
    parser.add_argument("--perspective", default=None)
    parser.add_argument("--source-nature", default=None, dest="source_nature")
    parser.add_argument("--setting", default=None)
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        default=False,
        dest="no_interactive",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        dest="state_dir",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the ingest command."""
    state_dir: Path = args.state_dir or get_state_dir()
    sources_dir = state_dir / "sources"

    if is_youtube_url(args.url_or_path):
        print(  # noqa: T201
            "YouTube ingestion is not yet wired in Phase 1 CLI.",
            file=sys.stderr,
        )
        return 1

    srt_path = Path(args.url_or_path)
    if not srt_path.exists():
        print(f"Error: file not found: {srt_path}", file=sys.stderr)  # noqa: T201
        return 1

    result = ingest_local_srt(
        srt_path,
        sources_dir,
        source_url=args.source_url,
        title=args.title,
    )

    # Full duplicate check (command level — distinct from transcript cache)
    info_path = sources_dir / result.source_id / "info.yaml"
    if info_path.exists():
        print(  # noqa: T201
            f"Source {result.source_id} is already fully ingested.\n"
            f"  Update context:  auto-lorebook configure-context {result.source_id}\n"
            f"  Re-generate:     auto-lorebook generate-reading {result.source_id}",
        )
        return 1

    # Defaults: CLI flags > wiki-context.setting > last-context
    config = load_config()
    wiki_setting: str | None = None
    if config.wiki_repo_path:
        wc = read_wiki_context(config.wiki_repo_path / ".wiki-context.yaml")
        wiki_setting = wc["setting"]
    last_ctx = load_last_context(state_dir)
    defaults = GatherDefaults(
        session_date=args.session_date or last_ctx.session_date,
        perspective=args.perspective or last_ctx.perspective,
        source_nature=args.source_nature or last_ctx.source_nature,
        setting=args.setting or wiki_setting or last_ctx.setting,
        notes=last_ctx.notes,
    )
    ctx = gather_context(defaults, no_interactive=args.no_interactive)
    save_last_context(state_dir, ctx)

    # Build and write info.yaml
    info = make_info_yaml(
        source_id=result.source_id,
        source_type=result.source_type,
        source_url=result.source_url,
        title=result.title,
        duration_seconds=result.duration_seconds,
        caption_type=result.caption_type,
    )
    info["session_date"] = ctx.session_date
    ctx_block = info["context"]
    ctx_block["perspective"] = ctx.perspective
    ctx_block["source_nature"] = ctx.source_nature
    ctx_block["setting"] = ctx.setting
    ctx_block["notes"] = ctx.notes
    write_info_yaml(info_path, info)

    print(f"Ingested {result.source_id}.")  # noqa: T201
    print(f"Run: auto-lorebook generate-reading {result.source_id}")  # noqa: T201
    return 0
