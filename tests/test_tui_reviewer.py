"""Tests for TuiReviewer: scripted message-bus approach (no full TUI needed)."""

from __future__ import annotations

import queue
import threading
import time
from unittest.mock import MagicMock

import pytest

from auto_lorebook.review import ApproveDecision, BundleDecision, BundleView, TargetView
from auto_lorebook.tui.reviewer import TuiReviewer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_view() -> BundleView:
    """Minimal BundleView for testing."""
    target = TargetView(
        proposal=MagicMock(),
        is_new_entity=False,
        new_entity_category=None,
        created_earlier_in_session=False,
        suggested_aliases=(),
        matched_via=None,
    )
    return BundleView(
        bundle_index=1,
        bundle_total=1,
        claim_group_id="cg-001",
        targets=(target,),
        source_url=None,
        source_title=None,
    )


def _make_approve_decision() -> BundleDecision:
    return BundleDecision(
        decision=ApproveDecision(),
        selected_indices=(0,),
    )


def _make_reviewer(
    *,
    scripted_result: object,
    cancel_after: bool = False,
) -> tuple[TuiReviewer, threading.Event]:
    """Build a TuiReviewer with a mock app and scripted show_bundle.

    The mock app's call_from_thread immediately invokes the callback, which puts
    scripted_result into the pending queue before decide_bundle unblocks.
    """
    cancel_event = threading.Event()
    pending: queue.Queue = queue.Queue()

    def show_bundle(_view: BundleView) -> None:
        if cancel_after:
            cancel_event.set()
        pending.put(scripted_result)

    def confirm_alias(_entity: str, _mention: str) -> None:
        if cancel_after:
            cancel_event.set()
        pending.put(scripted_result)

    app = MagicMock()
    app.call_from_thread.side_effect = lambda fn, *args: fn(*args)

    reviewer = TuiReviewer(
        app=app,
        cancel_event=cancel_event,
        pending=pending,
        show_bundle_fn=show_bundle,
        confirm_alias_fn=confirm_alias,
    )
    return reviewer, cancel_event


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTuiReviewerDecideBundle:
    def test_returns_scripted_bundle_decision(self) -> None:
        expected = _make_approve_decision()
        reviewer, _ = _make_reviewer(scripted_result=expected)
        view = _make_view()
        result = reviewer.decide_bundle(view)
        assert result is expected

    def test_show_bundle_fn_called_with_view(self) -> None:
        calls: list = []
        cancel_event = threading.Event()
        pending: queue.Queue = queue.Queue()
        decision = _make_approve_decision()

        def show_bundle(view: BundleView) -> None:
            calls.append(view)
            pending.put(decision)

        app = MagicMock()
        app.call_from_thread.side_effect = lambda fn, *args: fn(*args)
        reviewer = TuiReviewer(
            app=app,
            cancel_event=cancel_event,
            pending=pending,
            show_bundle_fn=show_bundle,
            confirm_alias_fn=MagicMock(),
        )

        view = _make_view()
        reviewer.decide_bundle(view)
        assert len(calls) == 1
        assert calls[0] is view

    def test_cancel_flag_raises_keyboard_interrupt(self) -> None:
        # cancel_after=True: show_bundle sets cancel_event, then puts result
        expected = _make_approve_decision()
        reviewer, _ = _make_reviewer(scripted_result=expected, cancel_after=True)
        view = _make_view()
        with pytest.raises(KeyboardInterrupt):
            reviewer.decide_bundle(view)

    def test_no_cancel_does_not_raise(self) -> None:
        expected = _make_approve_decision()
        reviewer, _ = _make_reviewer(scripted_result=expected, cancel_after=False)
        view = _make_view()
        result = reviewer.decide_bundle(view)
        assert isinstance(result, BundleDecision)


class TestTuiReviewerConfirmAlias:
    def test_returns_true(self) -> None:
        reviewer, _ = _make_reviewer(scripted_result=True)
        assert reviewer.confirm_alias("Aldara", "the city") is True

    def test_returns_false(self) -> None:
        reviewer, _ = _make_reviewer(scripted_result=False)
        assert reviewer.confirm_alias("Aldara", "the city") is False

    def test_cancel_raises_keyboard_interrupt(self) -> None:
        reviewer, _ = _make_reviewer(scripted_result=True, cancel_after=True)
        with pytest.raises(KeyboardInterrupt):
            reviewer.confirm_alias("Aldara", "the city")


class TestTuiReviewerByLabel:
    def test_by_label_is_human_review(self) -> None:
        reviewer, _ = _make_reviewer(scripted_result=_make_approve_decision())
        assert reviewer.by_label == "human-review"


class TestTuiReviewerThreadedDecide:
    """decide_bundle blocks in a worker thread until the queue is filled."""

    def test_worker_thread_returns_correctly(self) -> None:
        expected = _make_approve_decision()
        result_box: list = []
        error_box: list = []
        cancel_event = threading.Event()
        pending: queue.Queue = queue.Queue()

        def show_bundle(view: BundleView) -> None:
            # Do nothing here; main thread fills queue below
            pass

        app = MagicMock()
        app.call_from_thread.side_effect = lambda fn, *args: fn(*args)

        reviewer = TuiReviewer(
            app=app,
            cancel_event=cancel_event,
            pending=pending,
            show_bundle_fn=show_bundle,
            confirm_alias_fn=MagicMock(),
        )

        def worker() -> None:
            try:
                result_box.append(reviewer.decide_bundle(_make_view()))
            except Exception as exc:  # noqa: BLE001
                error_box.append(exc)

        t = threading.Thread(target=worker)
        t.start()
        # Give the worker a moment to reach pending.get()
        time.sleep(0.05)
        pending.put(expected)
        t.join(timeout=2)
        assert not t.is_alive(), "worker thread hung"
        assert not error_box, f"worker raised: {error_box}"
        assert result_box == [expected]
