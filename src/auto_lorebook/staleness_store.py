"""DB-backed staleness store for wiki page rebuild skip logic.

Public API:
    compute_page_inputs_hash(*, entity, aliases, facts, linked_facts,
                              entity_index, wiki_setting, model, model_params) -> str
    get_page_hash(conn, category, slug) -> str | None
    record_page_hash(conn, category, slug, inputs_sha256, *, generated_at) -> None
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    import sqlite3

    from auto_lorebook.entities import AliasRow, EntityRow
    from auto_lorebook.facts import FactRow


def _fact_tuple(fact: FactRow) -> list[object]:
    """Fixed-order list of prose-affecting fields from a FactRow."""
    return [
        fact.id,
        fact.text,
        fact.status,
        fact.status_reason,
        fact.speaker,
        fact.source_id,
        fact.locator,
        fact.session_date,
    ]


def compute_page_inputs_hash(
    *,
    entity: EntityRow,  # noqa: ARG001
    aliases: list[AliasRow],  # noqa: ARG001
    facts: list[FactRow],
    linked_facts: list[tuple[EntityRow, list[FactRow]]],
    entity_index: str,
    wiki_setting: str,
    model: str,
    model_params: dict[str, object],
) -> str:
    """SHA-256 of canonical inputs that affect generated prose.

    Deterministic; order of facts/linked_facts does not matter.
    ``entity`` and ``aliases`` reserved for future use; not hashed today.
    """
    obj = {
        "own_facts": [_fact_tuple(f) for f in sorted(facts, key=lambda f: f.id)],
        "linked_facts": [
            {
                "entity": f"{le.category}/{le.slug}",
                "facts": [_fact_tuple(f) for f in sorted(lf, key=lambda f: f.id)],
            }
            for le, lf in sorted(
                linked_facts, key=lambda p: f"{p[0].category}/{p[0].slug}"
            )
        ],
        "entity_index": entity_index,
        "wiki_setting": wiki_setting,
        "model": model,
        "model_params": model_params,
    }
    serialized = json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def get_page_hash(conn: sqlite3.Connection, category: str, slug: str) -> str | None:
    """Return stored inputs_sha256 for (category, slug), or None if absent."""
    row = conn.execute(
        "SELECT inputs_sha256 FROM entity_page_staleness WHERE category=? AND slug=?",
        (category, slug),
    ).fetchone()
    return row[0] if row else None


def record_page_hash(
    conn: sqlite3.Connection,
    category: str,
    slug: str,
    inputs_sha256: str,
    *,
    generated_at: str | None = None,
) -> None:
    """Upsert (category, slug) staleness row with given hash and timestamp."""
    ts = generated_at if generated_at is not None else format_iso_now()
    conn.execute(
        "INSERT INTO entity_page_staleness(category, slug, inputs_sha256, generated_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(category, slug) DO UPDATE SET"
        "  inputs_sha256=excluded.inputs_sha256,"
        "  generated_at=excluded.generated_at",
        (category, slug, inputs_sha256, ts),
    )
