"""Idempotent wiki skeleton creation under a wiki root."""

from __future__ import annotations

from typing import TYPE_CHECKING

from auto_lorebook import db, wiki_state
from auto_lorebook._io import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path

WIKI_SUBDIRS: tuple[str, ...] = (
    "characters",
    "locations",
    "factions",
    "events",
    "items",
    "concepts",
)
DOTTED_YAML_STUBS: tuple[str, ...] = (
    ".wiki-context.yaml",
    ".transcription-corrections.yaml",
)
STUB_BODY = "schema_version: 1\n"


def bootstrap(wiki_root: Path) -> None:
    """Create wiki skeleton if not present; safe to call repeatedly.

    Creates: entity dirs, dotted-yaml stubs (only if absent),
    .wiki-state/ dir, and .wiki-state/.gitignore (only if absent).
    """
    wiki_root.mkdir(parents=True, exist_ok=True)
    for sub in WIKI_SUBDIRS:
        (wiki_root / sub).mkdir(exist_ok=True)
    for fname in DOTTED_YAML_STUBS:
        path = wiki_root / fname
        if not path.exists():
            atomic_write_text(path, STUB_BODY)
    wiki_state.wiki_state_dir(wiki_root).mkdir(exist_ok=True)
    gi = wiki_state.gitignore_path(wiki_root)
    if not gi.exists():
        atomic_write_text(gi, wiki_state.GITIGNORE_BODY)
    else:
        existing = gi.read_text(encoding="utf-8")
        if "wiki.db" not in existing:
            gi.write_text(
                existing + "wiki.db\nwiki.db-wal\nwiki.db-shm\n",
                encoding="utf-8",
            )
    conn = db.open(wiki_state.wiki_db_path(wiki_root))
    conn.close()
