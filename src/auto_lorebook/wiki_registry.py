"""Pure data layer for the wiki registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class WikiRegistryError(ValueError):
    """Raised on invalid registry mutation."""


@dataclass
class WikiEntry:
    """Single wiki entry: nickname + filesystem path."""

    nickname: str
    path: Path


@dataclass
class WikiRegistry:
    """Ordered list of wiki entries + active pointer. No filesystem IO."""

    entries: list[WikiEntry] = field(default_factory=list)
    active: str | None = None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_list(self) -> list[dict[str, str]]:
        """Serialize entries to list of {nickname, path} dicts."""
        return [{"nickname": e.nickname, "path": str(e.path)} for e in self.entries]

    @classmethod
    def from_list(
        cls,
        data: list[dict[str, Any]],
        *,
        active: str | None,
    ) -> WikiRegistry:
        """Deserialize from list of {nickname, path} dicts."""
        entries = [WikiEntry(d["nickname"], Path(d["path"])) for d in data]
        return cls(entries=entries, active=active)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def _known_nicknames(self) -> set[str]:
        return {e.nickname for e in self.entries}

    def add(self, entry: WikiEntry) -> None:
        """Append entry; raises if nickname already exists."""
        if entry.nickname in self._known_nicknames():
            msg = f"nickname already registered: {entry.nickname!r}"
            raise WikiRegistryError(msg)
        self.entries.append(entry)

    def remove(self, nickname: str) -> None:
        """Drop entry by nickname; refuses if it is the active entry."""
        if nickname == self.active:
            msg = f"cannot remove active wiki: {nickname!r}"
            raise WikiRegistryError(msg)
        idx = next(
            (i for i, e in enumerate(self.entries) if e.nickname == nickname), None
        )
        if idx is None:
            msg = f"unknown nickname: {nickname!r}"
            raise WikiRegistryError(msg)
        del self.entries[idx]

    def rename(self, old: str, new: str) -> None:
        """Rename entry; updates active pointer if matched."""
        if old not in self._known_nicknames():
            msg = f"unknown nickname: {old!r}"
            raise WikiRegistryError(msg)
        if new != old and new in self._known_nicknames():
            msg = f"nickname already registered: {new!r}"
            raise WikiRegistryError(msg)
        for e in self.entries:
            if e.nickname == old:
                e.nickname = new
                break
        if self.active == old:
            self.active = new

    def set_active(self, nickname: str) -> None:
        """Set active; raises if nickname unknown."""
        if nickname not in self._known_nicknames():
            msg = f"unknown nickname: {nickname!r}"
            raise WikiRegistryError(msg)
        self.active = nickname
