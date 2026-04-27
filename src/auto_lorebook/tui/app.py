"""ProcessApp: top-level Textual application for the process pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import App
from textual.widgets import Footer, Label

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from auto_lorebook.config import Config
    from auto_lorebook.tui.state import PipelineState


class ProcessApp(App):
    """TUI orchestrator for the end-to-end source pipeline."""

    TITLE = "auto-lorebook process"
    CSS = ""

    def __init__(self, *, cfg: Config, state: PipelineState) -> None:
        super().__init__()
        self._cfg = cfg
        self._state = state

    def compose(self) -> ComposeResult:
        yield Label(
            f"[bold]auto-lorebook process[/bold]\n"
            f"Source: {self._state.source_id}\n"
            f"Stage: {self._state.stage.name}"
        )
        yield Footer()
