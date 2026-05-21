"""Batched page-step orchestrator for Stage 4.

Regenerates .md files for touched entities and their one-hop linked
entities after all facts are decided. Reports progress to stdout.

On reject-ingest, pass ``removed_entities`` to delete removed pages
and exclude removed entities from regeneration.

Public API:
    run_page_step(conn, wiki_repo, touched_entities, entity_index,
                  wiki_setting, client, model,
                  removed_entities) -> list[Path]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import entities as entities_mod
from auto_lorebook import facts as facts_mod
from auto_lorebook import stage4 as stage4_mod
from auto_lorebook.linked_budget import (
    LinkedContextTooLargeError,
    budget_linked_context,
)
from auto_lorebook.regen_set import plan_regeneration_set
from auto_lorebook.staleness_store import (
    compute_page_inputs_hash,
    get_page_hash,
    record_page_hash,
)
from auto_lorebook.summary_regen import delete_entity_summary

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
    removed_entities: list[tuple[str, str]] | None = None,
    entity_index: str = "",
    wiki_setting: str = "",
    client: OpenRouterClient,
    model: str = "",
    context_window: int = 200_000,
    budget_fraction: float = 0.25,
    skip_unchanged: bool = False,
) -> list[Path]:
    """Regenerate .md pages for touched entities and one-hop linked entities.

    :param touched_entities: (category, slug) pairs to summarize
    :param removed_entities: (category, slug) pairs whose pages to delete;
        excluded from regeneration even if also in touched_entities
    :param context_window: model context window for linked-context budgeting
    :param budget_fraction: fraction of context_window for linked context block
    :param skip_unchanged: skip pages whose inputs hash matches stored hash
    :returns: list of written paths
    """
    removed_set: frozenset[tuple[str, str]] = (
        frozenset(removed_entities) if removed_entities else frozenset()
    )
    # delete pages of removed entities
    for cat, slug in removed_set:
        delete_entity_summary(wiki_repo, cat, slug)

    if not touched_entities and not removed_set:
        return []

    # filter removed entities out of regen inputs
    filtered_touched = [e for e in touched_entities if e not in removed_set]

    if not filtered_touched and not removed_set:
        return []

    def linked_of(entity: tuple[str, str]) -> list[tuple[str, str]]:
        # exclude removed entities from linked set
        return [
            e
            for e in facts_mod.list_linked_entities(conn, entity[0], entity[1])
            if e not in removed_set
        ]

    regen = plan_regeneration_set(filtered_touched, linked_of)
    n_touched = len(regen.touched)
    n_linked = len(regen.linked)

    if not regen.ordered:
        return []

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
        entity = entities_mod.get_entity(conn, category, slug)

        # collect linked-entity facts for synthesis context
        linked_keys = facts_mod.list_linked_entities(conn, category, slug)
        linked_facts = []
        for nb_cat, nb_slug in linked_keys:
            nb_entity = entities_mod.get_entity(conn, nb_cat, nb_slug)
            if nb_entity is None:
                continue
            nb_facts = facts_mod.list_facts_by_entity(conn, nb_cat, nb_slug)
            linked_facts.append((nb_entity, nb_facts))

        # apply token budget to linked context
        own_facts = facts_mod.list_facts_by_entity(conn, category, slug)
        subject_fact_ids = {f.id for f in own_facts}
        try:
            linked_facts = budget_linked_context(
                linked_facts,
                subject_fact_ids,
                context_window=context_window,
                budget_fraction=budget_fraction,
            )
        except LinkedContextTooLargeError as exc:
            _logger.warning(
                "page_step: linked context too large for %s/%s, dropping: %s",
                category,
                slug,
                exc,
            )
            linked_facts = []

        # staleness check
        if entity is not None:
            aliases = entities_mod.list_aliases(conn, category, slug)
            current_hash: str | None = compute_page_inputs_hash(
                entity=entity,
                aliases=aliases,
                facts=own_facts,
                linked_facts=linked_facts,
                entity_index=entity_index,
                wiki_setting=wiki_setting,
                model=model,
                model_params={},
            )
        else:
            current_hash = None
        if (
            skip_unchanged
            and current_hash is not None
            and (get_page_hash(conn, category, slug) == current_hash)
        ):
            _logger.debug("page_step: skipping unchanged %s/%s", category, slug)
            continue

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
            if current_hash is not None:
                record_page_hash(conn, category, slug, current_hash)
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("page_step: skipping %s/%s: %s", category, slug, exc)
    return paths
