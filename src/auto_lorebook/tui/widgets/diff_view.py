"""Syntax-highlighted markdown viewer widget."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from textual.widgets import Markdown, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult


class DiffView(Static):
    """Scrollable markdown viewer."""

    DEFAULT_CSS = """
    DiffView {
        overflow-y: auto;
        height: 1fr;
        border: solid $panel;
        padding: 0 1;
    }
    """

    def __init__(self, text: str = "", **kwargs: object) -> None:
        super().__init__(**kwargs)  # ty: ignore[invalid-argument-type]
        self._text = text

    def compose(self) -> ComposeResult:
        yield Markdown(self._text)

    def update_text(self, text: str) -> None:
        """Replace the displayed markdown content."""
        self._text = text
        with contextlib.suppress(Exception):
            md = self.query_one(Markdown)
            md.update(text)
