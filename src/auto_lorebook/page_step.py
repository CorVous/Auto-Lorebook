"""Batched page-step orchestrator for Stage 4.

Regenerates .md files for a set of touched entities after all facts are
decided. Reports progress to stdout.

Public API:
    run_page_step(conn, wiki_repo, touched_entities, entity_index,
                  wiki_setting, client, model) -> list[Path]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import stage4 as stage4_mod

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
    """Regenerate .md pages for all touched entities; report progress.

    :param touched_entities: list of (category, slug) pairs
    :returns: list of written paths
    """
    n = len(touched_entities)
    if n == 0:
        return []
    print(f"Summarizing {n} {'entity' if n == 1 else 'entities'}...")  # noqa: T201
    paths: list[Path] = []
    for category, slug in touched_entities:
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
            )
            paths.append(path)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("page_step: skipping %s/%s: %s", category, slug, exc)
    return paths
