"""auto-lorebook plan subcommand.

Runs Stage 2 (planner) on an approved reading; writes
pending/<source_id>/plan.yaml. Requires the wiki-side reading.md
(run approve-reading first).
"""

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
    """Register the plan subcommand."""
    parser = subparsers.add_parser(
        "plan",
        parents=[common_parser],
        help="Run Stage 2 (planner) on an approved reading",
        description=(
            "Run Stage 2 (planner) on an approved reading; writes "
            "pending/<source_id>/plan.yaml. Requires the wiki-side "
            "reading.md (run approve-reading first)."
        ),
    )
    parser.add_argument("source_id", help="Source ID (e.g. yt-abc12345678)")
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the plan command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    try:
        plan_result = pipeline.plan(cfg, args.source_id)
    except pipeline.ReadingPipelineError as e:
        _logger.error("planner failed: %s", e)
        return 1

    print(f"Plan: {plan_result.plan_path}")  # noqa: T201
    return 0
