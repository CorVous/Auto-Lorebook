"""Regeneration-set planner for Stage 4 page step.

Pure function — no I/O, no sqlite3 import.

Public API:
    RegenerationSet, plan_regeneration_set
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


@dataclass(frozen=True)
class RegenerationSet:
    """Ordered entity set for page regeneration.

    touched: deduped input entities, original order preserved.
    linked:  one-hop co-targets not in touched, sorted (category, slug).
    """

    touched: tuple[tuple[str, str], ...]
    linked: tuple[tuple[str, str], ...]

    @property
    def ordered(self) -> tuple[tuple[str, str], ...]:
        """Touched first, then linked."""
        return self.touched + self.linked


def plan_regeneration_set(
    touched: Iterable[tuple[str, str]],
    linked_of: Callable[[tuple[str, str]], Iterable[tuple[str, str]]],
) -> RegenerationSet:
    """Build RegenerationSet: touched (deduped, order-preserved) + one-hop linked.

    linked_of: called once per touched entity, returns its co-targets.
    One hop, non-transitive — linked_of never called on linked entities.
    """
    seen: set[tuple[str, str]] = set()
    touched_deduped: list[tuple[str, str]] = []
    for entity in touched:
        if entity not in seen:
            seen.add(entity)
            touched_deduped.append(entity)

    linked_set: set[tuple[str, str]] = set()
    for entity in touched_deduped:
        for linked_entity in linked_of(entity):
            if linked_entity not in seen:
                linked_set.add(linked_entity)

    linked_sorted = sorted(linked_set)

    return RegenerationSet(
        touched=tuple(touched_deduped),
        linked=tuple(linked_sorted),
    )
