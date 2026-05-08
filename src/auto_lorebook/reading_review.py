"""Reading review engine: walk per-segment files, accumulate pending marks, commit.

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
from auto_lorebook import reading_pipeline as pipeline_mod
from auto_lorebook import reading_sidecar as sidecar_mod
from auto_lorebook import segment_file as segment_file_mod
from auto_lorebook.stage1b import AcceptedContextEntry

if TYPE_CHECKING:
    from pathlib import Path

    from auto_lorebook.segment_file import SegmentFile

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
    """Mark segment skipped; body will be rewritten to empty-bullets marker."""


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
    accepted_context: tuple[AcceptedContextEntry, ...]  # snapshot of accepted at commit


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
) -> ReadingReviewResult:
    """Walk segments, collect pending marks, commit on reviewer signal.

    Returns a `ReadingReviewResult` describing what happened. When the gate
    fires (every segment decided), writes the wiki-side `reading.md`.
    """
    wiki_repo = cfg.wiki_repo_path
    info_path = wiki_repo / "sources" / source_id / "info.yaml"
    try:
        info = info_yaml_mod.read(info_path)
    except info_yaml_mod.InfoError as e:
        raise ReadingReviewError(str(e)) from e

    sidecar_path = pipeline_mod.pending_sidecar_path(source_id)
    if not sidecar_path.exists():
        msg = f"No draft reading for {source_id!r}; run `generate-reading` first."
        raise ReadingReviewError(msg)
    try:
        sc = sidecar_mod.read(sidecar_path)
    except sidecar_mod.ReadingSidecarError as e:
        raise ReadingReviewError(str(e)) from e

    segments = pipeline_mod._load_segments(source_id)  # noqa: SLF001
    if not segments:
        msg = f"No segment files found for {source_id!r}."
        raise ReadingReviewError(msg)

    total = len(segments)
    # pending_marks: segment_id → "accepted" | "skipped" | "regenerating"
    pending_marks: dict[str, str] = {}

    # Walk: one pass; reviewer may return UndoDecision to retract a mark.
    for idx, sf in enumerate(segments, start=1):
        fm = sf.frontmatter
        view = _build_view(sf, idx, total, pending_marks, info)
        decision = reviewer.decide_segment(view)
        if isinstance(decision, AcceptDecision):
            pending_marks[fm.segment_id] = "accepted"
        elif isinstance(decision, SkipBulletsDecision):
            pending_marks[fm.segment_id] = "skipped"
        elif isinstance(decision, RegenerateAgainDecision):
            pending_marks[fm.segment_id] = "regenerating"
        elif isinstance(decision, UndoDecision):
            pending_marks.pop(fm.segment_id, None)

    # Build views of segments with a pending mark for decide_quit.
    pending_views = tuple(
        _build_view(sf, idx, total, pending_marks, info)
        for idx, sf in enumerate(segments, start=1)
        if sf.frontmatter.segment_id in pending_marks
    )

    commit = reviewer.decide_quit(pending_views)
    if commit is None:
        # abort — write nothing
        return ReadingReviewResult()

    # Commit transaction: write changed segments, then maybe fire gate.
    result = ReadingReviewResult()
    committed_segments: list[SegmentFile] = []
    for sf in segments:
        sid = sf.frontmatter.segment_id
        mark = pending_marks.get(sid)
        if mark is None:
            # unchanged
            result.unchanged += 1
            committed_segments.append(sf)
        else:
            seg_path = pipeline_mod.pending_segment_path(source_id, sid)
            if mark == "accepted":
                segment_file_mod.set_status(seg_path, "accepted")
                new_sf = segment_file_mod.read(seg_path)
                result.accepted += 1
            elif mark == "skipped":
                # Rewrite body to empty-bullets marker so assembly emits it.
                new_fm = segment_file_mod.SegmentFrontmatter(
                    segment_id=sf.frontmatter.segment_id,
                    segment_status="skipped",
                    start=sf.frontmatter.start,
                    end=sf.frontmatter.end,
                    title=sf.frontmatter.title,
                    speaker=sf.frontmatter.speaker,
                    notes=sf.frontmatter.notes,
                    overrides=list(sf.frontmatter.overrides),
                )
                new_sf = segment_file_mod.SegmentFile(
                    frontmatter=new_fm, body=_EMPTY_BODY
                )
                segment_file_mod.write(new_sf, seg_path)
                result.skipped += 1
            else:  # regenerating
                segment_file_mod.set_status(seg_path, "regenerating")
                new_sf = segment_file_mod.read(seg_path)
                result.regenerating += 1
            committed_segments.append(new_sf)

    # Build regen_batch if any committed segment is regenerating.
    regen_ids = tuple(
        sf.frontmatter.segment_id
        for sf in committed_segments
        if sf.frontmatter.segment_status == "regenerating"
    )
    if regen_ids:
        accepted_entries = tuple(
            AcceptedContextEntry(
                segment_id=sf.frontmatter.segment_id,
                start=sf.frontmatter.start,
                end=sf.frontmatter.end,
                title=sf.frontmatter.title,
                speaker=sf.frontmatter.speaker,
                bullets_body=sf.body,
            )
            for sf in committed_segments
            if sf.frontmatter.segment_status == "accepted"
        )
        result.regen_batch = RegenBatch(
            source_id=source_id,
            regen_segment_ids=regen_ids,
            accepted_context=accepted_entries,
        )

    # Gate: all segments decided?
    effective_statuses = {
        sf.frontmatter.segment_id: sf.frontmatter.segment_status
        for sf in committed_segments
    }
    gate = all(s in {"accepted", "skipped"} for s in effective_statuses.values())
    result.gate_fired = gate

    if gate and result.regen_batch is None:
        text = reading_assembly_mod.assemble(
            segments=committed_segments, sidecar=sc, info=info
        )
        dest = wiki_repo / "sources" / source_id / "reading.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        reading_mod.write(dest, text)
        result.wiki_reading_path = dest
        _logger.info("reading_review: gate fired; wrote %s", dest)

    return result


def _build_view(
    sf: SegmentFile,
    idx: int,
    total: int,
    pending_marks: dict[str, str],
    info: info_yaml_mod.Info,
) -> SegmentView:
    """Build a SegmentView for the given segment."""
    fm = sf.frontmatter
    return SegmentView(
        segment_index=idx,
        segment_total=total,
        segment_id=fm.segment_id,
        title=fm.title,
        start=fm.start,
        end=fm.end,
        speaker=fm.speaker,
        current_status=fm.segment_status,
        pending_mark=pending_marks.get(fm.segment_id),
        bullets_preview=sf.body[:200],
        source_url=info.source_url,
        source_title=info.title,
    )
