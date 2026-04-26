"""auto-lorebook approve-reading subcommand."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import reading_pipeline as pipeline

if TYPE_CHECKING:
    import argparse

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the approve-reading subcommand."""
    parser = subparsers.add_parser(
        "approve-reading",
        parents=[common_parser],
        help="Flip the draft reading to approved and copy it into the wiki",
        description=(
            "Sets `reading_status: approved` on the draft reading.md under "
            "~/.auto-lorebook/pending/<source_id>/reading/ and copies it to "
            "<wiki>/sources/<source_id>/reading.md. Downstream stages refuse "
            "to run on a draft reading."
        ),
    )
    parser.add_argument("source_id", help="Source ID (e.g. yt-abc12345678)")
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the approve-reading command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    try:
        approved = pipeline.approve(cfg, args.source_id)
    except pipeline.ReadingPipelineError as e:
        _logger.error("%s", e)
        return 1

    print(f"Approved: {approved}")  # noqa: T201

    try:
        plan_result = pipeline.plan(cfg, args.source_id)
    except pipeline.ReadingPipelineError as e:
        _logger.error("planner failed: %s", e)
        return 1

    print(f"Plan: {plan_result.plan_path}")  # noqa: T201

    try:
        extract_result = pipeline.extract(cfg, args.source_id)
    except pipeline.ReadingPipelineError as e:
        _logger.error("extractor failed: %s", e)
        return 1

    n = len(extract_result.proposals)
    flagged = extract_result.flagged_count
    print(  # noqa: T201
        f"Extracted {n} proposal(s) ({flagged} flagged) → "
        f"{extract_result.proposals_dir}"
    )
    return 0
