"""generate-reading subcommand: run the full 1a→1b pipeline."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from auto_lorebook.state import get_state_dir

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the generate-reading subcommand."""
    parser = subparsers.add_parser(
        "generate-reading",
        parents=[common_parser],
        help="Generate a reading for an ingested source (Stage 1a + 1b)",
        description="Run the structure + summarize pipeline and assemble reading.md.",
    )
    parser.add_argument("source_id", help="Source ID to generate a reading for")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        dest="state_dir",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the generate-reading command (pipeline stub)."""
    _state_dir: Path = args.state_dir or get_state_dir()
    # Pipeline stages (Tasks 25-34) not yet implemented.
    print(  # noqa: T201
        f"generate-reading: pipeline not yet implemented for {args.source_id}.\n"
        "Implement Tasks 25-34 to enable this command.",
    )
    return 0
