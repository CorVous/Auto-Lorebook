"""auto-lorebook extract subcommand.

Runs Stage 3 (extractor) on a planned reading; writes proposal YAMLs
under pending/<source_id>/proposals/. Requires pending/<source_id>/plan.yaml
(run plan first).
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
    """Register the extract subcommand."""
    parser = subparsers.add_parser(
        "extract",
        parents=[common_parser],
        help="Run Stage 3 (extractor) on a planned reading",
        description=(
            "Run Stage 3 (extractor) on a planned reading; writes proposal "
            "YAMLs under pending/<source_id>/proposals/. Requires "
            "pending/<source_id>/plan.yaml (run plan first)."
        ),
    )
    parser.add_argument("source_id", help="Source ID (e.g. yt-abc12345678)")
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the extract command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

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
