"""TuiReviewer: review.Reviewer implementation driven by the TUI review screen.

Runs inside the worker thread that hosts review.run(...). Communicates with
the loop-side ReviewScreen via a single-slot queue.Queue and threading.Event.

Cancel protocol: Textual intercepts SIGINT on the main loop and does NOT
propagate it into worker threads as a Python exception. The reviewer checks
cancel_event after every queue.get() and manufactures KeyboardInterrupt itself
so that review.run's existing cancel path (review.py:599-601) can record
ReviewResult.remaining before re-raising.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import queue
    import threading
    from collections.abc import Callable

    from auto_lorebook.review import BundleDecision, BundleView


class TuiReviewer:
    """review.Reviewer that routes bundles through the TUI review screen."""

    by_label = "human-review"

    def __init__(
        self,
        *,
        app: object,
        cancel_event: threading.Event,
        pending: queue.Queue,
        show_bundle_fn: Callable[[BundleView], None],
        confirm_alias_fn: Callable[[str, str], None],
    ) -> None:
        self._app = app
        self._cancel = cancel_event
        self._pending: queue.Queue = pending
        self._show_bundle = show_bundle_fn
        self._confirm_alias = confirm_alias_fn

    def decide_bundle(self, view: BundleView) -> BundleDecision:
        """Push *view* to the loop; block until the screen returns a decision."""
        self._app.call_from_thread(self._show_bundle, view)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        result = self._pending.get()
        if self._cancel.is_set():
            raise KeyboardInterrupt
        return result  # type: ignore[return-value]

    def confirm_alias(self, entity: str, mention: str) -> bool:
        """Ask the loop side to confirm an alias; block for the answer."""
        self._app.call_from_thread(self._confirm_alias, entity, mention)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        result = self._pending.get()
        if self._cancel.is_set():
            raise KeyboardInterrupt
        return bool(result)
