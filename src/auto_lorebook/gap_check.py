"""Mechanical gap-check heuristic over a Structure.

Surfaces contiguous stretches of low-yield segments longer than a
threshold. No LLM involvement; purely deterministic pattern matching
over segment titles and notes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook.timestamps import format_timestamp

if TYPE_CHECKING:
    from collections.abc import Iterable

    from auto_lorebook.structure import Segment, Structure

DEFAULT_THRESHOLD_SECONDS = 300.0

DEFAULT_LOW_YIELD_PATTERNS: tuple[str, ...] = (
    "rules",
    "break",
    "off-topic",
    "off topic",
    "silence",
    "inaudible",
    "pizza",
    "snack",
    "tangent",
    "aside",
)


@dataclass(frozen=True)
class GapWarning:
    """One contiguous low-yield stretch over threshold."""

    start: float
    end: float
    segment_ids: tuple[str, ...]
    segment_titles: tuple[str, ...]

    @property
    def duration(self) -> float:
        return self.end - self.start

    def format_warning(self) -> str:
        """Human-readable one-liner for reading review."""
        titles = ", ".join(f'"{t}"' for t in self.segment_titles)
        return (
            f"Possible coverage gap: {format_timestamp(self.start)}-"
            f"{format_timestamp(self.end)} covered only by segments "
            f"titled {titles}."
        )


def check(
    structure: Structure,
    *,
    threshold_seconds: float = DEFAULT_THRESHOLD_SECONDS,
    low_yield_patterns: Iterable[str] | None = None,
) -> list[GapWarning]:
    """Scan segments for maximal low-yield runs longer than threshold."""
    patterns = tuple(
        p.lower() for p in (low_yield_patterns or DEFAULT_LOW_YIELD_PATTERNS)
    )
    warnings: list[GapWarning] = []
    run: list[Segment] = []

    def flush() -> None:
        if not run:
            return
        duration = run[-1].end - run[0].start
        if duration > threshold_seconds:
            warnings.append(
                GapWarning(
                    start=run[0].start,
                    end=run[-1].end,
                    segment_ids=tuple(s.id for s in run),
                    segment_titles=tuple(s.title for s in run),
                )
            )

    for seg in structure.segments:
        if _is_low_yield(seg, patterns):
            run.append(seg)
        else:
            flush()
            run = []
    flush()
    return warnings


def _is_low_yield(seg: Segment, patterns: tuple[str, ...]) -> bool:
    haystack = (seg.title + " " + (seg.notes or "")).lower()
    return any(p in haystack for p in patterns)
