"""configure-context subcommand: re-gather context for an existing source."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from auto_lorebook.context.gather import (
    GatherDefaults,
    gather_context,
    load_last_context,
    save_last_context,
)
from auto_lorebook.sources.info_yaml import read_info_yaml, write_info_yaml
from auto_lorebook.state import get_state_dir

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the configure-context subcommand."""
    parser = subparsers.add_parser(
        "configure-context",
        parents=[common_parser],
        help="Re-gather context for an existing ingested source",
        description="Re-run the context prompt sequence and update info.yaml.",
    )
    parser.add_argument("source_id", help="Source ID to configure")
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
    """Execute the configure-context command."""
    state_dir: Path = args.state_dir or get_state_dir()
    sources_dir = state_dir / "sources"
    info_path = sources_dir / args.source_id / "info.yaml"

    if not info_path.exists():
        print(f"Source {args.source_id} not found.", file=sys.stderr)  # noqa: T201
        return 1

    info = read_info_yaml(info_path)
    ctx_block = info["context"]
    last_ctx = load_last_context(state_dir)
    defaults = GatherDefaults(
        session_date=info["session_date"] or last_ctx.session_date,
        perspective=ctx_block["perspective"] or last_ctx.perspective,
        source_nature=ctx_block["source_nature"] or last_ctx.source_nature,
        setting=ctx_block["setting"] or last_ctx.setting,
        notes=ctx_block["notes"] or last_ctx.notes,
    )
    ctx = gather_context(defaults, no_interactive=args.no_interactive)
    save_last_context(state_dir, ctx)

    info["session_date"] = ctx.session_date
    ctx_block["perspective"] = ctx.perspective
    ctx_block["source_nature"] = ctx.source_nature
    ctx_block["setting"] = ctx.setting
    ctx_block["notes"] = ctx.notes
    write_info_yaml(info_path, info)

    print(f"Context updated for {args.source_id}.")  # noqa: T201
    return 0
