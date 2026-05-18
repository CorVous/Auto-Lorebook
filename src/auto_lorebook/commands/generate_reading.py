"""auto-lorebook generate-reading subcommand."""

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
    """Register the generate-reading subcommand."""
    parser = subparsers.add_parser(
        "generate-reading",
        parents=[common_parser],
        help="Run Stage 1a + 1b and write a draft reading.md for review",
        description=(
            "Run Stage 1a (structure) and Stage 1b (summarize) on an "
            "already-ingested source, producing a draft reading.md under "
            "<wiki>/.wiki-state/pending/<source_id>/reading/ for human review."
        ),
    )
    parser.add_argument(
        "source_id",
        help="Source ID (e.g. yt-abc12345678)",
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the generate-reading command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    try:
        result = pipeline.generate(
            cfg, args.source_id, wiki_override=getattr(args, "wiki", None)
        )
    except pipeline.ReadingPipelineError as e:
        _logger.error("%s", e)
        return 1

    print(f"Draft reading sidecar: {result.sidecar_path}")  # noqa: T201
    print(f"  segments:    {result.segments_dir}")  # noqa: T201
    print(f"  structure:   {result.structure_path}")  # noqa: T201
    print(f"  bullets:     {result.bullets_path}")  # noqa: T201
    if result.gap_warnings:
        print()  # noqa: T201
        print(f"{len(result.gap_warnings)} possible coverage gap(s):")  # noqa: T201
        for w in result.gap_warnings:
            print(f"  ⚠ {w.format_warning()}")  # noqa: T201
    return 0
