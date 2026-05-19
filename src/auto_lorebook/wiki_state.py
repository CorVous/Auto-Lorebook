"""Pure path arithmetic for per-wiki tool state under <wiki>/.wiki-state/."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

WIKI_STATE_DIRNAME = ".wiki-state"
PENDING_DIRNAME = "pending"
READING_DIRNAME = "reading"
PROPOSALS_DIRNAME = "proposals"
PLAN_FILENAME = "plan.yaml"
LAST_CONTEXT_FILENAME = "last-context.yaml"
GITIGNORE_FILENAME = ".gitignore"
WIKI_DB_FILENAME = "wiki.db"
GITIGNORE_BODY = "pending/\nwiki.db\nwiki.db-wal\nwiki.db-shm\n"


def wiki_state_dir(wiki_root: Path) -> Path:
    """Return <wiki>/.wiki-state/."""
    return wiki_root / WIKI_STATE_DIRNAME


def pending_dir(wiki_root: Path) -> Path:
    """Return <wiki>/.wiki-state/pending/."""
    return wiki_state_dir(wiki_root) / PENDING_DIRNAME


def pending_source_dir(wiki_root: Path, source_id: str) -> Path:
    """Return <wiki>/.wiki-state/pending/<source_id>/."""
    return pending_dir(wiki_root) / source_id


def pending_reading_dir(wiki_root: Path, source_id: str) -> Path:
    """Return <wiki>/.wiki-state/pending/<source_id>/reading/."""
    return pending_source_dir(wiki_root, source_id) / READING_DIRNAME


def pending_plan_path(wiki_root: Path, source_id: str) -> Path:
    """Return <wiki>/.wiki-state/pending/<source_id>/plan.yaml."""
    return pending_source_dir(wiki_root, source_id) / PLAN_FILENAME


def pending_proposals_dir(wiki_root: Path, source_id: str) -> Path:
    """Return <wiki>/.wiki-state/pending/<source_id>/proposals/."""
    return pending_source_dir(wiki_root, source_id) / PROPOSALS_DIRNAME


def pending_proposal_path(wiki_root: Path, source_id: str, proposal_id: str) -> Path:
    """Return <wiki>/.wiki-state/pending/<source_id>/proposals/<proposal_id>.yaml."""
    return pending_proposals_dir(wiki_root, source_id) / f"{proposal_id}.yaml"


def last_context_path(wiki_root: Path) -> Path:
    """Return <wiki>/.wiki-state/last-context.yaml."""
    return wiki_state_dir(wiki_root) / LAST_CONTEXT_FILENAME


def gitignore_path(wiki_root: Path) -> Path:
    """Return <wiki>/.wiki-state/.gitignore."""
    return wiki_state_dir(wiki_root) / GITIGNORE_FILENAME


def wiki_db_path(wiki_root: Path) -> Path:
    """Return <wiki>/.wiki-state/wiki.db."""
    return wiki_state_dir(wiki_root) / WIKI_DB_FILENAME
