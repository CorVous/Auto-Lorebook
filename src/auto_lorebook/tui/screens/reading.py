"""Gate-1 reading screen: reading.md viewer + a/e/r/u/q controls."""

from __future__ import annotations

import contextlib
import os
import subprocess  # noqa: S404
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label

from auto_lorebook import reading_pipeline
from auto_lorebook.tui.widgets.diff_view import DiffView

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from auto_lorebook.config import Config


class ReadingScreen(Screen):
    """Viewer for the pending draft reading with gate-1 controls.

    Mirrors commands/approve_reading.py::_interactive_session.
    """

    BINDINGS: ClassVar[list] = [
        Binding("a", "approve", "Approve"),
        Binding("e", "edit", "Edit"),
        Binding("r", "reject", "Reject"),
        Binding("u", "undo", "Undo"),
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
            return self._pending_path.read_text(encoding="utf-8")
        return "_No draft reading found._"

    def _header_line(self) -> str:
        dirty = ""
        if (
            self._pending_path.exists()
            and self._pending_path.read_bytes() != self._original_bytes
        ):
            dirty = " [edited]"
        return f"[bold]{self._pending_path}{dirty}[/bold]"

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
        editor = os.environ.get("EDITOR", "vi")
        with self.app.suspend():
            subprocess.run([editor, str(self._pending_path)], check=False)  # noqa: S603
        self._refresh_view()

    def action_quit_screen(self) -> None:
        self.dismiss(("quit", self._pending_action))
