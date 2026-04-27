"""Progress screen: log/spinner panel for non-interactive pipeline stages."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from textual.screen import Screen
from textual.widgets import Footer, Header, Label, LoadingIndicator, RichLog

if TYPE_CHECKING:
    from textual.app import ComposeResult


class ProgressScreen(Screen):
    """Displays a spinner and scrollable log while a background worker runs."""

    def __init__(self, *, title: str = "Running…", **kwargs: object) -> None:
        super().__init__(**kwargs)  # ty: ignore[invalid-argument-type]
        self._title = title

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label(f"[bold]{self._title}[/bold]", id="progress-title")
        yield LoadingIndicator()
        yield RichLog(id="log", markup=True, highlight=True)
        yield Footer()

    def append_log(self, line: str) -> None:
        """Append *line* to the log (safe from worker thread via call_from_thread)."""
        with contextlib.suppress(Exception):
            log = self.query_one("#log", RichLog)
            log.write(line)

    def set_title(self, title: str) -> None:
        """Update the progress title label."""
        with contextlib.suppress(Exception):
            lbl = self.query_one("#progress-title", Label)
            lbl.update(f"[bold]{title}[/bold]")
