"""auto-lorebook reject-ingest subcommand.

Removes every fact and alias tagged with the given ingest, deletes any
entity stub the ingest itself created and that is now empty of facts,
and clears the pending pipeline artifacts (`plan.yaml` + `proposals/`).
Leaves `<wiki>/sources/<source_id>/` and `pending/<id>/reading/` alone
so the user can re-run the pipeline from `approve-reading` or
`regenerate-reading` if desired.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import ingest_cleanup
from auto_lorebook.interactive import _is_interactive

if TYPE_CHECKING:
    import argparse

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the reject-ingest subcommand."""
    parser = subparsers.add_parser(
        "reject-ingest",
        parents=[common_parser],
        help="Undo all of one ingest's contributions",
        description=(
            "Removes facts and aliases tagged with this ingest from every "
            "entity YAML, deletes empty stubs the ingest itself created, "
            "and clears pending plan + proposals. Source files and "
            "reading-stage artifacts are left untouched."
        ),
    )
    parser.add_argument("source_id", help="Source/ingest ID (e.g. yt-abc12345678)")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt (required for non-interactive runs).",
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the reject-ingest command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    if not args.yes and not _is_interactive():
        _logger.error("Refusing to reject-ingest non-interactively without --yes.")
        return 1

    wiki_override: str | None = getattr(args, "wiki", None)

    previewed = ingest_cleanup.preview(cfg, args.source_id, wiki_override=wiki_override)
    if (
        previewed.facts_removed == 0
        and previewed.aliases_removed == 0
        and previewed.stubs_deleted == 0
    ):
        # Still clean pending/ if it exists; otherwise nothing to do.
        actual = ingest_cleanup.reject_ingest(
            cfg, args.source_id, wiki_override=wiki_override
        )
        print(  # noqa: T201
            f"Nothing to reject for {args.source_id!r}; "
            "no facts, aliases, or stubs match."
        )
        _ = actual  # pending may have been cleaned; counts already zero
        return 0

    if not args.yes:
        prompt = (
            f"Reject ingest {args.source_id!r}? This removes "
            f"{previewed.facts_removed} fact(s), "
            f"{previewed.aliases_removed} alias(es); "
            f"deletes {previewed.stubs_deleted} stub(s). [y/N] "
        )
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()  # noqa: T201
            return 130
        if answer not in {"y", "yes"}:
            print("Cancelled; no changes made.")  # noqa: T201
            return 0

    result = ingest_cleanup.reject_ingest(
        cfg, args.source_id, wiki_override=wiki_override
    )
    print(  # noqa: T201
        f"Rejected ingest {args.source_id!r}: "
        f"removed {result.facts_removed} facts, "
        f"{result.aliases_removed} aliases; "
        f"deleted {result.stubs_deleted} stub(s); "
        f"modified {result.entities_modified} entit(ies)."
    )
    return 0
