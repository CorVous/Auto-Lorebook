"""auto-lorebook configure-context subcommand."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import info_yaml
from auto_lorebook.commands._shared import finalize_context

if TYPE_CHECKING:
    import argparse

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the configure-context subcommand."""
    parser = subparsers.add_parser(
        "configure-context",
        parents=[common_parser],
        help="Re-run context prompts for an existing source",
        description=(
            "Re-open the interactive context step for an existing source, "
            "filling in skipped fields or correcting mistakes."
        ),
    )
    parser.add_argument(
        "source_id",
        help="Source ID (e.g. txt-abc1234567 or yt-abc123defgh)",
    )
    parser.add_argument(
        "--session-date",
        dest="session_date",
        default=None,
        metavar="YYYY-MM-DD",
    )
    parser.add_argument("--perspective", dest="perspective", default=None)
    parser.add_argument("--source-nature", dest="source_nature", default=None)
    parser.add_argument("--setting", dest="setting", default=None)
    parser.add_argument("--notes", dest="notes", default=None, help="One-line notes")
    parser.add_argument(
        "--no-interactive",
        dest="no_interactive",
        action="store_true",
        default=False,
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the configure-context command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    wiki_repo = cfg.resolve_active_wiki(getattr(args, "wiki", None))
    info_path = wiki_repo / "sources" / args.source_id / "info.yaml"

    if not info_path.exists():
        _logger.error(
            "No info.yaml found for source '%s'. Run `ingest` first.", args.source_id
        )
        return 1

    try:
        info = info_yaml.read_yaml(info_path)
    except info_yaml.InfoError as e:
        _logger.error("Could not read info.yaml: %s", e)
        return 1

    return finalize_context(info, info_path, cfg, args)
