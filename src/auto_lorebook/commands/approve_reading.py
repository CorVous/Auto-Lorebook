"""auto-lorebook approve-reading subcommand."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess  # noqa: S404
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import reading_pipeline as pipeline
from auto_lorebook.interactive import _is_interactive

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

_logger = logging.getLogger(__name__)

_PROMPT = "[a]pprove / [e]dit / [r]eject / [u]ndo / [q]uit > "
_RENDER_LINES = 40


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the approve-reading subcommand."""
    parser = subparsers.add_parser(
        "approve-reading",
        parents=[common_parser],
        help="Interactively approve, edit, or reject the draft reading",
        description=(
            "Opens an interactive session over the draft reading.md under "
            "~/.auto-lorebook/pending/<source_id>/reading/. Keys: "
            "[a]pprove (flip to approved + copy to wiki + run plan/extract), "
            "[e]dit (open in $EDITOR), [r]eject (queue pending dir for delete), "
            "[u]ndo (restore to session start, clear reject), "
            "[q]uit (commit a queued reject after confirmation, else no-op). "
            "Pass --yes to skip the loop and auto-approve."
        ),
    )
    parser.add_argument("source_id", help="Source ID (e.g. yt-abc12345678)")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive loop; auto-approve (required for non-TTY runs).",
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the approve-reading command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    if not args.yes and not _is_interactive():
        _logger.error("Refusing to approve-reading non-interactively without --yes.")
        return 1

    if args.yes:
        return _approve_and_extract(cfg, args.source_id)

    return _interactive_session(cfg, args.source_id)


def _approve_and_extract(cfg: cfg_mod.Config, source_id: str) -> int:
    """One-shot flow: approve → plan → extract (delegates to reading_pipeline)."""
    try:
        result = pipeline.approve_and_extract(cfg, source_id)
    except pipeline.ReadingPipelineError as e:
        _logger.error("%s", e)
        return 1

    print(f"Approved: {result.approved_path}")  # noqa: T201
    print(f"Plan: {result.plan_result.plan_path}")  # noqa: T201
    n = len(result.extract_result.proposals)
    flagged = result.extract_result.flagged_count
    print(  # noqa: T201
        f"Extracted {n} proposal(s) ({flagged} flagged) → "
        f"{result.extract_result.proposals_dir}"
    )
    return 0


def _interactive_session(cfg: cfg_mod.Config, source_id: str) -> int:
    pending_path = pipeline.pending_reading_path(source_id)
    if not pending_path.exists():
        _logger.error(
            "No draft reading for %r. Run `generate-reading` first.", source_id
        )
        return 1

    original_bytes = pending_path.read_bytes()
    pending_action = "none"

    while True:
        _render(pending_path, pending_action, original_bytes)
        try:
            choice = input(_PROMPT).strip().lower()
        except EOFError:
            print()  # noqa: T201
            return _commit_quit(source_id, pending_action)
        except KeyboardInterrupt:
            print()  # noqa: T201
            return 130

        if choice == "a":
            return _approve_and_extract(cfg, source_id)
        if choice == "q":
            return _commit_quit(source_id, pending_action)
        if choice == "r":
            pending_action = "reject"
            continue
        if choice == "u":
            pending_path.write_bytes(original_bytes)
            pending_action = "none"
            continue
        if choice == "e":
            editor = os.environ.get("EDITOR", "vi")
            subprocess.run([editor, str(pending_path)], check=False)  # noqa: S603
            continue
        # unrecognized → re-prompt


def _render(pending_path: Path, pending_action: str, original_bytes: bytes) -> None:
    text = pending_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    head = "\n".join(lines[:_RENDER_LINES])
    suffix = (
        f"\n  ... ({len(lines) - _RENDER_LINES} more lines, {len(text)} chars total)"
        if len(lines) > _RENDER_LINES
        else ""
    )
    dirty = " [edited]" if pending_path.read_bytes() != original_bytes else ""
    print(f"\n--- {pending_path}{dirty} ---")  # noqa: T201
    print(head + suffix)  # noqa: T201
    print(f"--- pending action: {pending_action} ---")  # noqa: T201


def _commit_quit(source_id: str, pending_action: str) -> int:
    if pending_action != "reject":
        return 0
    pending_dir = pipeline.pending_dir(source_id)
    try:
        answer = input(f"Delete {pending_dir}? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()  # noqa: T201
        return 0
    if answer in {"y", "yes"}:
        shutil.rmtree(pending_dir, ignore_errors=True)
        print(f"Deleted {pending_dir}")  # noqa: T201
    return 0
