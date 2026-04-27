"""Gate-2 review screen: undo state-reset contract and basic key behaviour."""

from __future__ import annotations

import queue
import threading
from unittest.mock import MagicMock

from auto_lorebook.review import (
    ApproveDecision,
    BundleView,
    EditDecision,
    RejectDecision,
    TargetView,
)
from auto_lorebook.tui.screens.review import ReviewScreen

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_view(n_targets: int = 2) -> BundleView:
    targets = tuple(
        TargetView(
            proposal=MagicMock(
                target_entity=f"Entity{i}",
                proposed_section="lore",
                proposed_status="authoritative",
            ),
            is_new_entity=False,
            new_entity_category=None,
            created_earlier_in_session=False,
            suggested_aliases=(),
            matched_via=None,
        )
        for i in range(n_targets)
    )
    return BundleView(
        bundle_index=1,
        bundle_total=1,
        claim_group_id="cg-001",
        targets=targets,
        source_url=None,
        source_title=None,
    )


def _make_screen() -> tuple[ReviewScreen, queue.Queue, threading.Event]:
    pending: queue.Queue = queue.Queue()
    cancel_event = threading.Event()
    screen = ReviewScreen(pending=pending, cancel_event=cancel_event)
    return screen, pending, cancel_event


# ---------------------------------------------------------------------------
# Unit-level tests (no Pilot needed for pure state logic)
# ---------------------------------------------------------------------------


class TestUndoStateReset:
    """[u]ndo resets bundle_edits, overrides, and selected to all-True."""

    def test_undo_resets_bundle_edits_to_none(self) -> None:
        screen, _pending, _ = _make_screen()
        view = _make_view(2)
        screen.show_bundle(view)
        # Simulate effect of [e]: set a bundle edit
        screen._bundle_edits = EditDecision(new_text="changed text")  # noqa: SLF001
        screen.action_undo()
        assert screen._bundle_edits is None  # noqa: SLF001

    def test_undo_resets_overrides(self) -> None:
        screen, _pending, _ = _make_screen()
        view = _make_view(2)
        screen.show_bundle(view)
        screen._overrides = {0: EditDecision(new_text="override")}  # noqa: SLF001
        screen.action_undo()
        assert screen._overrides == {}  # noqa: SLF001

    def test_undo_resets_selected_to_all_true(self) -> None:
        screen, _pending, _ = _make_screen()
        view = _make_view(3)
        screen.show_bundle(view)
        # Simulate [t] unchecking route 0
        screen._selected[0] = False  # noqa: SLF001
        screen.action_undo()
        assert screen._selected == [True, True, True]  # noqa: SLF001


class TestApproveAfterUndo:
    """After undo, approve emits an ApproveDecision with all routes selected."""

    def test_approve_after_undo_includes_route_0(self) -> None:
        screen, pending, _ = _make_screen()
        view = _make_view(2)
        screen.show_bundle(view)
        # Simulate edits and unchecking route 0
        screen._bundle_edits = EditDecision(new_text="edited")  # noqa: SLF001
        screen._overrides = {0: EditDecision(new_text="per-target")}  # noqa: SLF001
        screen._selected[0] = False  # noqa: SLF001
        # Undo
        screen.action_undo()
        # Approve
        screen.action_approve()
        decision = pending.get_nowait()
        assert isinstance(decision.decision, ApproveDecision)
        assert 0 in decision.selected_indices
        assert 1 in decision.selected_indices
        assert decision.per_target_overrides == {}

    def test_approve_without_undo_uses_edits(self) -> None:
        screen, pending, _ = _make_screen()
        view = _make_view(2)
        screen.show_bundle(view)
        edit = EditDecision(new_text="my edit")
        screen._bundle_edits = edit  # noqa: SLF001
        screen.action_approve()
        decision = pending.get_nowait()
        assert decision.decision is edit

    def test_approve_with_no_selected_routes_does_not_emit(self) -> None:
        screen, pending, _ = _make_screen()
        view = _make_view(2)
        screen.show_bundle(view)
        screen._selected = [False, False]  # noqa: SLF001
        screen.action_approve()
        assert pending.empty()


class TestRejectEmitsBundleDecision:
    def test_reject_emits_reject_decision(self) -> None:
        screen, pending, _ = _make_screen()
        view = _make_view(1)
        screen.show_bundle(view)
        screen.action_reject()
        decision = pending.get_nowait()
        assert isinstance(decision.decision, RejectDecision)
        assert decision.selected_indices == ()


class TestShowBundle:
    def test_show_bundle_initialises_selected_all_true(self) -> None:
        screen, _, _ = _make_screen()
        view = _make_view(3)
        screen.show_bundle(view)
        assert screen._selected == [True, True, True]  # noqa: SLF001

    def test_show_bundle_clears_prior_overrides(self) -> None:
        screen, _, _ = _make_screen()
        screen.show_bundle(_make_view(2))
        screen._overrides = {0: EditDecision(new_text="x")}  # noqa: SLF001
        screen.show_bundle(_make_view(2))  # new bundle
        assert screen._overrides == {}  # noqa: SLF001

    def test_show_bundle_clears_bundle_edits(self) -> None:
        screen, _, _ = _make_screen()
        screen.show_bundle(_make_view(2))
        screen._bundle_edits = EditDecision(new_text="prev")  # noqa: SLF001
        screen.show_bundle(_make_view(2))
        assert screen._bundle_edits is None  # noqa: SLF001
