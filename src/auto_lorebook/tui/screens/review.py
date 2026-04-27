"""Gate-2 review screen: bundle viewer + a/e/r/p/t/u controls.

Mirrors commands/review.py key contract (lines 148-184). State is pure
local to each bundle: selected, overrides, bundle_edits are reset on
every show_bundle call.

[u]ndo scope: the on-screen bundle only — once a BundleDecision is emitted
it cannot be recalled.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Label

from auto_lorebook.review import (
    ApproveDecision,
    BundleDecision,
    EditDecision,
    RejectDecision,
)
from auto_lorebook.tui.widgets.bundle_view import BundleViewWidget

if TYPE_CHECKING:
    import queue
    import threading

    from textual.app import ComposeResult

    from auto_lorebook.review import BundleView


class ReviewScreen(Screen):
    """Gate-2 bundle review; one BundleView at a time.

    Worker thread pushes bundles via show_bundle (scheduled via
    call_from_thread). Screen emits BundleDecision into pending queue.
    """

    BINDINGS: ClassVar[list] = [
        Binding("a", "approve", "Approve"),
        Binding("r", "reject", "Reject"),
        Binding("e", "edit", "Edit"),
        Binding("t", "targets", "Targets"),
        Binding("u", "undo", "Undo"),
        Binding("q", "quit_review", "Quit"),
        Binding("ctrl+c", "quit_review", "Quit", show=False),
    ]

    def __init__(
        self,
        *,
        pending: queue.Queue,
        cancel_event: threading.Event,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # ty: ignore[invalid-argument-type]
        self._pending = pending
        self._cancel = cancel_event
        self._view: BundleView | None = None
        # per-bundle local state — reset in show_bundle
        self._selected: list[bool] = []
        self._overrides: dict[int, EditDecision] = {}
        self._bundle_edits: EditDecision | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("[dim]Waiting for first bundle…[/dim]", id="bundle-area")
        yield Footer()

    def show_bundle(self, view: BundleView) -> None:
        """Accept a new bundle from the worker thread and update display."""
        self._view = view
        self._selected = [True] * len(view.targets)
        self._overrides = {}
        self._bundle_edits = None
        self._refresh_bundle()

    def _refresh_bundle(self) -> None:
        if self._view is None:
            return
        with contextlib.suppress(Exception):
            area = self.query_one("#bundle-area")
            area.remove_children()
            widget = BundleViewWidget(self._view, self._selected, id="bundle-widget")
            area.mount(widget)

    def _emit(self, decision: BundleDecision) -> None:
        self._pending.put(decision)

    def action_approve(self) -> None:
        if self._view is None:
            return
        selected = tuple(i for i, on in enumerate(self._selected) if on)
        if not selected:
            with contextlib.suppress(Exception):
                self.notify(
                    "No routes selected — select at least one to approve.",
                    severity="warning",
                )
            return
        overrides = {i: ov for i, ov in self._overrides.items() if i in selected}
        decision = self._bundle_edits or ApproveDecision()
        self._emit(
            BundleDecision(
                decision=decision,
                selected_indices=selected,
                per_target_overrides=overrides,
            )
        )

    def action_reject(self) -> None:
        self._emit(BundleDecision(decision=RejectDecision(), selected_indices=()))

    def action_edit(self) -> None:
        """Show bundle-level edit notice (full modal not yet implemented)."""
        with contextlib.suppress(Exception):
            self.notify(
                "[e] edit: use the CLI `review` command for complex edits "
                "(TUI edit coming soon)."
            )

    def action_targets(self) -> None:
        """Sync selected state from checkboxes, then show count."""
        if self._view is None:
            return
        with contextlib.suppress(Exception):
            widget = self.query_one("#bundle-widget", BundleViewWidget)
            self._selected = widget.get_selected()
        with contextlib.suppress(Exception):
            self.notify(f"Selected routes: {sum(self._selected)}/{len(self._selected)}")

    def action_undo(self) -> None:
        """Reset all per-bundle edits — scope is on-screen bundle only."""
        self._bundle_edits = None
        self._overrides = {}
        if self._view:
            self._selected = [True] * len(self._view.targets)
        with contextlib.suppress(Exception):
            self._refresh_bundle()
            self.notify("Undo: all edits cleared for this bundle.")

    def action_quit_review(self) -> None:
        self._cancel.set()
        self.app.exit()
