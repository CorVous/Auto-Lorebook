"""auto-lorebook replan subcommand.

Discards unreviewed proposals from the current run and re-runs the
planner + extractor against the wiki's current entity state. Already-
approved facts in entity YAMLs are unaffected; stubs created by
earlier approvals are visible to the new plan because
`pipeline.extract` rebuilds the in-memory `EntityIndex` from disk.
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
    """Register the replan subcommand."""
    parser = subparsers.add_parser(
        "replan",
        parents=[common_parser],
        help="Discard unreviewed proposals; re-run planner + extractor",
        description=(
            "Discards every unreviewed proposal under "
            "pending/<source_id>/proposals/, re-invokes the planner "
            "(which sees any entity stubs created by approvals earlier "
            "in this ingest), and re-runs the extractor. Already-"
            "approved facts in entity YAMLs are unaffected."
        ),
    )
    parser.add_argument("source_id", help="Source/ingest ID (e.g. yt-abc12345678)")
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the replan command."""
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

    try:
        extract_result = pipeline.extract(cfg, args.source_id)
    except pipeline.ReadingPipelineError as e:
        _logger.error("extractor failed: %s", e)
        return 1

    n = len(extract_result.proposals)
    flagged = extract_result.flagged_count
    print(f"Replanned: {plan_result.plan_path}")  # noqa: T201
    print(  # noqa: T201
        f"Extracted {n} proposal(s) ({flagged} flagged) → "
        f"{extract_result.proposals_dir}"
    )
    return 0
