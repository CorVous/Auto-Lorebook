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
    BundleDecision,
    BundleEdits,
    BundleView,
    RejectDecision,
    TargetEdits,
    TargetView,
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
        result = review_mod.run(
            cfg=cfg,
            source_id=args.source_id,
            reviewer=reviewer,
            wiki_override=getattr(args, "wiki", None),
        )
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
    """Approves every bundle; declines every alias suggestion."""

    by_label = "auto-approve"

    def decide_bundle(self, view: BundleView) -> BundleDecision:
        return BundleDecision(
            decision=ApproveDecision(),
            selected_indices=tuple(range(len(view.targets))),
        )

    def confirm_alias(self, entity: str, mention: str) -> bool:  # noqa: ARG002
        return False


class InteractiveReviewer:
    """Renders one bundle screen and prompts for the next action."""

    by_label = "human-review"

    def decide_bundle(self, view: BundleView) -> BundleDecision:
        # `selected[i]` toggles route i. Default: every route checked.
        selected = [True] * len(view.targets)
        # Per-target overrides accumulated via `[t]`.
        overrides: dict[int, TargetEdits] = {}
        bundle_edits: BundleEdits | None = None
        _render_bundle(view, selected, overrides, bundle_edits)
        while True:
            try:
                choice = input(_prompt_line(view)).strip().lower()
            except EOFError:
                # No more stdin (non-interactive harness w/o --auto-approve);
                # treat as reject so we don't loop forever.
                return BundleDecision(decision=RejectDecision(), selected_indices=())
            if choice in {"a", "approve"}:
                indices = tuple(i for i, on in enumerate(selected) if on)
                if not indices:
                    print("  No routes selected; toggle with [t] or reject.")  # noqa: T201
                    continue
                return BundleDecision(
                    decision=bundle_edits or ApproveDecision(),
                    selected_indices=indices,
                    per_target_overrides={
                        i: ov for i, ov in overrides.items() if selected[i]
                    },
                )
            if choice in {"r", "reject"}:
                return BundleDecision(decision=RejectDecision(), selected_indices=())
            if choice in {"e", "edit"}:
                bundle_edits = _gather_bundle_edits(view, bundle_edits)
                _render_bundle(view, selected, overrides, bundle_edits)
                continue
            if choice in {"t", "targets"}:
                _gather_target_toggles(view, selected, overrides)
                _render_bundle(view, selected, overrides, bundle_edits)
                continue
            if choice in {"p", "play"}:
                _print_play(view)
                continue
            if choice in {"u", "undo"}:
                # Reset accumulated bundle state: clears edits, re-checks
                # every route (un-rejecting per-target rejections), and
                # drops per-target overrides.
                bundle_edits = None
                overrides.clear()
                for i in range(len(selected)):
                    selected[i] = True
                print("  Undid bundle edits, re-checked every route.")  # noqa: T201
                _render_bundle(view, selected, overrides, bundle_edits)
                continue
            print(f"  unknown choice {choice!r}; try a/e/r/p/t/u")  # noqa: T201

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


def _gather_bundle_edits(
    view: BundleView, current: BundleEdits | None
) -> BundleEdits | None:
    """Bundle-level edits: text / status / status_reason only.

    `section` and `speaker` vary per route, so they live in the
    `[t]argets` sub-prompt as per-target overrides.
    """
    sample = view.targets[0].proposal
    print("  Bundle edit (Enter to keep current value; applies to checked routes):")  # noqa: T201
    new_text = _prompt_optional(
        "text", current.new_text if current and current.new_text else sample.text
    )
    new_status = _prompt_status(
        current.new_status if current and current.new_status else sample.status
    )
    new_status_reason = _prompt_optional(
        "status_reason",
        (
            current.new_status_reason
            if current and current.new_status_reason is not None
            else sample.status_reason or ""
        ),
    )
    edits = BundleEdits(
        new_text=new_text,
        new_status=new_status,
        new_status_reason=new_status_reason,
    )
    return None if edits.is_noop() else edits


def _gather_target_toggles(
    view: BundleView,
    selected: list[bool],
    overrides: dict[int, TargetEdits],
) -> None:
    """Toggle inclusion per route; for kept rows, override section / speaker."""
    print("  Targets (per route):")  # noqa: T201
    for i, target in enumerate(view.targets):
        mark = "[x]" if selected[i] else "[ ]"
        print(  # noqa: T201
            f"    {i + 1}. {mark} {target.proposal.target_entity}  "
            f"(section={target.proposal.section}, speaker={target.proposal.speaker})"
        )
    while True:
        try:
            raw = input("    toggle # / edit # / done: ").strip().lower()
        except EOFError:
            return
        if raw in {"", "done", "d"}:
            return
        parts = raw.split()
        if len(parts) == 2 and parts[0] in {"toggle", "t"} and parts[1].isdigit():
            idx = int(parts[1]) - 1
            if 0 <= idx < len(selected):
                selected[idx] = not selected[idx]
                mark = "[x]" if selected[idx] else "[ ]"
                print(  # noqa: T201
                    f"    {mark} {view.targets[idx].proposal.target_entity}"
                )
            continue
        if len(parts) == 2 and parts[0] in {"edit", "e"} and parts[1].isdigit():
            idx = int(parts[1]) - 1
            if 0 <= idx < len(view.targets):
                _edit_target_override(view.targets[idx], idx, overrides)
            continue
        print("    expected: 'toggle N' / 'edit N' / 'done'")  # noqa: T201


def _edit_target_override(
    target: TargetView, idx: int, overrides: dict[int, TargetEdits]
) -> None:
    """Prompt for per-target section / speaker; merge into overrides."""
    p = target.proposal
    current = overrides.get(idx)
    new_section = _prompt_optional(
        "section",
        current.new_section if current and current.new_section else p.section,
    )
    new_speaker = _prompt_optional(
        "speaker",
        current.new_speaker if current and current.new_speaker else p.speaker,
    )
    edits = TargetEdits(new_section=new_section, new_speaker=new_speaker)
    if edits.is_noop():
        overrides.pop(idx, None)
    else:
        overrides[idx] = edits


def _prompt_line(view: BundleView) -> str:
    if len(view.targets) > 1:
        return "[a]pprove  [e]dit  [r]eject  [p]lay  [t]argets  [u]ndo\n> "
    return "[a]pprove  [e]dit  [r]eject  [p]lay (open URL)  [u]ndo\n> "


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _render_bundle(
    view: BundleView,
    selected: list[bool],
    overrides: dict[int, TargetEdits],
    bundle_edits: BundleEdits | None,
) -> None:
    """Render one claim-group bundle.

    Singletons render an abbreviated form (no checklist). Multi-target
    bundles show the claim text once, then a numbered route checklist
    with per-row entity / category / section / matched_via.
    """
    head_proposal = view.targets[0].proposal
    text = (
        bundle_edits.new_text
        if bundle_edits and bundle_edits.new_text
        else head_proposal.text
    )
    status = (
        bundle_edits.new_status
        if bundle_edits and bundle_edits.new_status
        else head_proposal.status
    )
    status_reason = (
        bundle_edits.new_status_reason
        if bundle_edits and bundle_edits.new_status_reason is not None
        else head_proposal.status_reason
    )
    is_singleton = len(view.targets) == 1
    header_kind = "Proposal" if is_singleton else "Bundle"
    route_count = len(view.targets)
    selected_count = sum(1 for b in selected if b)
    print(  # noqa: T201
        f"\n─── {header_kind} {view.bundle_index} of {view.bundle_total}  ·  "
        f"Claim group {view.claim_group_id} "
        f"({selected_count} of {route_count} routes selected) {_SEP[:8]}"
    )
    print(f"Proposed text:\n  {text!r}")  # noqa: T201
    print(f"\nRaw transcript:\n  {head_proposal.raw_transcript_span!r}")  # noqa: T201
    if head_proposal.corrections_applied:
        print("\nCorrections applied:")  # noqa: T201
        for c in head_proposal.corrections_applied:
            print(f'  • "{c.from_}" → "{c.to}"  ({c.source})')  # noqa: T201
    print()  # noqa: T201
    print(f"Source: {view.source_title or head_proposal.source_id}")  # noqa: T201
    print(  # noqa: T201
        f"Locator: {head_proposal.locator}  → {_play_url(view) or '(no source URL)'}"
    )
    status_line = f"Status: {status}"
    if status_reason:
        status_line += f"  ({status_reason})"
    print(status_line)  # noqa: T201
    if head_proposal.session_date:
        print(f"Session date: {head_proposal.session_date}")  # noqa: T201
    if head_proposal.context_before or head_proposal.context_after:
        print("\nContext:")  # noqa: T201
        if head_proposal.context_before:
            print(f"  Before: {head_proposal.context_before!r}")  # noqa: T201
        if head_proposal.context_after:
            print(f"  After:  {head_proposal.context_after!r}")  # noqa: T201
    if head_proposal.extractor_flagged:
        print(  # noqa: T201
            f"Flagged: {head_proposal.flag_reason or 'extractor flagged this'}"
        )
    if head_proposal.hint_widened:
        print("Hint widened to parent segment")  # noqa: T201

    print()  # noqa: T201
    print("Routes:")  # noqa: T201
    for i, target in enumerate(view.targets):
        mark = "[x]" if selected[i] else "[ ]"
        ov = overrides.get(i)
        section = ov.new_section if ov and ov.new_section else target.proposal.section
        speaker = ov.new_speaker if ov and ov.new_speaker else target.proposal.speaker
        prefix = "  " if is_singleton else f"  {i + 1}. "
        print(  # noqa: T201
            f"{prefix}{mark} {_route_label(target)}  "
            f"section={section}  speaker={speaker}"
        )
        if target.matched_via:
            print(f"      Matched via: {target.matched_via}")  # noqa: T201
        if target.suggested_aliases:
            joined = ", ".join(f'"{a}"' for a in target.suggested_aliases)
            print(f"      Suggested aliases: {joined}")  # noqa: T201
    print()  # noqa: T201


def _route_label(target: TargetView) -> str:
    name = target.proposal.target_entity
    if target.is_new_entity:
        cat = target.new_entity_category or "?"
        if target.created_earlier_in_session:
            return f"{name} ({cat}) — created earlier this session"
        return f"{name} (NEW — {cat}, will be created on approval)"
    return f"{name} (existing)"


def _play_url(view: BundleView) -> str | None:
    """URL with start timestamp tacked on, derived from the head target."""
    if not view.source_url:
        return None
    try:
        start_seconds, _ = parse_locator_hint(view.targets[0].proposal.locator)
    except TimestampError:
        return view.source_url
    return reading_mod.linkify_timestamp(view.source_url, start_seconds)


def _print_play(view: BundleView) -> None:
    url = _play_url(view)
    if url:
        print(f"  → {url}")  # noqa: T201
    else:
        print("  (no source URL for this bundle)")  # noqa: T201


# ---------------------------------------------------------------------------
# Edit prompts
# ---------------------------------------------------------------------------


_VALID_STATUSES = ("authoritative", "trustworthy", "hearsay", "disproven")


def _prompt_optional(label: str, current: str) -> str | None:
    """Prompt with `current` shown as default; blank → None (keep)."""
    try:
        raw = input(f"    {label} [{current}]: ")
    except EOFError:
        return None
    raw = raw.strip()
    return raw or None


def _prompt_status(current: str) -> str | None:
    """Prompt for a valid status; re-prompt on unknown; blank → keep."""
    options = "/".join(_VALID_STATUSES)
    while True:
        try:
            raw = input(f"    status [{current}] ({options}): ")
        except EOFError:
            return None
        raw = raw.strip()
        if not raw:
            return None
        if raw in _VALID_STATUSES:
            return raw
        print(f"    must be one of: {options}")  # noqa: T201
