"""Context screen: per-field form backed by gather_context."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, ClassVar

from textual.screen import Screen
from textual.validation import Regex, ValidationResult, Validator
from textual.widgets import Button, Footer, Header, Input, Label

from auto_lorebook import config as cfg_mod
from auto_lorebook import info_yaml as info_yaml_mod
from auto_lorebook import interactive as interactive_mod
from auto_lorebook import wiki_context as wiki_context_mod

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from auto_lorebook.config import Config
    from auto_lorebook.info_yaml import Info


class _SourceNatureValidator(Validator):
    """Accept only values in SOURCE_NATURES."""

    def validate(self, value: str) -> ValidationResult:
        if not value or value in interactive_mod.SOURCE_NATURES:
            return self.success()
        allowed = ", ".join(interactive_mod.SOURCE_NATURES)
        return self.failure(f"Must be one of: {allowed}")


class ContextScreen(Screen):
    """Form with one Input per context field; pre-fills from info.yaml."""

    BINDINGS: ClassVar[list] = [
        ("ctrl+s", "submit", "Save"),
        ("escape", "quit", "Quit"),
    ]

    def __init__(
        self,
        *,
        cfg: Config,
        source_id: str,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # ty: ignore[invalid-argument-type]
        self._cfg = cfg
        self._source_id = source_id

    def _load_info(self) -> Info:
        info_path = self._cfg.wiki_repo_path / "sources" / self._source_id / "info.yaml"
        if info_path.exists():
            with contextlib.suppress(info_yaml_mod.InfoError):
                return info_yaml_mod.read(info_path)
        return info_yaml_mod.Info(
            source_id=self._source_id,
            source_type="unknown",
            fetched_at="",
            context=info_yaml_mod.SourceContext(),
        )

    def compose(self) -> ComposeResult:
        info = self._load_info()
        ctx = info.context
        wc = wiki_context_mod.read(self._cfg.wiki_repo_path / ".wiki-context.yaml")

        yield Header()
        yield Label(
            f"[bold]Context[/bold] — {self._source_id}\n"
            "Fill in what you know. Press [b]Ctrl+S[/b] to save or [b]Esc[/b] to quit.",
        )

        yield Label("Session date (YYYY-MM-DD):")
        yield Input(
            value=info.session_date or "",
            placeholder="2026-04-27",
            id="session-date",
            validators=[
                Regex(
                    r"(\d{4}-\d{2}-\d{2}|^$)",
                    failure_description="Use YYYY-MM-DD",
                )
            ],
        )

        yield Label("Perspective (e.g. 'Cor playing Kiki'):")
        yield Input(
            value=ctx.perspective or "",
            placeholder="Cor playing Kiki",
            id="perspective",
        )

        natures = "/".join(interactive_mod.SOURCE_NATURES)
        yield Label(f"Source nature ({natures}):")
        yield Input(
            value=ctx.source_nature or "",
            placeholder="actual-play",
            id="source-nature",
            validators=[_SourceNatureValidator()],
        )

        yield Label("Setting:")
        yield Input(
            value=ctx.setting or wc.setting.name or "",
            placeholder=wc.setting.name or "Realms of...",
            id="setting",
        )

        yield Label("Notes (one line):")
        yield Input(
            value=ctx.notes or "",
            placeholder="Optional notes",
            id="notes",
        )

        yield Button("Save", variant="primary", id="save-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self.action_submit()

    def action_submit(self) -> None:
        """Collect form values, call gather_context, dismiss with updated Info."""
        info = self._load_info()
        wc = wiki_context_mod.read(self._cfg.wiki_repo_path / ".wiki-context.yaml")
        last = cfg_mod.load_last_context()

        def _val(field_id: str) -> str | None:
            with contextlib.suppress(Exception):
                inp = self.query_one(f"#{field_id}", Input)
                return inp.value.strip() or None
            return None

        flags = {
            "session_date": _val("session-date"),
            "perspective": _val("perspective"),
            "source_nature": _val("source-nature"),
            "setting": _val("setting"),
            "notes": _val("notes"),
        }
        updated = interactive_mod.gather_context(
            info, flags, wc, last, interactive=False
        )
        self.dismiss(updated)

    def action_quit(self) -> None:
        self.dismiss(None)
