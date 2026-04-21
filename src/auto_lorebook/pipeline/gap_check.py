"""Mechanical gap-check heuristic for low-yield transcript stretches."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

from auto_lorebook.sources.srt import ts_to_seconds

_LOW_YIELD: frozenset[str] = frozenset({
    "rules discussion",
    "break",
    "off-topic",
    "silence",
    "pizza",
    "inaudible",
})


def _is_low_yield(segment: dict[str, object]) -> bool:
    title = str(segment.get("title") or "").lower()
    notes = str(segment.get("notes") or "").lower()
    return any(p in title or p in notes for p in _LOW_YIELD)


@dataclass(slots=True)
class GapWarning:
    """Low-yield stretch warning."""

    start: str
    end: str
    duration_seconds: float
    segment_ids: list[str] = field(default_factory=list)


def check_gaps(
    structure: dict[str, object],
    *,
    threshold_seconds: float = 300.0,
) -> list[GapWarning]:
    """Return gap warnings for contiguous low-yield stretches above threshold.

    This is a warning-only check — it does not block the pipeline.

    :param structure: parsed structure.yaml dict
    :param threshold_seconds: minimum stretch duration to warn on (default 5 min)
    :return: list of GapWarning (empty if none found)
    """
    raw_segs = structure.get("segments")
    segments: list[dict[str, object]] = cast(
        "list[dict[str, object]]",
        [cast("dict[str, object]", s) if isinstance(s, dict) else {} for s in raw_segs]  # type: ignore[union-attr]
        if isinstance(raw_segs, list)
        else [],
    )
    warnings: list[GapWarning] = []

    run_start: str | None = None
    run_start_secs: float = 0.0
    run_ids: list[str] = []

    for seg in segments:
        seg_start = str(seg.get("start", "0:00:00"))
        seg_id = str(seg.get("id", ""))

        if _is_low_yield(seg):
            if run_start is None:
                run_start = seg_start
                run_start_secs = ts_to_seconds(seg_start)
            run_ids.append(seg_id)
        else:
            if run_start is not None:
                run_end_secs = ts_to_seconds(seg_start)
                duration = run_end_secs - run_start_secs
                if duration >= threshold_seconds:
                    warnings.append(
                        GapWarning(
                            start=run_start,
                            end=seg_start,
                            duration_seconds=duration,
                            segment_ids=list(run_ids),
                        )
                    )
            run_start = None
            run_start_secs = 0.0
            run_ids = []

    # Flush any trailing low-yield run
    if run_start is not None and segments:
        last_end = str(segments[-1].get("end", "0:00:00"))
        duration = ts_to_seconds(last_end) - run_start_secs
        if duration >= threshold_seconds:
            warnings.append(
                GapWarning(
                    start=run_start,
                    end=last_end,
                    duration_seconds=duration,
                    segment_ids=list(run_ids),
                )
            )

    return warnings
