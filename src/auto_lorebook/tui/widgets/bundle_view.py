"""BundleView widget: renders a review bundle (proposals + route checklist)."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from textual.widgets import Checkbox, Label, Static

if TYPE_CHECKING:
    from textual.app import ComposeResult

    from auto_lorebook.review import BundleView


class BundleViewWidget(Static):
    """Renders a BundleView with per-route checkboxes."""

    DEFAULT_CSS = """
    BundleViewWidget {
        overflow-y: auto;
        height: 1fr;
        border: solid $panel;
        padding: 0 1;
    }
    BundleViewWidget Label {
        margin: 0 0 1 0;
    }
    """

    def __init__(
        self, view: BundleView, selected: list[bool], **kwargs: object
    ) -> None:
        super().__init__(**kwargs)  # ty: ignore[invalid-argument-type]
        self._view = view
        self._selected = selected

    def compose(self) -> ComposeResult:
        v = self._view
        header = (
            f"[bold]Bundle {v.bundle_index}/{v.bundle_total}[/bold]  "
            f"claim: {v.claim_group_id}"
        )
        if v.source_title:
            header += f"\nSource: {v.source_title}"
        if v.source_url:
            header += f"  ({v.source_url})"
        yield Label(header)

        for i, target in enumerate(v.targets):
            p = target.proposal
            entity_tag = "[new]" if target.is_new_entity else "[existing]"
            label = f"{entity_tag} {p.target_entity}"
            if target.matched_via:
                label += f"  via: {target.matched_via}"
            yield Checkbox(label, value=self._selected[i], id=f"route-{i}")
            yield Label(
                f"  Section: {p.section or '—'}  "
                f"Status: {p.status or '—'}",
            )

    def get_selected(self) -> list[bool]:
        """Return current checkbox states."""
        result = list(self._selected)
        for i in range(len(self._view.targets)):
            with contextlib.suppress(Exception):
                cb = self.query_one(f"#route-{i}", Checkbox)
                result[i] = cb.value
        return result
