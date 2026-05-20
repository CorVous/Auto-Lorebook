"""Reading review engine: walk per-segment DB rows, accumulate pending marks, commit.

Pure-logic module. No `input()` / `print()` calls. Display + prompt I/O lives
in `commands/approve_reading.py` and is injected via the `Reviewer` protocol.
Tests script a `Reviewer` to drive the engine deterministically.

Per-segment terminal states: `draft` (fresh), `accepted`, `skipped`,
`regenerating`. A segment is "decided" if status is `accepted` or `skipped`.

Decisions are deferred — the engine accumulates pending marks during the walk
and commits in one transaction at `decide_quit` time. Gate predicate: every
segment is decided. On gate fire, `reading_assembly.assemble` is invoked and
the wiki-side `reading.md` is written.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from auto_lorebook import config as cfg_mod
from auto_lorebook import info_yaml as info_yaml_mod
from auto_lorebook import reading as reading_mod
from auto_lorebook import reading_assembly as reading_assembly_mod
from auto_lorebook import reading_sidecar as sidecar_mod
from auto_lorebook import structure_store as structure_store_mod
from auto_lorebook.stage1b import AcceptedContextEntry

if TYPE_CHECKING:
    from pathlib import Path

    from auto_lorebook.structure_store import SegmentRow

_logger = logging.getLogger(__name__)

_EMPTY_BODY = "_No claims extracted from this segment._\n"

# ------------- error --------------------------------------------------------


class ReadingReviewError(RuntimeError):
    """Unrecoverable engine failure."""


# ------------- decisions ----------------------------------------------------


@dataclass(frozen=True)
class AcceptDecision:
    """Mark segment accepted."""


@dataclass(frozen=True)
class SkipBulletsDecision:
    """Mark segment skipped; bullets cleared."""


@dataclass(frozen=True)
class RegenerateAgainDecision:
    """Mark segment regenerating (no-op in this slice; gate blocks)."""


@dataclass(frozen=True)
class UndoDecision:
    """Clear the pending mark for this segment."""


@dataclass(frozen=True)
class CommitDecision:
    """Commit pending marks and quit."""


SegmentDecision = (
    AcceptDecision | SkipBulletsDecision | RegenerateAgainDecision | UndoDecision
)

# ------------- views / result -----------------------------------------------


@dataclass(frozen=True)
class SegmentView:
    """Display data passed to the reviewer per segment."""

    segment_index: int
    segment_total: int
    segment_id: str
    title: str
    start: float
    end: float
    speaker: str
    current_status: str
    pending_mark: str | None  # status queued this walk; None if untouched
    bullets_preview: str
    source_url: str | None
    source_title: str | None


@dataclass(frozen=True)
class RegenBatch:
    """Segments queued for quit-time regeneration."""

    source_id: str
    regen_segment_ids: tuple[str, ...]
    accepted_context: tuple[AcceptedContextEntry, ...]


@dataclass
class ReadingReviewResult:
    """Counts per terminal status after a run."""

    accepted: int = 0
    skipped: int = 0
    regenerating: int = 0
    unchanged: int = 0
    gate_fired: bool = False
    wiki_reading_path: Path | None = None
    regen_batch: RegenBatch | None = None


# ------------- reviewer protocol --------------------------------------------


class Reviewer(Protocol):
    """Decision-maker injected into `run`. Tests script this directly."""

    @property
    def by_label(self) -> str:
        """Recorded for parity with review.Reviewer; reserved for future slices."""
        ...

    def decide_segment(self, view: SegmentView) -> SegmentDecision:
        """Return a decision for one segment during the walk."""
        ...

    def decide_quit(self, pending: tuple[SegmentView, ...]) -> CommitDecision | None:
        """Return CommitDecision to commit, None to abort."""
        ...


# ------------- engine -------------------------------------------------------


def run(
    *,
    cfg: cfg_mod.Config,
    source_id: str,
    reviewer: Reviewer,
    wiki_override: str | None = None,
) -> ReadingReviewResult:
    """Walk segments from DB, collect pending marks, commit on reviewer signal.

    Returns a `ReadingReviewResult` describing what happened. When the gate
    fires (every segment decided), writes the wiki-side `reading.md`.
    """
    from auto_lorebook import db as db_mod  # noqa: PLC0415
    from auto_lorebook import wiki_state as wiki_state_mod  # noqa: PLC0415

    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    conn = db_mod.open(wiki_state_mod.wiki_db_path(wiki_repo))
    try:
        info_conn = conn
        try:
            info = info_yaml_mod.read(info_conn, source_id, wiki_repo=wiki_repo)
        except info_yaml_mod.InfoError as e:
            raise ReadingReviewError(str(e)) from e

        if not sidecar_mod.exists(conn, source_id):
            msg = f"No draft reading for {source_id!r}; run `generate-reading` first."
            raise ReadingReviewError(msg)
        try:
            sc = sidecar_mod.read_state(conn, source_id)
        except sidecar_mod.ReadingSidecarError as e:
            raise ReadingReviewError(str(e)) from e

        segments = structure_store_mod.list_segments(conn, source_id)
        if not segments:
            msg = f"No segments found for {source_id!r}."
            raise ReadingReviewError(msg)

        total = len(segments)
        bullets_map = structure_store_mod.read_bullets(conn, source_id)
        # pending_marks: segment_id → "accepted" | "skipped" | "regenerating"
        pending_marks: dict[str, str] = {}

        # Walk: one pass; reviewer may return UndoDecision to retract a mark.
        for idx, seg in enumerate(segments, start=1):
            view = _build_view(seg, idx, total, pending_marks, info, bullets_map)
            decision = reviewer.decide_segment(view)
            if isinstance(decision, AcceptDecision):
                pending_marks[seg.segment_id] = "accepted"
            elif isinstance(decision, SkipBulletsDecision):
                pending_marks[seg.segment_id] = "skipped"
            elif isinstance(decision, RegenerateAgainDecision):
                pending_marks[seg.segment_id] = "regenerating"
            elif isinstance(decision, UndoDecision):
                pending_marks.pop(seg.segment_id, None)

        # Build views of segments with a pending mark for decide_quit.
        pending_views = tuple(
            _build_view(seg, idx, total, pending_marks, info, bullets_map)
            for idx, seg in enumerate(segments, start=1)
            if seg.segment_id in pending_marks
        )

        commit = reviewer.decide_quit(pending_views)
        if commit is None:
            return ReadingReviewResult()

        # Commit transaction: update DB statuses, then maybe fire gate.
        result = ReadingReviewResult()
        # Track committed states for gate evaluation and assembly.
        committed_states: dict[str, str] = {}
        for seg in segments:
            sid = seg.segment_id
            mark = pending_marks.get(sid)
            if mark is None:
                result.unchanged += 1
                committed_states[sid] = seg.segment_status
            else:
                if mark == "accepted":
                    structure_store_mod.set_segment_status(
                        conn, source_id, sid, "accepted"
                    )
                    result.accepted += 1
                elif mark == "skipped":
                    structure_store_mod.set_segment_status(
                        conn, source_id, sid, "skipped"
                    )
                    # clear bullets for skipped segment
                    structure_store_mod.write_segment_bullets(conn, source_id, sid, [])
                    result.skipped += 1
                else:  # regenerating
                    structure_store_mod.set_segment_status(
                        conn, source_id, sid, "regenerating"
                    )
                    result.regenerating += 1
                committed_states[sid] = mark

        # Build regen_batch if any committed segment is regenerating.
        regen_ids = tuple(
            sid for sid, status in committed_states.items() if status == "regenerating"
        )
        if regen_ids:
            # Refresh bullets_map after skipped clears.
            fresh_bullets = structure_store_mod.read_bullets(conn, source_id)
            accepted_entries = tuple(
                AcceptedContextEntry(
                    segment_id=seg.segment_id,
                    start=float(_ts(seg.start)),
                    end=float(_ts(seg.end)),
                    title=seg.title,
                    speaker=seg.speaker or "",
                    bullets_body=_bullets_body(
                        fresh_bullets.segments.get(seg.segment_id, []),
                        info.source_url,
                        sc.name_corrections,
                    ),
                )
                for seg in segments
                if committed_states.get(seg.segment_id) == "accepted"
            )
            result.regen_batch = RegenBatch(
                source_id=source_id,
                regen_segment_ids=regen_ids,
                accepted_context=accepted_entries,
            )

        # Gate: all segments decided?
        gate = all(s in {"accepted", "skipped"} for s in committed_states.values())
        result.gate_fired = gate

        if gate and result.regen_batch is None:
            # Reload state after mutations for assembly.
            sc_fresh = sidecar_mod.read_state(conn, source_id)
            text = reading_assembly_mod.assemble(
                conn=conn, ingest_id=source_id, info=info, sidecar=sc_fresh
            )
            dest = wiki_repo / "sources" / source_id / "reading.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            reading_mod.write(dest, text)
            result.wiki_reading_path = dest
            _logger.info("reading_review: gate fired; wrote %s", dest)

        conn.commit()
        return result
    finally:
        conn.close()


def _ts(ts_str: str) -> float:
    """Parse h:mm:ss to float."""
    from auto_lorebook.timestamps import parse_timestamp  # noqa: PLC0415

    return parse_timestamp(ts_str)


def _bullets_body(
    bullets: list,
    source_url: str | None,
    name_corrections: dict[str, str],
) -> str:
    """Render bullets to body string for AcceptedContextEntry."""
    from auto_lorebook.timestamps import format_timestamp  # noqa: PLC0415

    if not bullets:
        return _EMPTY_BODY
    parts: list[str] = []
    for b in bullets:
        text = reading_mod.apply_name_corrections(b.text, name_corrections)
        anchor_ts = format_timestamp(b.anchor)
        link = reading_mod.linkify_timestamp(source_url, b.anchor)
        if link:
            parts.append(f"- {text} [[{anchor_ts}]]({link})")
        else:
            parts.append(f"- {text} [{anchor_ts}]")
    return "\n".join(parts) + "\n"


def _build_view(
    seg: SegmentRow,
    idx: int,
    total: int,
    pending_marks: dict[str, str],
    info: info_yaml_mod.Info,
    bullets_map: object,
) -> SegmentView:
    """Build a SegmentView for the given segment."""
    from auto_lorebook.timestamps import parse_timestamp  # noqa: PLC0415

    seg_bullets = getattr(bullets_map, "segments", {}).get(seg.segment_id, [])
    preview = _bullets_body(seg_bullets, info.source_url, {})[:200]
    return SegmentView(
        segment_index=idx,
        segment_total=total,
        segment_id=seg.segment_id,
        title=seg.title,
        start=parse_timestamp(seg.start),
        end=parse_timestamp(seg.end),
        speaker=seg.speaker or "",
        current_status=seg.segment_status,
        pending_mark=pending_marks.get(seg.segment_id),
        bullets_preview=preview,
        source_url=info.source_url,
        source_title=info.title,
    )
