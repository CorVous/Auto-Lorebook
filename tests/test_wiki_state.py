"""Tests for wiki_state pure path arithmetic."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_lorebook import wiki_state


def test_wiki_state_dir_shape() -> None:
    p = wiki_state.wiki_state_dir(Path("/w"))
    assert p == Path("/w/.wiki-state")


def test_pending_dir_shape() -> None:
    p = wiki_state.pending_dir(Path("/w"))
    assert p == Path("/w/.wiki-state/pending")


def test_pending_reading_dir_shape() -> None:
    p = wiki_state.pending_reading_dir(Path("/w"), "src-001")
    assert p == Path("/w/.wiki-state/pending/src-001/reading")


def test_pending_plan_path_shape() -> None:
    p = wiki_state.pending_plan_path(Path("/w"), "src-001")
    assert p == Path("/w/.wiki-state/pending/src-001/plan.yaml")
    assert p.name == "plan.yaml"


def test_pending_proposals_dir_shape() -> None:
    p = wiki_state.pending_proposals_dir(Path("/w"), "src-001")
    assert p == Path("/w/.wiki-state/pending/src-001/proposals")


def test_pending_proposal_path_shape() -> None:
    p = wiki_state.pending_proposal_path(Path("/w"), "src-001", "prop-42")
    assert p == Path("/w/.wiki-state/pending/src-001/proposals/prop-42.yaml")
    assert p.name == "prop-42.yaml"


def test_last_context_path_shape() -> None:
    p = wiki_state.last_context_path(Path("/w"))
    assert p == Path("/w/.wiki-state/last-context.yaml")


def test_gitignore_path_shape() -> None:
    p = wiki_state.gitignore_path(Path("/w"))
    assert p == Path("/w/.wiki-state/.gitignore")


def test_purity_no_filesystem_io() -> None:
    """All functions must be pure — no I/O on nonexistent paths."""
    root = Path("/nonexistent/wiki/that/does/not/exist")
    wiki_state.wiki_state_dir(root)
    wiki_state.pending_dir(root)
    wiki_state.pending_reading_dir(root, "sid")
    wiki_state.pending_plan_path(root, "sid")
    wiki_state.pending_proposals_dir(root, "sid")
    wiki_state.pending_proposal_path(root, "sid", "pid")
    wiki_state.last_context_path(root)
    wiki_state.gitignore_path(root)
    # nothing created
    assert not root.exists()


@pytest.mark.parametrize(
    ("source_id", "proposal_id"),
    [
        ("yt-abc123", "char-aldara"),
        ("qa-deadbeef", "loc-ember-vale"),
    ],
)
def test_pending_proposal_path_various_ids(source_id: str, proposal_id: str) -> None:
    p = wiki_state.pending_proposal_path(Path("/w"), source_id, proposal_id)
    assert p.suffix == ".yaml"
    assert p.parent.name == "proposals"
    assert p.parent.parent.name == source_id
