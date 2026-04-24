"""auto-lorebook configure-context subcommand."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import (
    corrections,
    entity_index,
    info_yaml,
    interactive,
    wiki_context,
)
from auto_lorebook import preamble as preamble_mod

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

    wiki_repo = cfg.wiki_repo_path
    info_path = wiki_repo / "sources" / args.source_id / "info.yaml"

    if not info_path.exists():
        _logger.error(
            "No info.yaml found for source '%s'. Run `ingest` first.", args.source_id
        )
        return 1

    try:
        info = info_yaml.read(info_path)
    except info_yaml.InfoError as e:
        _logger.error("Could not read info.yaml: %s", e)
        return 1

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
