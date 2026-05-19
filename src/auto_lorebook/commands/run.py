"""auto-lorebook run umbrella subcommand.

Walks a source through the pipeline from its current state to completion,
delegating each stage to the matching command's run() function.
"""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook.commands import approve_reading as approve_reading_cmd
from auto_lorebook.commands import extract as extract_cmd
from auto_lorebook.commands import generate_reading as generate_reading_cmd
from auto_lorebook.commands import plan as plan_cmd
from auto_lorebook.commands import review as review_cmd
from auto_lorebook.pipeline_state import Stage, first_missing_stage

if TYPE_CHECKING:
    from collections.abc import Callable

_logger = logging.getLogger(__name__)

# Extra Namespace fields each stage's run() requires beyond source_id/wiki.
_STAGE_DEFAULTS: dict[Stage, dict[str, object]] = {
    Stage.GENERATE_READING: {},
    Stage.APPROVE_READING: {"yes": False},
    Stage.PLAN: {},
    Stage.EXTRACT: {},
    Stage.REVIEW: {"auto_approve": False},
}

# Maps Stage → command run() callable; exposed for patching in tests.
STAGE_RUNNERS: dict[Stage, Callable[[argparse.Namespace], int]] = {
    Stage.GENERATE_READING: generate_reading_cmd.run,
    Stage.APPROVE_READING: approve_reading_cmd.run,
    Stage.PLAN: plan_cmd.run,
    Stage.EXTRACT: extract_cmd.run,
    Stage.REVIEW: review_cmd.run,
}


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the run subcommand."""
    parser = subparsers.add_parser(
        "run",
        parents=[common_parser],
        help="Walk a source through the pipeline from current state to completion",
        description=(
            "Detect which pipeline stage is next for a given source and run "
            "through to completion. At human-gate stages (approve-reading, "
            "review) defers to the interactive command as-is."
        ),
    )
    parser.add_argument("source_id", help="Source ID (e.g. yt-abc12345678)")
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the run command: loop through stages until done."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    wiki_override: str | None = getattr(args, "wiki", None)
    source_id: str = args.source_id

    while True:
        stage = first_missing_stage(cfg, source_id, wiki_override=wiki_override)
        if stage is None:
            return 0

        stage_runner = STAGE_RUNNERS[stage]
        defaults = _STAGE_DEFAULTS[stage]
        stage_args = argparse.Namespace(
            source_id=source_id,
            wiki=wiki_override,
            **defaults,
        )
        exit_code = stage_runner(stage_args)
        if exit_code != 0:
            return exit_code
