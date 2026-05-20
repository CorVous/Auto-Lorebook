"""Stage 4 summary regeneration: render entity Markdown from DB.

Stage 4 is mechanical: groups facts by status bucket and renders bullets
with citations. Rich LLM-driven summarizer prose is deferred to a future
ticket.

Public API:
    regenerate_entity(conn, wiki_repo, category, slug) -> Path
    regenerate_all(conn, wiki_repo) -> list[Path]
    delete_entity_summary(wiki_repo, category, slug) -> None
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import entities as entities_mod
from auto_lorebook import facts as facts_mod
from auto_lorebook._io import atomic_write_text

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

_logger = logging.getLogger(__name__)

_STATUS_ORDER = ("authoritative", "trustworthy", "hearsay", "disproven")


def _summary_path(wiki_repo: Path, category: str, slug: str) -> Path:
    return wiki_repo / category / f"{slug}.md"


def _source_url_with_ts(source_url: str | None, locator: str) -> str | None:
    """Append timestamp query param to YouTube URLs."""
    if not source_url:
        return None
    # YouTube: convert H:MM:SS or MM:SS to seconds and add &t=N
    parts = locator.split(":")
    try:
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), int(float(parts[2]))
            secs = h * 3600 + m * 60 + s
        elif len(parts) == 2:
            m, s = int(parts[0]), int(float(parts[1]))
            secs = m * 60 + s
        else:
            return source_url
    except (ValueError, IndexError):
        return source_url
    if "youtube.com" in source_url or "youtu.be" in source_url:
        sep = "&" if "?" in source_url else "?"
        return f"{source_url}{sep}t={secs}"
    return source_url


def _render_entity(
    entity: entities_mod.EntityRow,
    aliases: list[entities_mod.AliasRow],
    fact_rows: list[facts_mod.FactRow],
    conn: sqlite3.Connection,
) -> str:
    """Build Markdown string for one entity."""
    lines: list[str] = [f"# {entity.canonical_name}", ""]

    if aliases:
        lines.extend(("## Aliases", *[f"- {a.name}" for a in aliases], ""))

    # group facts by status
    by_status: dict[str, list[facts_mod.FactRow]] = {s: [] for s in _STATUS_ORDER}
    for fact in fact_rows:
        bucket = fact.status if fact.status in by_status else "hearsay"
        by_status[bucket].append(fact)

    has_any = any(bool(v) for v in by_status.values())
    if has_any:
        lines.extend(("## Facts", ""))

    for status in _STATUS_ORDER:
        bucket = by_status[status]
        if not bucket:
            continue
        lines.append(f"### {status.capitalize()}")
        # resolve source_url once per unique source_id
        source_urls: dict[str, str | None] = {}
        for fact in bucket:
            if fact.source_id not in source_urls:
                row = conn.execute(
                    "SELECT source_url FROM sources WHERE source_id=?",
                    (fact.source_id,),
                ).fetchone()
                source_urls[fact.source_id] = row[0] if row else None

            src_url = _source_url_with_ts(source_urls[fact.source_id], fact.locator)
            target_row = conn.execute(
                "SELECT section FROM fact_targets"
                " WHERE fact_id=? AND entity_category=? AND entity_slug=?",
                (fact.id, entity.category, entity.slug),
            ).fetchone()
            section_label = target_row[0] if target_row else ""

            citation = f"[{fact.locator}]({src_url})" if src_url else fact.locator

            if status == "disproven":
                text_part = f"~~{fact.text}~~"
                reason_part = f"  ({fact.status_reason})" if fact.status_reason else ""
                lines.append(
                    f"- {text_part}  ({citation})"
                    f"  (section: {section_label}){reason_part}"
                )
            else:
                lines.append(f"- {fact.text}  ({citation})  (section: {section_label})")
        lines.append("")

    return "\n".join(lines)


def regenerate_entity(
    conn: sqlite3.Connection,
    wiki_repo: Path,
    category: str,
    slug: str,
) -> Path:
    """Render entity Markdown from DB; write atomically. Return path."""
    entity = entities_mod.get_entity(conn, category, slug)
    if entity is None:
        msg = f"entity not found: {category}/{slug}"
        raise ValueError(msg)
    aliases = entities_mod.list_aliases(conn, category, slug)
    fact_rows = facts_mod.list_facts_by_entity(conn, category, slug)
    text = _render_entity(entity, aliases, fact_rows, conn)
    path = _summary_path(wiki_repo, category, slug)
    atomic_write_text(path, text)
    _logger.debug("summary_regen: wrote %s", path)
    return path


def regenerate_all(
    conn: sqlite3.Connection,
    wiki_repo: Path,
) -> list[Path]:
    """Regenerate .md for every entity in DB."""
    rows = entities_mod.list_entities(conn)
    paths: list[Path] = []
    for entity in rows:
        try:
            p = regenerate_entity(conn, wiki_repo, entity.category, entity.slug)
            paths.append(p)
        except ValueError as exc:
            _logger.warning(
                "summary_regen: skipping %s/%s: %s", entity.category, entity.slug, exc
            )
    return paths


def delete_entity_summary(wiki_repo: Path, category: str, slug: str) -> None:
    """Delete entity .md file (silent if absent)."""
    _summary_path(wiki_repo, category, slug).unlink(missing_ok=True)
