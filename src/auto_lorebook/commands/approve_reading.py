"""approve-reading subcommand: transition reading_status draft → approved."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from auto_lorebook.config import load_config
from auto_lorebook.reading.frontmatter import join_frontmatter, split_frontmatter
from auto_lorebook.state import get_state_dir

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the approve-reading subcommand."""
    parser = subparsers.add_parser(
        "approve-reading",
        parents=[common_parser],
        help="Approve a draft reading and copy it to the wiki repo",
        description=(
            "Transitions reading_status from 'draft' to 'approved' and "
            "writes the approved reading to the wiki repo."
        ),
    )
    parser.add_argument("source_id", help="Source ID whose reading to approve")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        dest="state_dir",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(func=run)
    return parser


def find_reading_path(state_dir: Path, source_id: str) -> Path | None:
    """Scan pending dirs to find reading.md for source_id.

    :param state_dir: tool state directory
    :param source_id: source ID to match in frontmatter
    :return: path to reading.md or None
    """
    pending_dir = state_dir / "pending"
    if not pending_dir.exists():
        return None
    for ingest_dir in sorted(pending_dir.iterdir()):
        if not ingest_dir.is_dir():
            continue
        reading_path = ingest_dir / "reading" / "reading.md"
        if not reading_path.exists():
            continue
        try:
            fm, _ = split_frontmatter(reading_path.read_text(encoding="utf-8"))
            if fm.get("source_id") == source_id:
                return reading_path
        except Exception:  # noqa: BLE001,S112
            continue
    return None


def run(args: argparse.Namespace) -> int:
    """Execute the approve-reading command."""
    state_dir: Path = args.state_dir or get_state_dir()
    reading_path = find_reading_path(state_dir, args.source_id)

    if reading_path is None:
        print(f"No reading found for {args.source_id}.", file=sys.stderr)  # noqa: T201
        return 1

    content = reading_path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(content)
    status = fm.get("reading_status")

    if status == "approved":
        print(f"{args.source_id} is already approved.")  # noqa: T201
        return 0

    if status != "draft":
        print(  # noqa: T201
            f"Expected reading_status='draft', got {status!r}.",
            file=sys.stderr,
        )
        return 1

    fm["reading_status"] = "approved"
    updated = join_frontmatter(fm, body)
    reading_path.write_text(updated, encoding="utf-8")

    config = load_config()
    if config.wiki_repo_path:
        wiki_dir = config.wiki_repo_path / "sources" / args.source_id
        wiki_dir.mkdir(parents=True, exist_ok=True)
        dest = wiki_dir / "reading.md"
        dest.write_text(updated, encoding="utf-8")
        print(f"Approved and copied to {dest}.")  # noqa: T201
    else:
        print(  # noqa: T201
            f"Approved {args.source_id}. "
            "No wiki_repo_path configured — reading not copied to wiki."
        )
    return 0
