"""auto-lorebook approve-reading subcommand."""

from __future__ import annotations

import logging
import os
import subprocess  # noqa: S404
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import info_yaml as info_yaml_mod
from auto_lorebook import reading_pipeline as pipeline
from auto_lorebook import reading_sidecar as sidecar_mod
from auto_lorebook import segment_file as segment_file_mod
from auto_lorebook.interactive import _is_interactive
from auto_lorebook.reading_review import (
    AcceptDecision,
    CommitDecision,
    RegenerateAgainDecision,
    SegmentView,
    SkipBulletsDecision,
    UndoDecision,
)
from auto_lorebook.reading_sidecar import ReadingSidecarError
from auto_lorebook.timestamps import format_timestamp

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from auto_lorebook.gap_check import GapWarning
    from auto_lorebook.reading_review import SegmentDecision

_logger = logging.getLogger(__name__)

_OUTER_PROMPT = "[#] open  [n] next-draft  [m] meta  [q] quit\n> "
_SEG_PROMPT = (
    "[a] accept  [e] edit  [s] skip-bullets  [g] regenerate-again"
    "  [u] undo  [b] back\n> "
)


class AutoAcceptReviewer:
    """Marks every still-draft segment accepted, then commits unconditionally.

    Used by `--yes` (non-interactive) path and `reading_pipeline.approve`.
    """

    by_label = "auto-accept"

    def decide_segment(self, view: SegmentView) -> SegmentDecision:
        # Already terminal on disk — don't overwrite with a pending mark.
        if view.current_status in {"accepted", "skipped", "regenerating"}:
            return UndoDecision()
        return AcceptDecision()

    def decide_quit(
        self,
        pending: tuple[SegmentView, ...],  # noqa: ARG002
    ) -> CommitDecision:
        return CommitDecision()


@dataclass
class _SegSummary:
    """Flat summary for outer list view."""

    segment_id: str
    title: str
    start: float
    end: float
    speaker: str
    current_status: str  # disk truth: draft|accepted|skipped|regenerating


# keyed by segment_id; value in {"accepted","skipped","regenerating"}
_PendingMarks = dict[str, str]


class _ReplayReviewer:
    """Drives engine with pre-recorded per-segment decisions."""

    by_label = "interactive"

    def __init__(self, marks: _PendingMarks) -> None:
        self._marks = dict(marks)

    def decide_segment(self, view: SegmentView) -> SegmentDecision:
        mark = self._marks.get(view.segment_id)
        if mark == "accepted":
            return AcceptDecision()
        if mark == "skipped":
            return SkipBulletsDecision()
        if mark == "regenerating":
            return RegenerateAgainDecision()
        return UndoDecision()

    def decide_quit(self, pending: tuple[SegmentView, ...]) -> CommitDecision:  # noqa: ARG002
        return CommitDecision()


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the approve-reading subcommand."""
    parser = subparsers.add_parser(
        "approve-reading",
        parents=[common_parser],
        help="Interactively approve or skip draft reading segments",
        description=(
            "Opens a hierarchical interactive session over the draft reading. "
            "Outer view: numbered segment list with status; "
            "keys [#] (open segment N), [n] (next draft), "
            "[m] (open reading.yaml in $EDITOR), "
            "[q] (commit pending marks; if every segment is decided, "
            "write wiki-side reading.md). "
            "Per-segment prompt: [a] accept, [e] edit body in $EDITOR, "
            "[s] skip-bullets, [u] undo this segment, [b] back. "
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
        return _approve_only(cfg, args.source_id)

    return _interactive_session(cfg, args.source_id)


def _approve_only(cfg: cfg_mod.Config, source_id: str) -> int:
    """Approve reading: assemble + copy to wiki."""
    try:
        approved = pipeline.approve(cfg, source_id)
    except pipeline.ReadingPipelineError as e:
        _logger.error("%s", e)
        return 1

    print(f"Approved: {approved}")  # noqa: T201
    print(f"Run `auto-lorebook plan {source_id}` next.")  # noqa: T201
    return 0


def _load_summaries(source_id: str) -> list[_SegSummary]:
    """Load frontmatter from all seg-NNN.md files, sorted by filename."""
    segs_dir = pipeline.pending_segments_dir(source_id)
    if not segs_dir.exists():
        return []
    paths = sorted(segs_dir.glob("*.md"))
    out = []
    for p in paths:
        sf = segment_file_mod.read(p)
        fm = sf.frontmatter
        out.append(
            _SegSummary(
                segment_id=fm.segment_id,
                title=fm.title,
                start=fm.start,
                end=fm.end,
                speaker=fm.speaker,
                current_status=fm.segment_status,
            )
        )
    return out


def _render_gap_warnings(warnings: list[GapWarning]) -> None:
    """Print gap-warning blocks sorted by start. No output when empty."""
    if not warnings:
        return
    # below segment list, above prompt — keeps warnings in last-screen view
    print()  # noqa: T201
    for idx, w in enumerate(sorted(warnings, key=lambda x: x.start)):
        if idx > 0:
            print()  # noqa: T201
        titles = ", ".join(f'"{t}"' for t in w.segment_titles)
        start_ts = format_timestamp(w.start)
        end_ts = format_timestamp(w.end)
        print("⚠ Possible coverage gap:")  # noqa: T201
        print(f"  {start_ts}–{end_ts} covered only by segments titled")  # noqa: T201, RUF001
        print(f"  {titles}.")  # noqa: T201
        print("  If this stretch contained worldbuilding, regenerate with a hint.")  # noqa: T201


def _render_outer(
    source_id: str,
    summaries: list[_SegSummary],
    pending_marks: _PendingMarks,
    source_title: str | None,
    gap_warnings: list[GapWarning] | None = None,
) -> None:
    title_str = source_title or source_id
    print(f"\nReading: {source_id} — {title_str}")  # noqa: T201
    for i, s in enumerate(summaries, start=1):
        start_ts = format_timestamp(s.start)
        end_ts = format_timestamp(s.end)
        mark = pending_marks.get(s.segment_id)
        arrow = f" →{mark}" if mark else ""
        print(  # noqa: T201
            f"  {i:>3}. [{s.current_status}]  {start_ts}–{end_ts}  "  # noqa: RUF001
            f"{s.title}  ({s.speaker}){arrow}"
        )
    _render_gap_warnings(gap_warnings or [])
    print(_OUTER_PROMPT, end="", flush=True)  # noqa: T201


def _render_segment(
    summary: _SegSummary,
    pending_mark: str | None,
    body: str,
) -> None:
    start_ts = format_timestamp(summary.start)
    end_ts = format_timestamp(summary.end)
    pending_str = pending_mark or "?"
    print(  # noqa: T201
        f"\n{summary.segment_id} "
        f"[{summary.current_status} → {pending_str}]  "
        f"{start_ts}–{end_ts}  {summary.title}  ({summary.speaker})"  # noqa: RUF001
    )
    lines = body.splitlines()
    limit = 60
    if len(lines) <= limit:
        print(body)  # noqa: T201
    else:
        print("\n".join(lines[:limit]))  # noqa: T201
        print(f"... ({len(lines) - limit} more lines)")  # noqa: T201
    print(_SEG_PROMPT, end="", flush=True)  # noqa: T201


def _open_in_editor(path: Path) -> None:
    editor = os.environ.get("EDITOR", "vi")
    subprocess.run([editor, str(path)], check=False)  # noqa: S603


def _pick_segment_index(raw: str, total: int) -> int | None:
    """Parse 1-based digit string → 0-based index, or None if out of range."""
    if not raw.isdigit():
        return None
    one_based = int(raw)
    if 1 <= one_based <= total:
        return one_based - 1
    return None


def _next_draft_index(
    summaries: list[_SegSummary],
    pending_marks: _PendingMarks,
    start_at: int = 0,
) -> int | None:
    """First index ≥ start_at that is draft and not yet pending; wraps once."""
    n = len(summaries)
    for offset in range(n):
        idx = (start_at + offset) % n
        s = summaries[idx]
        if s.current_status == "draft" and s.segment_id not in pending_marks:
            return idx
    return None


def _per_segment_prompt(
    source_id: str,
    idx: int,
    summaries: list[_SegSummary],
    pending_marks: _PendingMarks,
) -> None:
    """Inner per-segment loop; mutates pending_marks in place."""
    summary = summaries[idx]
    sid = summary.segment_id

    while True:
        # re-read body each iteration so edits show
        seg_path = pipeline.pending_segment_path(source_id, sid)
        sf = segment_file_mod.read(seg_path)
        _render_segment(summary, pending_marks.get(sid), sf.body)

        try:
            choice = input("").strip().lower()
        except EOFError:
            return
        # KeyboardInterrupt propagates to outer

        if choice == "a":
            pending_marks[sid] = "accepted"
            return
        if choice == "s":
            pending_marks[sid] = "skipped"
            return
        if choice == "g":
            pending_marks[sid] = "regenerating"
            return
        if choice == "e":
            _open_in_editor(pipeline.pending_segment_path(source_id, sid))
            # reload summary so edits show (status might have changed externally)
            summaries[idx] = _load_summaries(source_id)[idx]
            summary = summaries[idx]
        elif choice == "u":
            pending_marks.pop(sid, None)
            # stay in segment prompt
        elif choice == "b":
            return
        # unrecognized → re-prompt


def _open_meta(source_id: str) -> None:
    _open_in_editor(pipeline.pending_sidecar_path(source_id))


def _commit_and_exit(
    cfg: cfg_mod.Config,
    source_id: str,
    pending_marks: _PendingMarks,
) -> int:
    from auto_lorebook import reading_review as reading_review_mod  # noqa: PLC0415

    try:
        result = reading_review_mod.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=_ReplayReviewer(pending_marks),
        )
    except reading_review_mod.ReadingReviewError as e:
        _logger.error("%s", e)
        return 1

    if result.gate_fired and result.wiki_reading_path is not None:
        print(f"Approved: {result.wiki_reading_path}")  # noqa: T201
        print(f"Run `auto-lorebook plan {source_id}` next.")  # noqa: T201
        return 0

    if result.regen_batch is not None:
        n = len(result.regen_batch.regen_segment_ids)
        print(f"Regenerating {n} segment(s)...")  # noqa: T201
        try:
            pipeline.regenerate_after_review(cfg, result.regen_batch)
        except pipeline.ReadingPipelineError as e:
            _logger.error("%s", e)
            return 1

    remaining = sum(
        1
        for s in _load_summaries(source_id)
        if s.current_status not in {"accepted", "skipped"}
    )
    print(f"Still {remaining} undecided; pending marks committed for the rest.")  # noqa: T201
    return 0


def _interactive_session(cfg: cfg_mod.Config, source_id: str) -> int:
    sidecar_path = pipeline.pending_sidecar_path(source_id)
    if not sidecar_path.exists():
        _logger.error(
            "No draft reading for %r. Run `generate-reading` first.", source_id
        )
        return 1

    # load source title for header
    source_title: str | None = None
    try:
        info_path = cfg.wiki_repo_path / "sources" / source_id / "info.yaml"
        info = info_yaml_mod.read(info_path)
        source_title = info.title
    except info_yaml_mod.InfoError:
        pass

    # load gap_warnings from sidecar; fall back to [] on parse failure
    gap_warnings: list[GapWarning] = []
    try:
        sc = sidecar_mod.read(sidecar_path)
        gap_warnings = sc.gap_warnings
    except ReadingSidecarError:
        pass

    summaries = _load_summaries(source_id)
    pending_marks: _PendingMarks = {}

    while True:
        _render_outer(source_id, summaries, pending_marks, source_title, gap_warnings)

        try:
            choice = input("").strip().lower()
        except EOFError:
            choice = "q"
        except KeyboardInterrupt:
            print()  # noqa: T201
            return 130

        if choice.isdigit():
            idx = _pick_segment_index(choice, len(summaries))
            if idx is None:
                continue
            try:
                _per_segment_prompt(source_id, idx, summaries, pending_marks)
            except KeyboardInterrupt:
                print()  # noqa: T201
                return 130
            summaries = _load_summaries(source_id)
        elif choice == "n":
            idx = _next_draft_index(summaries, pending_marks)
            if idx is None:
                print("  (no remaining draft segments)")  # noqa: T201
                continue
            try:
                _per_segment_prompt(source_id, idx, summaries, pending_marks)
            except KeyboardInterrupt:
                print()  # noqa: T201
                return 130
            summaries = _load_summaries(source_id)
        elif choice == "m":
            _open_meta(source_id)
        elif choice == "q":
            return _commit_and_exit(cfg, source_id, pending_marks)
        # unrecognized → re-prompt
