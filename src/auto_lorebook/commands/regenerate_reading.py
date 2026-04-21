"""regenerate-reading subcommand: re-run 1a and/or 1b pipeline stages."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from auto_lorebook.state import get_state_dir

_logger = logging.getLogger(__name__)

_FROM_CHOICES = ("structure", "summarize")


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the regenerate-reading subcommand."""
    parser = subparsers.add_parser(
        "regenerate-reading",
        parents=[common_parser],
        help="Re-run pipeline stages for an existing reading",
        description=(
            "Re-run from structure (1a+1b) or from summarize (1b only). "
            "name_corrections in frontmatter are preserved across regenerations."
        ),
    )
    parser.add_argument("source_id", help="Source ID to regenerate")
    parser.add_argument(
        "--from",
        required=True,
        choices=_FROM_CHOICES,
        dest="from_",
        metavar="{structure,summarize}",
        help="Stage to restart from: 'structure' reruns 1a+1b; 'summarize' reruns 1b",
    )
    parser.add_argument(
        "--segments",
        default=None,
        help="Comma-separated segment IDs to re-summarize (--from=summarize only)",
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


def parse_segments(segments_str: str | None) -> list[str]:
    """Split comma-separated segment IDs into a list.

    :param segments_str: raw --segments value or None
    :return: list of stripped segment ID strings
    """
    if not segments_str:
        return []
    return [s.strip() for s in segments_str.split(",") if s.strip()]


def run(args: argparse.Namespace) -> int:
    """Execute the regenerate-reading command (pipeline stub)."""
    _state_dir: Path = args.state_dir or get_state_dir()
    segments = parse_segments(args.segments)
    _logger.debug(
        "regenerate-reading: source=%s from=%s segments=%s",
        args.source_id,
        args.from_,
        segments,
    )
    print(  # noqa: T201
        f"regenerate-reading: pipeline not yet implemented for {args.source_id} "
        f"(--from={args.from_}).\n"
        "Implement Tasks 25-34 to enable this command.",
    )
    return 0
