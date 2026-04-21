"""readings subcommand: list and show readings."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from auto_lorebook.commands.approve_reading import find_reading_path
from auto_lorebook.reading.frontmatter import split_frontmatter
from auto_lorebook.sources.info_yaml import read_info_yaml
from auto_lorebook.state import get_state_dir

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the readings subcommand with list/show sub-subcommands."""
    parser = subparsers.add_parser(
        "readings",
        help="List or show readings",
        description="Inspect generated readings.",
    )
    sub = parser.add_subparsers(
        dest="readings_command",
        help="readings sub-command",
        required=True,
    )

    # readings list
    list_p = sub.add_parser(
        "list",
        parents=[common_parser],
        help="List all ingested sources with their reading status",
    )
    list_p.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        dest="state_dir",
        help=argparse.SUPPRESS,
    )
    list_p.set_defaults(func=run_list)

    # readings show
    show_p = sub.add_parser(
        "show",
        parents=[common_parser],
        help="Print a reading to stdout",
    )
    show_p.add_argument("source_id", help="Source ID to show")
    show_p.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        dest="state_dir",
        help=argparse.SUPPRESS,
    )
    show_p.set_defaults(func=run_show)

    return parser


def run_list(args: argparse.Namespace) -> int:
    """List all ingested sources and their reading status."""
    state_dir: Path = args.state_dir or get_state_dir()
    sources_dir = state_dir / "sources"
    pending_dir = state_dir / "pending"

    # Collect source info
    entries: dict[str, dict[str, object]] = {}
    if sources_dir.exists():
        for source_dir in sorted(sources_dir.iterdir()):
            if not source_dir.is_dir():
                continue
            info_path = source_dir / "info.yaml"
            if not info_path.exists():
                continue
            try:
                info = read_info_yaml(info_path)
                entries[source_dir.name] = {
                    "title": info["title"],
                    "reading_status": None,
                }
            except Exception:  # noqa: BLE001
                entries[source_dir.name] = {
                    "title": source_dir.name,
                    "reading_status": None,
                }

    # Overlay reading status from pending dir
    if pending_dir.exists():
        for ingest_dir in sorted(pending_dir.iterdir()):
            if not ingest_dir.is_dir():
                continue
            reading_path = ingest_dir / "reading" / "reading.md"
            if not reading_path.exists():
                continue
            try:
                fm, _ = split_frontmatter(reading_path.read_text(encoding="utf-8"))
                sid = fm.get("source_id")
                if isinstance(sid, str) and sid in entries:
                    entries[sid]["reading_status"] = fm.get("reading_status", "unknown")
            except Exception:  # noqa: BLE001,S110
                pass

    if not entries:
        print("No sources ingested.")  # noqa: T201
        return 0

    for sid, data in sorted(entries.items()):
        status = data["reading_status"] or "no reading"
        print(f"{sid}  {data['title']}  [{status}]")  # noqa: T201
    return 0


def run_show(args: argparse.Namespace) -> int:
    """Print a reading to stdout."""
    state_dir: Path = args.state_dir or get_state_dir()
    reading_path = find_reading_path(state_dir, args.source_id)
    if reading_path is None:
        print(f"No reading found for {args.source_id}.", file=sys.stderr)  # noqa: T201
        return 1
    print(reading_path.read_text(encoding="utf-8"))  # noqa: T201
    return 0
