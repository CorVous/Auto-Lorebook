"""auto-lorebook regenerate-reading subcommand."""

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
    """Register the regenerate-reading subcommand."""
    parser = subparsers.add_parser(
        "regenerate-reading",
        parents=[common_parser],
        help="Re-run Stage 1a or 1b for an existing draft reading",
        description=(
            "Re-runs from a given substage. `--from=structure` reruns 1a "
            "then 1b (discarding any existing bullets). `--from=summarize` "
            "reruns only 1b, preserving structure.yaml. With `--segments`, "
            "only the listed segments are re-summarized; other segments "
            "keep their existing bullets."
        ),
    )
    parser.add_argument("source_id", help="Source ID (e.g. yt-abc12345678)")
    parser.add_argument(
        "--from",
        dest="from_stage",
        required=True,
        choices=["structure", "summarize"],
        help="Substage to start from",
    )
    parser.add_argument(
        "--segments",
        dest="segments",
        default=None,
        help=("Comma-separated segment IDs (valid only with --from=summarize)"),
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the regenerate-reading command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    segment_ids: list[str] | None = None
    if args.segments:
        segment_ids = [s.strip() for s in args.segments.split(",") if s.strip()]

    try:
        result = pipeline.regenerate(
            cfg,
            args.source_id,
            from_stage=args.from_stage,
            segment_ids=segment_ids,
        )
    except pipeline.ReadingPipelineError as e:
        _logger.error("%s", e)
        return 1

    print(f"Draft reading: {result.pending_reading_path}")  # noqa: T201
    if result.gap_warnings:
        print()  # noqa: T201
        print(f"{len(result.gap_warnings)} possible coverage gap(s):")  # noqa: T201
        for w in result.gap_warnings:
            print(f"  ⚠ {w.format_warning()}")  # noqa: T201
    return 0
