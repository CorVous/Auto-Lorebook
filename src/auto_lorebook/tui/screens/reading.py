"""Gate-1 reading screen: reading.md viewer + a/e/r/u/q/n/p controls."""

from __future__ import annotations

import contextlib
import os
import re
import subprocess  # noqa: S404
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label

from auto_lorebook import reading_pipeline
from auto_lorebook.tui.widgets.diff_view import DiffView

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from auto_lorebook.config import Config


def _split_segments(text: str) -> list[str]:
    """Split reading.md into per-segment blocks (each starting with ##)."""
    parts = re.split(r"(?m)^(?=## )", text)
    return [p.strip("\n") for p in parts[1:] if p.strip()]


def _reconstruct(preamble: str, segments: list[str]) -> str:
    """Reassemble reading.md from preamble and segment blocks."""
    if not segments:
        return preamble
    return preamble.rstrip("\n") + "\n\n" + "\n\n".join(segments) + "\n"


class ReadingScreen(Screen):
    """Viewer for the pending draft reading with gate-1 controls.

    Mirrors commands/approve_reading.py::_interactive_session.
    Segments are navigated one at a time with [n]/[p].
    """

    BINDINGS: ClassVar[list] = [
        Binding("a", "approve", "Approve"),
        Binding("e", "edit", "Edit"),
        Binding("r", "reject", "Reject"),
        Binding("u", "undo", "Undo"),
        Binding("n", "next_seg", "Next"),
        Binding("p", "prev_seg", "Prev"),
        Binding("q", "quit_screen", "Quit"),
    ]

    def __init__(self, *, cfg: Config, source_id: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # ty: ignore[invalid-argument-type]
        self._cfg = cfg
        self._source_id = source_id
        self._pending_path = reading_pipeline.pending_reading_path(source_id)
        self._original_bytes = (
            self._pending_path.read_bytes() if self._pending_path.exists() else b""
        )
        self._pending_action = "none"
        self._preamble: str = ""
        self._segments: list[str] = []
        self._seg_idx: int = 0

    def compose(self) -> ComposeResult:
        yield Header()
        text = self._load_text()
        yield Label(self._header_line(), id="header")
        yield DiffView(text, id="reading-view")
        yield Label(
            f"[dim]Pending action: {self._pending_action}[/dim]", id="action-label"
        )
        yield Footer()

    def _load_text(self) -> str:
        if self._pending_path.exists():
            full = self._pending_path.read_text(encoding="utf-8")
            parts = re.split(r"(?m)^(?=## )", full)
            segs = [p.strip("\n") for p in parts[1:] if p.strip()]
            if segs:
                self._preamble = parts[0]
                self._segments = segs
                self._seg_idx = min(self._seg_idx, len(segs) - 1)
                return segs[self._seg_idx]
            return full
        return "_No draft reading found._"

    def _header_line(self) -> str:
        dirty = ""
        if (
            self._pending_path.exists()
            and self._pending_path.read_bytes() != self._original_bytes
        ):
            dirty = " [edited]"
        base = f"[bold]{self._pending_path}{dirty}[/bold]"
        if self._segments:
            return f"{base}  [dim]{self._seg_idx + 1}/{len(self._segments)}[/dim]"
        return base

    def _refresh_view(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#reading-view", DiffView).update_text(self._load_text())
            self.query_one("#header", Label).update(self._header_line())
            self.query_one("#action-label", Label).update(
                f"[dim]Pending action: {self._pending_action}[/dim]"
            )

    def action_approve(self) -> None:
        self.dismiss(("approve", self._source_id))

    def action_reject(self) -> None:
        self._pending_action = "reject"
        self._refresh_view()

    def action_undo(self) -> None:
        if self._pending_path.exists():
            self._pending_path.write_bytes(self._original_bytes)
        self._pending_action = "none"
        self._refresh_view()

    def action_edit(self) -> None:
        if not self._segments:
            return
        seg_text = self._segments[self._seg_idx]
        editor = os.environ.get("EDITOR", "vi")
        tmp: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            ) as f:
                f.write(seg_text)
                tmp = Path(f.name)
            with self.app.suspend():
                subprocess.run([editor, str(tmp)], check=False)  # noqa: S603
            edited = tmp.read_text(encoding="utf-8").strip("\n")
            if edited != seg_text:
                self._segments[self._seg_idx] = edited
                self._pending_path.write_text(
                    _reconstruct(self._preamble, self._segments), encoding="utf-8"
                )
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        self._refresh_view()

    def action_next_seg(self) -> None:
        if self._seg_idx < len(self._segments) - 1:
            self._seg_idx += 1
            self._refresh_view()

    def action_prev_seg(self) -> None:
        if self._seg_idx > 0:
            self._seg_idx -= 1
            self._refresh_view()

    def action_quit_screen(self) -> None:
        self.dismiss(("quit", self._pending_action))
