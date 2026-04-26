"""auto-lorebook review subcommand.

Walks each `pending/<source_id>/proposals/<proposed_id>.yaml` and
prompts for `[a]pprove / [e]dit / [r]eject / [p]lay`. On approval (with
optional alias confirmations) appends a fact to the target entity's
YAML, atomically creating the stub on first approval for proposed-new
entities. Resume on Ctrl-C: untouched proposal files remain.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import reading as reading_mod
from auto_lorebook import review as review_mod
from auto_lorebook.interactive import _is_interactive
from auto_lorebook.review import (
    ApproveDecision,
    Decision,
    EditDecision,
    ProposalView,
    RejectDecision,
)
from auto_lorebook.timestamps import TimestampError, parse_locator_hint

if TYPE_CHECKING:
    import argparse

_logger = logging.getLogger(__name__)

_SEP = "─" * 68


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the review subcommand."""
    parser = subparsers.add_parser(
        "review",
        parents=[common_parser],
        help="Walk pending proposals; approve/edit/reject each",
        description=(
            "Reviews extracted proposals one at a time. On approval the fact "
            "is appended to the target entity's YAML (creating a new stub "
            "atomically for proposed-new entities). Ctrl-C leaves remaining "
            "proposals on disk for the next invocation to resume."
        ),
    )
    parser.add_argument("source_id", help="Source/ingest ID (e.g. yt-abc12345678)")
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help=(
            "Approve every proposal without prompting AND decline every "
            "alias suggestion. Aliases are suggestions, not pre-approvals — "
            "this flag does NOT add suggested aliases. Use it for non-"
            "interactive environments (CI, scripted runs)."
        ),
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the review command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    if not args.auto_approve and not _is_interactive():
        _logger.error("Refusing to review non-interactively without --auto-approve.")
        return 1

    reviewer: review_mod.Reviewer = (
        AutoApproveReviewer() if args.auto_approve else InteractiveReviewer()
    )

    try:
        result = review_mod.run(cfg=cfg, source_id=args.source_id, reviewer=reviewer)
    except review_mod.ReviewError as e:
        _logger.error("%s", e)
        return 1
    except KeyboardInterrupt:
        print(  # noqa: T201
            f"\nInterrupted; resume with: auto-lorebook review {args.source_id}"
        )
        return 130

    total = result.approved + result.edited + result.rejected
    if total == 0 and result.remaining == 0:
        print(  # noqa: T201
            f"Nothing to review for {args.source_id!r}; either run "
            f"`approve-reading` or this ingest is fully reviewed."
        )
        return 0
    print(  # noqa: T201
        f"Reviewed {total}: approved={result.approved} "
        f"edited={result.edited} rejected={result.rejected}"
    )
    return 0


# ---------------------------------------------------------------------------
# Reviewer implementations
# ---------------------------------------------------------------------------


class AutoApproveReviewer:
    """Approves everything; declines every alias suggestion."""

    by_label = "auto-approve"

    def decide(self, view: ProposalView) -> Decision:  # noqa: ARG002
        return ApproveDecision()

    def confirm_alias(self, entity: str, mention: str) -> bool:  # noqa: ARG002
        return False


class InteractiveReviewer:
    """Renders the spec'd display and prompts for the next action."""

    by_label = "human-review"

    def decide(self, view: ProposalView) -> Decision:
        _render(view)
        while True:
            try:
                choice = (
                    input("[a]pprove  [e]dit  [r]eject  [p]lay (open URL)\n> ")
                    .strip()
                    .lower()
                )
            except EOFError:
                # No more stdin (non-interactive harness w/o --auto-approve);
                # treat as reject so we don't loop forever.
                return RejectDecision()
            if choice in {"a", "approve"}:
                return ApproveDecision()
            if choice in {"r", "reject"}:
                return RejectDecision()
            if choice in {"e", "edit"}:
                new_text = input(f"  current: {view.proposal.text}\n  edited: ").strip()
                if not new_text:
                    print("  (empty edit; re-prompting)")  # noqa: T201
                    continue
                return EditDecision(new_text=new_text)
            if choice in {"p", "play"}:
                _print_play(view)
                continue
            print(f"  unknown choice {choice!r}; try a/e/r/p")  # noqa: T201

    def confirm_alias(self, entity: str, mention: str) -> bool:
        while True:
            try:
                raw = (
                    input(f'  Add "{mention}" as alias for {entity}? [y/n] ')
                    .strip()
                    .lower()
                )
            except EOFError:
                return False
            if raw in {"y", "yes"}:
                return True
            if raw in {"n", "no", ""}:
                return False
            print("  please answer y or n")  # noqa: T201


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _render(view: ProposalView) -> None:
    p = view.proposal
    print(  # noqa: T201
        f"\n─── Proposal {view.proposal_index} of {view.proposal_total}  ·  "
        f"Claim group {p.claim_group_id} "
        f"({view.group_position} of {view.group_size} targets) {_SEP[:8]}"
    )
    print(_target_line(view))  # noqa: T201
    if view.matched_via:
        print(f"  Matched via: {view.matched_via}")  # noqa: T201
    if p.extractor_flagged:
        print(f"  Flagged: {p.flag_reason or 'extractor flagged this proposal'}")  # noqa: T201
    if p.hint_widened:
        print("  Hint widened to parent segment")  # noqa: T201
    print(f"Section: {p.section}")  # noqa: T201
    print()  # noqa: T201
    print(f"Proposed text:\n  {p.text!r}")  # noqa: T201
    print(f"\nRaw transcript:\n  {p.raw_transcript_span!r}")  # noqa: T201
    if p.corrections_applied:
        print("\nCorrections applied:")  # noqa: T201
        for c in p.corrections_applied:
            print(f'  • "{c.from_}" → "{c.to}"  ({c.source})')  # noqa: T201
    print()  # noqa: T201
    print(f"Source: {view.source_title or view.proposal.source_id}")  # noqa: T201
    print(f"Locator: {p.locator}  → {_play_url(view) or '(no source URL)'}")  # noqa: T201
    print(f"Speaker: {p.speaker}")  # noqa: T201
    status_line = f"Status: {p.status}"
    if p.status_reason:
        status_line += f"  ({p.status_reason})"
    print(status_line)  # noqa: T201
    if p.session_date:
        print(f"Session date: {p.session_date}")  # noqa: T201
    if p.context_before or p.context_after:
        print("\nContext:")  # noqa: T201
        if p.context_before:
            print(f"  Before: {p.context_before!r}")  # noqa: T201
        if p.context_after:
            print(f"  After:  {p.context_after!r}")  # noqa: T201
    if p.claim_group_siblings:
        print("\nAlso routes to:")  # noqa: T201
        for s in p.claim_group_siblings:
            print(f"  → {s.entity}  ({s.proposed_id})")  # noqa: T201
    print()  # noqa: T201


def _target_line(view: ProposalView) -> str:
    if view.is_new_entity:
        cat = view.new_entity_category or "?"
        if view.created_earlier_in_session:
            return (
                f"Target entity: {view.proposal.target_entity} ({cat})\n"
                f"  Created earlier in this review session"
            )
        return (
            f"Target entity: {view.proposal.target_entity} "
            f"(NEW — {cat}, will be created on approval)"
        )
    return f"Target entity: {view.proposal.target_entity} (existing)"


def _play_url(view: ProposalView) -> str | None:
    """Return a URL with the start timestamp tacked on, or None."""
    if not view.source_url:
        return None
    try:
        start_seconds, _ = parse_locator_hint(view.proposal.locator)
    except TimestampError:
        return view.source_url
    return reading_mod.linkify_timestamp(view.source_url, start_seconds)


def _print_play(view: ProposalView) -> None:
    url = _play_url(view)
    if url:
        print(f"  → {url}")  # noqa: T201
    else:
        print("  (no source URL for this proposal)")  # noqa: T201
