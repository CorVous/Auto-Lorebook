"""Welcome screen: URL/path entry + config sanity check."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, ClassVar

from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label

if TYPE_CHECKING:
    from textual.app import ComposeResult


class WelcomeScreen(Screen):
    """Entry point for `process` with no positional argument."""

    BINDINGS: ClassVar[list] = [("q", "quit", "Quit")]

    def __init__(
        self,
        *,
        source_id: str | None = None,
        url_or_path: str | None = None,
        message: str | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # ty: ignore[invalid-argument-type]
        self._source_id = source_id
        self._url_or_path = url_or_path
        self._message = message

    def compose(self) -> ComposeResult:
        yield Header()
        if self._message:
            yield Label(f"[bold yellow]{self._message}[/bold yellow]", id="msg")
        if self._source_id:
            yield Label(
                f"[bold]Resuming source:[/bold] {self._source_id}",
                id="source-label",
            )
        else:
            yield Label("Enter YouTube URL or local file path:", id="prompt-label")
            yield Input(
                value=self._url_or_path or "",
                placeholder=("https://youtube.com/watch?v=... or /path/to/file.srt"),
                id="url-input",
            )
        yield Button("Start", variant="primary", id="start-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start-btn":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:  # noqa: ARG002
        self._submit()

    def _submit(self) -> None:
        if self._source_id:
            self.dismiss(self._source_id)
            return
        with contextlib.suppress(Exception):
            inp = self.query_one("#url-input", Input)
            value = inp.value.strip()
            if value:
                self.dismiss(value)

    def action_quit(self) -> None:
        self.app.exit()
