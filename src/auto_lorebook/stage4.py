"""Stage 4: LLM-prose entity summarizer.

Generates readable prose pages for entities with approved facts.
Zero-fact entities get a mechanical stub with no LLM call.

Public API:
    Stage4Error, SummarizeResult
    build_prompt, parse_response, run
    render_entity_page, summarize_entity
"""

from __future__ import annotations

import logging
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
) -> str:
    """Assemble user message from entity facts, index, and wiki setting."""
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
) -> str:
    """Render full Markdown page for entity.

    Zero-fact entities (prose=None): mechanical stub, no ``## Summary``.
    Entities with facts: ``## Summary`` + prose + status-grouped facts + references.
    """
    lines: list[str] = [f"# {entity.canonical_name}", ""]

    if aliases:
        lines.extend(["## Aliases", *[f"- {a.name}" for a in aliases], ""])

    # zero-fact stub — minimal mechanical output
    if not facts or prose is None:
        return "\n".join(lines)

    # prose summary section
    lines.extend(["## Summary", "", prose.strip(), ""])

    # group facts by status; assign footnote numbers
    by_status: dict[str, list[FactRow]] = {s: [] for s in _STATUS_ORDER}
    for fact in facts:
        bucket = fact.status if fact.status in by_status else "hearsay"
        by_status[bucket].append(fact)

    # build footnote index: fact_id → footnote number
    fn_counter = 0
    fact_footnote: dict[str, int] = {}
    for status in _STATUS_ORDER:
        for fact in by_status[status]:
            fn_counter += 1
            fact_footnote[fact.id] = fn_counter

    # ## Facts section
    lines.extend(["## Facts", ""])
    for status in _STATUS_ORDER:
        bucket = by_status[status]
        if not bucket:
            continue
        lines.extend((f"### {status.capitalize()}", ""))
        for fact in bucket:
            fn = fact_footnote[fact.id]
            if status == "disproven":
                text_part = f"~~{fact.text}~~"
                reason_part = f" — {fact.status_reason}" if fact.status_reason else ""
                lines.append(f"[^{fn}]: {text_part}{reason_part}")
            else:
                lines.append(f"[^{fn}]: {fact.text}")
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
            fn = fact_footnote[fact.id]
            src_url = _resolve_source_url(conn, fact.source_id, fact.locator)
            link = f"[{fact.locator}]({src_url})" if src_url else fact.locator
            speaker = f"— {fact.speaker}, " if fact.speaker else ""
            session = f" (session: {fact.session_date})" if fact.session_date else ""
            quote = f'"{fact.text}"'
            if status == "disproven":
                reason = f"\n  *{fact.status_reason}*" if fact.status_reason else ""
                lines.append(f"[^{fn}]: {quote}  {speaker}{link}{session}{reason}")
            else:
                lines.append(f"[^{fn}]: {quote}  {speaker}{link}{session}")

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
) -> Path:
    """Render entity page (LLM prose or stub); write atomically. Return path.

    Zero-fact entities: write mechanical stub, no LLM call.
    Entities with facts: call LLM, write prose page.
    """
    entity = entities_mod.get_entity(conn, category, slug)
    if entity is None:
        msg = f"entity not found: {category}/{slug}"
        raise ValueError(msg)
    aliases = entities_mod.list_aliases(conn, category, slug)
    fact_rows = facts_mod.list_facts_by_entity(conn, category, slug)

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
        )
        prose = result.prose

    text = render_entity_page(
        entity=entity,
        aliases=aliases,
        facts=fact_rows,
        prose=prose,
        conn=conn,
    )
    path = _summary_path(wiki_repo, category, slug)
    atomic_write_text(path, text)
    _logger.debug("stage4: wrote %s", path)
    return path
