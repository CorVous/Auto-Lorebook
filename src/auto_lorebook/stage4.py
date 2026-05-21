"""Stage 4: LLM-prose entity summarizer.

Generates readable prose pages for entities with approved facts.
Zero-fact entities get a mechanical stub with no LLM call.
Linked-entity facts (one-hop co-targets) can be threaded in for synthesis context.

Public API:
    Stage4Error, SummarizeResult
    build_prompt, parse_response, run
    render_entity_page, summarize_entity
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auto_lorebook import entities as entities_mod
from auto_lorebook import facts as facts_mod
from auto_lorebook._io import atomic_write_text
from auto_lorebook.llm_helpers import build_system_prompt, parse_json_object

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

    from auto_lorebook.entities import AliasRow, EntityRow
    from auto_lorebook.facts import FactRow
    from auto_lorebook.openrouter import OpenRouterClient

_logger = logging.getLogger(__name__)

_STATUS_ORDER = ("authoritative", "trustworthy", "hearsay", "disproven")

_TASK_INSTRUCTIONS = """\
You are writing a prose summary for one entity in a worldbuilding wiki.
The entity's facts (approved claims from transcripts) are listed below,
grouped by epistemic status. Write a cohesive, readable summary paragraph
that integrates these facts naturally.

Rules:
- Write in third person, present tense.
- Authoritative facts: state as plain fact.
- Trustworthy facts: attribute to source ("According to X, ...").
- Hearsay facts: hedge appropriately ("Some accounts suggest ...", "Rumors hold ...").
- Disproven facts: do NOT include in the prose; they appear only in the Facts section.
- Be concise. One to three paragraphs.
- Do NOT invent facts beyond what is listed.
- Linked-entity facts (below): you MAY synthesize a claim drawn from a
  linked entity's fact, with the same epistemic-status hedging as above.
  Do not fabricate beyond what is listed.
- Markers: use [[category/slug]] to link an entity by name (e.g. [[locations/aldara]]);
  use [[fact:<fact_id>]] to cite a linked entity's fact (e.g. [[fact:f-n01]]).

Emit a single JSON object:
{
  "prose": "<the prose summary>"
}

Emit ONLY the JSON object. No prose outside it, no code fences.
"""


class Stage4Error(RuntimeError):
    """Stage 4 failed: bad LLM output or schema violation."""


@dataclass(frozen=True)
class SummarizeResult:
    """LLM output from the summarizer stage."""

    prose: str


def build_prompt(
    *,
    entity: EntityRow,
    aliases: list[AliasRow],
    facts: list[FactRow],
    entity_index: str,
    wiki_setting: str,
    linked_facts: list[tuple[EntityRow, list[FactRow]]] | None = None,
) -> str:
    """Assemble user message from entity facts, index, wiki setting, linked context."""
    parts: list[str] = []

    if wiki_setting.strip():
        parts.extend(["Setting context:", wiki_setting.strip(), ""])

    parts.extend([
        f"Entity: {entity.canonical_name}",
        f"Category: {entity.category}",
    ])
    if aliases:
        alias_names = ", ".join(a.name for a in aliases)
        parts.append(f"Aliases: {alias_names}")
    parts.append("")

    if entity_index.strip():
        parts.extend([
            "Entity index (for cross-reference awareness):",
            entity_index.strip(),
            "",
        ])

    # group facts by status
    by_status: dict[str, list[FactRow]] = {s: [] for s in _STATUS_ORDER}
    for fact in facts:
        bucket = fact.status if fact.status in by_status else "hearsay"
        by_status[bucket].append(fact)

    parts.append("Facts (by epistemic status):")
    for status in _STATUS_ORDER:
        bucket = by_status[status]
        if not bucket:
            continue
        parts.append(f"\n{status.upper()}:")
        for fact in bucket:
            reason = f" [reason: {fact.status_reason}]" if fact.status_reason else ""
            speaker = f" (speaker: {fact.speaker})" if fact.speaker else ""
            parts.append(f"  - [{fact.id}] {fact.text}{speaker}{reason}")

    # linked-entity context block (one-hop linked entities)
    if linked_facts:
        parts.extend(["", "Linked entities (for synthesis context):"])
        for linked_ent, linked_fact_rows in linked_facts:
            name = linked_ent.canonical_name
            cat_slug = f"{linked_ent.category}/{linked_ent.slug}"
            parts.append(f"\n{name} ({cat_slug}):")
            nb_by_status: dict[str, list[FactRow]] = {s: [] for s in _STATUS_ORDER}
            for fact in linked_fact_rows:
                bucket = fact.status if fact.status in nb_by_status else "hearsay"
                nb_by_status[bucket].append(fact)
            for status in _STATUS_ORDER:
                nb_bucket = nb_by_status[status]
                if not nb_bucket:
                    continue
                parts.append(f"  {status.upper()}:")
                for fact in nb_bucket:
                    reason = (
                        f" [reason: {fact.status_reason}]" if fact.status_reason else ""
                    )
                    speaker = f" (speaker: {fact.speaker})" if fact.speaker else ""
                    parts.append(f"    - [{fact.id}] {fact.text}{speaker}{reason}")

    return "\n".join(parts)


def parse_response(payload: dict[str, Any]) -> SummarizeResult:
    """Extract SummarizeResult from LLM JSON payload.

    :raises Stage4Error: missing prose field
    """
    if "prose" not in payload:
        msg = "Stage 4 response missing required 'prose' field"
        raise Stage4Error(msg)
    return SummarizeResult(prose=str(payload["prose"]))


def run(
    *,
    entity: EntityRow,
    aliases: list[AliasRow],
    facts: list[FactRow],
    entity_index: str,
    wiki_setting: str,
    client: OpenRouterClient,
    model: str,
    linked_facts: list[tuple[EntityRow, list[FactRow]]] | None = None,
) -> SummarizeResult:
    """Run Stage 4 LLM call for one entity; return SummarizeResult.

    :raises Stage4Error: bad LLM output
    """
    user_msg = build_prompt(
        entity=entity,
        aliases=aliases,
        facts=facts,
        entity_index=entity_index,
        wiki_setting=wiki_setting,
        linked_facts=linked_facts,
    )
    messages = [
        {
            "role": "system",
            "content": build_system_prompt("", _TASK_INSTRUCTIONS),
        },
        {"role": "user", "content": user_msg},
    ]
    resp = client.complete(
        messages,
        model=model,
        response_format={"type": "json_object"},
    )
    try:
        payload = parse_json_object(resp.text, "Stage 4")
    except ValueError as e:
        raise Stage4Error(str(e)) from e
    return parse_response(payload)


_ENTITY_LINK_RE = re.compile(r"\[\[([a-z0-9-]+)/([a-z0-9-]+)\]\]")
_FACT_REF_RE = re.compile(r"\[\[fact:([^\]]+)\]\]")


def _resolve_entity_links(
    prose: str,
    entity_lookup: dict[tuple[str, str], EntityRow],
    *,
    from_category: str,
) -> str:
    """Scan [[category/slug]] markers; resolve to markdown links or plain text.

    Same-category: bare ``slug.md``; cross-category: ``../category/slug.md``.
    Unresolvable: raw ``category/slug`` text + logged warning.  Never raises.
    """

    def _replace(m: re.Match[str]) -> str:
        cat, slug = m.group(1), m.group(2)
        entity = entity_lookup.get((cat, slug))
        if entity is None:
            _logger.warning("unresolvable entity marker: %s/%s", cat, slug)
            return f"{cat}/{slug}"
        path = f"{slug}.md" if cat == from_category else f"../{cat}/{slug}.md"
        return f"[{entity.canonical_name}]({path})"

    return _ENTITY_LINK_RE.sub(_replace, prose)


def _resolve_crossref_markers(
    prose: str,
    linked_fact_index: dict[str, tuple[EntityRow, FactRow]],
    *,
    from_category: str,
) -> tuple[str, list[str]]:
    """Scan [[fact:<id>]] markers; resolve to footnote refs + footnote defs.

    Resolvable: inline marker → ``[^<id>]``; footnote def quotes linked fact
    text and links to linked entity's page anchored at ``#fn:<id>``.
    Unresolvable: plain text + logged warning.  Never raises.
    Returns (rewritten_prose, footnote_def_lines).
    """
    footnote_defs: list[str] = []
    seen_fact_ids: set[str] = set()

    def _replace(m: re.Match[str]) -> str:
        fact_id = m.group(1)
        entry = linked_fact_index.get(fact_id)
        if entry is None:
            _logger.warning("unresolvable fact marker: %s", fact_id)
            return fact_id
        linked_ent, linked_fact = entry
        cat = linked_ent.category
        slug = linked_ent.slug
        path = f"{slug}.md" if cat == from_category else f"../{cat}/{slug}.md"
        anchor = f"#fn:{fact_id}"
        quote = f'"{linked_fact.text}"'
        link = f"[{linked_ent.canonical_name}]({path}{anchor})"
        if fact_id not in seen_fact_ids:
            seen_fact_ids.add(fact_id)
            footnote_defs.append(f"[^{fact_id}]: {quote} — {link}")
        return f"[^{fact_id}]"

    rewritten = _FACT_REF_RE.sub(_replace, prose)
    return rewritten, footnote_defs


def _source_url_with_ts(source_url: str | None, locator: str) -> str | None:
    """Append timestamp query param to YouTube URLs."""
    if not source_url:
        return None
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


def _resolve_source_url(
    conn: sqlite3.Connection | None,
    source_id: str,
    locator: str,
) -> str | None:
    """Look up source URL and apply timestamp; returns None if conn is None."""
    if conn is None:
        return None
    row = conn.execute(
        "SELECT source_url FROM sources WHERE source_id=?",
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    return _source_url_with_ts(row[0], locator)


def render_entity_page(
    *,
    entity: EntityRow,
    aliases: list[AliasRow],
    facts: list[FactRow],
    prose: str | None,
    conn: sqlite3.Connection | None,
    entity_lookup: dict[tuple[str, str], EntityRow] | None = None,
    linked_facts: list[tuple[EntityRow, list[FactRow]]] | None = None,
) -> str:
    """Render full Markdown page for entity.

    Zero-fact entities (prose=None): mechanical stub, no ``## Summary``.
    Entities with facts: ``## Summary`` + prose + status-grouped facts + references.
    entity_lookup: index for [[category/slug]] marker resolution.
    linked_facts: linked-entity pairs for [[fact:<id>]] crossref resolution.
    """
    lines: list[str] = [f"# {entity.canonical_name}", ""]

    if aliases:
        lines.extend(["## Aliases", *[f"- {a.name}" for a in aliases], ""])

    # zero-fact stub — minimal mechanical output
    if not facts or prose is None:
        return "\n".join(lines)

    # resolve entity-link and crossref markers in prose
    resolved_prose = prose.strip()
    crossref_footnote_defs: list[str] = []
    if entity_lookup is not None:
        resolved_prose = _resolve_entity_links(
            resolved_prose, entity_lookup, from_category=entity.category
        )
    if linked_facts is not None:
        # build flat index: fact_id → (entity, fact)
        linked_fact_index: dict[str, tuple[EntityRow, FactRow]] = {}
        for linked_ent, linked_fact_rows in linked_facts:
            for lf in linked_fact_rows:
                linked_fact_index[lf.id] = (linked_ent, lf)
        resolved_prose, crossref_footnote_defs = _resolve_crossref_markers(
            resolved_prose, linked_fact_index, from_category=entity.category
        )

    # prose summary section
    lines.extend(["## Summary", "", resolved_prose, ""])

    # group facts by status; use fact.id as stable anchor label
    by_status: dict[str, list[FactRow]] = {s: [] for s in _STATUS_ORDER}
    for fact in facts:
        bucket = fact.status if fact.status in by_status else "hearsay"
        by_status[bucket].append(fact)

    # ## Facts section
    lines.extend(["## Facts", ""])
    for status in _STATUS_ORDER:
        bucket = by_status[status]
        if not bucket:
            continue
        lines.extend((f"### {status.capitalize()}", ""))
        for fact in bucket:
            if status == "disproven":
                text_part = f"~~{fact.text}~~"
                reason_part = f" — {fact.status_reason}" if fact.status_reason else ""
                lines.append(f"[^{fact.id}]: {text_part}{reason_part}")
            else:
                lines.append(f"[^{fact.id}]: {fact.text}")
        lines.append("")

    # ## References section
    source_ids_seen: list[str] = []
    source_ids_set: set[str] = set()
    for status in _STATUS_ORDER:
        for fact in by_status[status]:
            if fact.source_id not in source_ids_set:
                source_ids_set.add(fact.source_id)
                source_ids_seen.append(fact.source_id)

    if source_ids_seen:
        lines.extend(["## References", ""])
        for i, source_id in enumerate(source_ids_seen, start=1):
            url: str | None = None
            title: str | None = None
            if conn is not None:
                row = conn.execute(
                    "SELECT source_url, title FROM sources WHERE source_id=?",
                    (source_id,),
                ).fetchone()
                if row:
                    url = row[0]
                    title = row[1] if len(row) > 1 else None
            label = title or source_id
            if url:
                lines.append(f"{i}. {label} — {url}")
            else:
                lines.append(f"{i}. {label}")
        lines.append("")

    # per-fact footnote citations with full details
    lines.append("")
    for status in _STATUS_ORDER:
        for fact in by_status[status]:
            src_url = _resolve_source_url(conn, fact.source_id, fact.locator)
            link = f"[{fact.locator}]({src_url})" if src_url else fact.locator
            speaker = f"— {fact.speaker}, " if fact.speaker else ""
            session = f" (session: {fact.session_date})" if fact.session_date else ""
            quote = f'"{fact.text}"'
            if status == "disproven":
                reason = f"\n  *{fact.status_reason}*" if fact.status_reason else ""
                lines.append(f"[^{fact.id}]: {quote}  {speaker}{link}{session}{reason}")
            else:
                lines.append(f"[^{fact.id}]: {quote}  {speaker}{link}{session}")

    # crossref footnote defs (linked-entity citations)
    if crossref_footnote_defs:
        lines.append("")
        lines.extend(crossref_footnote_defs)

    return "\n".join(lines)


def _summary_path(wiki_repo: Path, category: str, slug: str) -> Path:
    return wiki_repo / category / f"{slug}.md"


def summarize_entity(
    conn: sqlite3.Connection,
    wiki_repo: Path,
    category: str,
    slug: str,
    *,
    entity_index: str,
    wiki_setting: str,
    client: OpenRouterClient,
    model: str,
    linked_facts: list[tuple[EntityRow, list[FactRow]]] | None = None,
) -> Path:
    """Render entity page (LLM prose or stub); write atomically. Return path.

    Zero-fact entities: write mechanical stub, no LLM call.
    Entities with facts: call LLM, write prose page.
    linked_facts: linked-entity (entity, facts) pairs for synthesis context.
    """
    entity = entities_mod.get_entity(conn, category, slug)
    if entity is None:
        msg = f"entity not found: {category}/{slug}"
        raise ValueError(msg)
    aliases = entities_mod.list_aliases(conn, category, slug)
    fact_rows = facts_mod.list_facts_by_entity(conn, category, slug)

    # build entity lookup for [[category/slug]] marker resolution
    all_entities = entities_mod.list_entities(conn)
    entity_lookup: dict[tuple[str, str], EntityRow] = {
        (e.category, e.slug): e for e in all_entities
    }

    prose: str | None = None
    if fact_rows:
        result = run(
            entity=entity,
            aliases=aliases,
            facts=fact_rows,
            entity_index=entity_index,
            wiki_setting=wiki_setting,
            client=client,
            model=model,
            linked_facts=linked_facts,
        )
        prose = result.prose

    text = render_entity_page(
        entity=entity,
        aliases=aliases,
        facts=fact_rows,
        prose=prose,
        conn=conn,
        entity_lookup=entity_lookup,
        linked_facts=linked_facts,
    )
    path = _summary_path(wiki_repo, category, slug)
    atomic_write_text(path, text)
    _logger.debug("stage4: wrote %s", path)
    return path
