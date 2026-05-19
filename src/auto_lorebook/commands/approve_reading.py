"""auto-lorebook approve-reading subcommand."""

from __future__ import annotations

import logging
import os
import subprocess  # noqa: S404
from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import db as db_mod
from auto_lorebook import info_yaml as info_yaml_mod
from auto_lorebook import reading_pipeline as pipeline
from auto_lorebook import reading_sidecar as sidecar_mod
from auto_lorebook import structure_store as structure_store_mod
from auto_lorebook import wiki_state as wiki_state_mod
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
from auto_lorebook.timestamps import format_timestamp, parse_timestamp

if TYPE_CHECKING:
    import argparse

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
            "[m] (open sidecar meta in $EDITOR), "
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

    wiki_override: str | None = getattr(args, "wiki", None)

    if args.yes:
        return _approve_only(cfg, args.source_id, wiki_override=wiki_override)

    return _interactive_session(cfg, args.source_id, wiki_override=wiki_override)


def _approve_only(
    cfg: cfg_mod.Config,
    source_id: str,
    wiki_override: str | None = None,
) -> int:
    """Approve reading: assemble + copy to wiki."""
    try:
        approved = pipeline.approve(cfg, source_id, wiki_override=wiki_override)
    except pipeline.ReadingPipelineError as e:
        _logger.error("%s", e)
        return 1

    print(f"Approved: {approved}")  # noqa: T201
    print(f"Run `auto-lorebook plan {source_id}` next.")  # noqa: T201
    return 0


def _load_summaries(
    cfg: cfg_mod.Config,
    source_id: str,
    wiki_override: str | None = None,
) -> list[_SegSummary]:
    """Load segment summaries from DB."""
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        rows = structure_store_mod.list_segments(conn, source_id)
    finally:
        conn.close()
    return [
        _SegSummary(
            segment_id=r.segment_id,
            title=r.title,
            start=parse_timestamp(r.start),
            end=parse_timestamp(r.end),
            speaker=r.speaker or "",
            current_status=r.segment_status,
        )
        for r in rows
    ]


def _render_gap_warnings(warnings: list[GapWarning]) -> None:
    """Print gap-warning blocks sorted by start. No output when empty."""
    if not warnings:
        return
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


def _open_in_editor(path: object) -> None:
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


def _load_segment_body(
    cfg: cfg_mod.Config,
    source_id: str,
    segment_id: str,
    wiki_override: str | None = None,
) -> str:
    """Render segment body from DB for interactive display."""
    from auto_lorebook import reading_assembly as assembly_mod  # noqa: PLC0415

    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        seg_row = structure_store_mod.get_segment(conn, source_id, segment_id)
        if seg_row is None:
            return ""
        info = info_yaml_mod.read(conn, source_id, wiki_repo=wiki_repo)
        sc = sidecar_mod.read_state(conn, source_id)
        bullets_map = structure_store_mod.read_bullets(conn, source_id)
        seg_bullets = bullets_map.segments.get(segment_id, [])
        flags = list(seg_row.flags)
        return assembly_mod.build_segment_body(
            seg_bullets=seg_bullets,
            flags=flags,
            source_url=info.source_url,
            name_corrections=sc.name_corrections,
        )
    finally:
        conn.close()


def _per_segment_prompt(
    cfg: cfg_mod.Config,
    source_id: str,
    idx: int,
    summaries: list[_SegSummary],
    pending_marks: _PendingMarks,
    wiki_override: str | None = None,
) -> None:
    """Inner per-segment loop; mutates pending_marks in place."""
    summary = summaries[idx]
    sid = summary.segment_id

    while True:
        body = _load_segment_body(cfg, source_id, sid, wiki_override)
        _render_segment(summary, pending_marks.get(sid), body)

        try:
            choice = input("").strip().lower()
        except EOFError:
            return

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
            # Open a tempfile with the segment body for editing.
            import pathlib  # noqa: PLC0415
            import tempfile  # noqa: PLC0415

            with tempfile.NamedTemporaryFile(
                suffix=f"-{sid}.md", mode="w", encoding="utf-8", delete=False
            ) as tf:
                tf.write(body)
                tmp_path = pathlib.Path(tf.name)
            try:
                _open_in_editor(tmp_path)
                # Body edits are display-only; bullets still come from DB.
            finally:
                tmp_path.unlink(missing_ok=True)
            # reload summary
            fresh = _load_summaries(cfg, source_id, wiki_override)
            summaries[idx] = fresh[idx]
            summary = summaries[idx]
        elif choice == "u":
            pending_marks.pop(sid, None)
        elif choice == "b":
            return


def _open_meta(
    cfg: cfg_mod.Config,
    source_id: str,
    wiki_override: str | None = None,
) -> None:
    """Open a tempfile YAML with sidecar meta; write back on save."""
    import tempfile  # noqa: PLC0415

    import yaml  # noqa: PLC0415

    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        sc = sidecar_mod.read_state(conn, source_id)
    finally:
        conn.close()

    data = {
        "default_speaker": sc.default_speaker,
        "name_corrections": dict(sc.name_corrections),
        "session_date": sc.session_date,
    }
    import pathlib  # noqa: PLC0415

    with tempfile.NamedTemporaryFile(
        suffix="-meta.yaml", mode="w", encoding="utf-8", delete=False
    ) as tf:
        yaml.safe_dump(data, tf, allow_unicode=True, sort_keys=False)
        tmp_path = pathlib.Path(tf.name)
    try:
        _open_in_editor(tmp_path)
        if tmp_path.exists():
            text = tmp_path.read_text(encoding="utf-8")
            try:
                updated = yaml.safe_load(text)
                if isinstance(updated, dict):
                    conn2 = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
                    try:
                        sidecar_mod.write_state(
                            conn2,
                            source_id,
                            default_speaker=str(
                                updated.get("default_speaker") or sc.default_speaker
                            ),
                            name_corrections={
                                str(k): str(v)
                                for k, v in (
                                    updated.get("name_corrections") or {}
                                ).items()
                            },
                            session_date=updated.get("session_date"),
                        )
                        conn2.commit()
                    finally:
                        conn2.close()
            except Exception:  # noqa: BLE001
                _logger.debug("meta edit: could not parse YAML; ignoring")
    finally:
        tmp_path.unlink(missing_ok=True)


def _commit_and_exit(
    cfg: cfg_mod.Config,
    source_id: str,
    pending_marks: _PendingMarks,
    wiki_override: str | None = None,
) -> int:
    from auto_lorebook import reading_review as reading_review_mod  # noqa: PLC0415

    try:
        result = reading_review_mod.run(
            cfg=cfg,
            source_id=source_id,
            reviewer=_ReplayReviewer(pending_marks),
            wiki_override=wiki_override,
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
            pipeline.regenerate_after_review(
                cfg, result.regen_batch, wiki_override=wiki_override
            )
        except pipeline.ReadingPipelineError as e:
            _logger.error("%s", e)
            return 1

    summaries = _load_summaries(cfg, source_id, wiki_override)
    remaining = sum(
        1 for s in summaries if s.current_status not in {"accepted", "skipped"}
    )
    print(f"Still {remaining} undecided; pending marks committed for the rest.")  # noqa: T201
    return 0


def _interactive_session(
    cfg: cfg_mod.Config,
    source_id: str,
    wiki_override: str | None = None,
) -> int:
    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        has_state = sidecar_mod.exists(conn, source_id)
    finally:
        conn.close()

    if not has_state:
        _logger.error(
            "No draft reading for %r. Run `generate-reading` first.", source_id
        )
        return 1

    # load source title for header
    source_title: str | None = None
    try:
        conn2 = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
        try:
            info = info_yaml_mod.read(conn2, source_id, wiki_repo=wiki_repo)
            source_title = info.title
        finally:
            conn2.close()
    except info_yaml_mod.InfoError:
        pass

    # load gap_warnings from DB sidecar
    gap_warnings: list[GapWarning] = []
    try:
        conn3 = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
        try:
            sc = sidecar_mod.read_state(conn3, source_id)
            gap_warnings = sc.gap_warnings
        finally:
            conn3.close()
    except ReadingSidecarError:
        pass

    summaries = _load_summaries(cfg, source_id, wiki_override)
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
                _per_segment_prompt(
                    cfg, source_id, idx, summaries, pending_marks, wiki_override
                )
            except KeyboardInterrupt:
                print()  # noqa: T201
                return 130
            summaries = _load_summaries(cfg, source_id, wiki_override)
        elif choice == "n":
            idx = _next_draft_index(summaries, pending_marks)
            if idx is None:
                print("  (no remaining draft segments)")  # noqa: T201
                continue
            try:
                _per_segment_prompt(
                    cfg, source_id, idx, summaries, pending_marks, wiki_override
                )
            except KeyboardInterrupt:
                print()  # noqa: T201
                return 130
            summaries = _load_summaries(cfg, source_id, wiki_override)
        elif choice == "m":
            _open_meta(cfg, source_id, wiki_override)
        elif choice == "q":
            return _commit_and_exit(
                cfg, source_id, pending_marks, wiki_override=wiki_override
            )
