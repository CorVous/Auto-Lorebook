"""Batched page-step orchestrator for Stage 4.

Regenerates .md files for touched entities and their one-hop linked
entities after all facts are decided. Reports progress to stdout.

Public API:
    run_page_step(conn, wiki_repo, touched_entities, entity_index,
                  wiki_setting, client, model) -> list[Path]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import entities as entities_mod
from auto_lorebook import facts as facts_mod
from auto_lorebook import stage4 as stage4_mod
from auto_lorebook.regen_set import plan_regeneration_set

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from auto_lorebook.openrouter import OpenRouterClient

_logger = logging.getLogger(__name__)


def run_page_step(
    conn: sqlite3.Connection,
    wiki_repo: Path,
    touched_entities: list[tuple[str, str]],
    *,
    entity_index: str = "",
    wiki_setting: str = "",
    client: OpenRouterClient,
    model: str = "",
) -> list[Path]:
    """Regenerate .md pages for touched entities and one-hop linked entities.

    :param touched_entities: list of (category, slug) pairs
    :returns: list of written paths
    """
    if not touched_entities:
        return []

    def linked_of(entity: tuple[str, str]) -> list[tuple[str, str]]:
        return facts_mod.list_linked_entities(conn, entity[0], entity[1])

    regen = plan_regeneration_set(touched_entities, linked_of)
    n_touched = len(regen.touched)
    n_linked = len(regen.linked)

    if n_linked:
        print(  # noqa: T201
            f"Summarizing {n_touched} touched + {n_linked} linked"
            f" {'entity' if n_touched + n_linked == 1 else 'entities'}..."
        )
    else:
        n = n_touched
        print(f"Summarizing {n} {'entity' if n == 1 else 'entities'}...")  # noqa: T201

    paths: list[Path] = []
    for category, slug in regen.ordered:
        # collect linked-entity facts for synthesis context
        linked_keys = facts_mod.list_linked_entities(conn, category, slug)
        linked_facts = []
        for nb_cat, nb_slug in linked_keys:
            nb_entity = entities_mod.get_entity(conn, nb_cat, nb_slug)
            if nb_entity is None:
                continue
            nb_facts = facts_mod.list_facts_by_entity(conn, nb_cat, nb_slug)
            linked_facts.append((nb_entity, nb_facts))

        try:
            path = stage4_mod.summarize_entity(
                conn,
                wiki_repo,
                category,
                slug,
                entity_index=entity_index,
                wiki_setting=wiki_setting,
                client=client,
                model=model,
                linked_facts=linked_facts or None,
            )
            paths.append(path)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("page_step: skipping %s/%s: %s", category, slug, exc)
    return paths
