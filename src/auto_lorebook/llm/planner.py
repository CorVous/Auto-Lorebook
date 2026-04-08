"""Stage 2 planner: decides what wiki actions to take from pre-processor output."""

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING

from auto_lorebook.models import EntityAction, PlannerOutput, SourceReference

if TYPE_CHECKING:
    from auto_lorebook.llm.client import OpenRouterClient
    from auto_lorebook.models import PreprocessorOutput, TranscriptChunk

DEFAULT_PLANNER_MODEL = "anthropic/claude-opus-4"

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a lore planning assistant. You will receive:
    1. Numbered transcript sections with optional timestamps.
    2. The Stage 1 analysis: which sections relate to which existing wiki entities,
       and a list of potentially new entity names not yet in the wiki.
    3. Existing wiki page content.

    Your job is to decide what actions are needed to keep the wiki up to date:
    - "create": The entity does not exist in the wiki and deserves a new page.
    - "update": The entity exists but the transcript adds meaningful new information.
    - "merge":  The entity exists and the transcript contradicts or significantly
                enriches existing content in a way that requires restructuring.

    For every action you recommend, cite the specific transcript chunks (by index)
    that support it and include a short quote from that chunk.

    Respond ONLY with valid JSON matching this exact schema:
    {
      "entity_actions": [
        {
          "entity_name": <str>,
          "category": <str>,
          "action": "create" | "update" | "merge",
          "info_to_add": <str>,
          "source_refs": [
            {"chunk_index": <int>, "quote": <str>, "timestamp_seconds": <float | null>}
          ],
          "rationale": <str>
        }
      ],
      "summary": <str>
    }

    If no actions are needed, return an empty entity_actions array and a brief summary.
""")


def _build_user_message(
    preprocessor_output: PreprocessorOutput,
    chunks: list[TranscriptChunk],
    wiki_pages: dict[str, str],
) -> str:
    """Format the user message with transcript sections, Stage 1 analysis, and wiki.

    :param preprocessor_output: Output from the Stage 1 pre-processor.
    :param chunks: Original transcript chunks (indexed).
    :param wiki_pages: Mapping of wiki page title → markdown content.
    :return: Formatted user message string.
    """
    lines: list[str] = ["## Transcript Sections"]
    for i, chunk in enumerate(chunks):
        ts = ""
        if chunk.start_seconds is not None:
            ts = f" [{chunk.start_seconds:.1f}s]"
        lines.append(f"[{i}]{ts} {chunk.text}")

    lines.extend(["\n## Stage 1 Analysis", "### Section-to-Entity Mappings"])
    for mapping in preprocessor_output.section_mappings:
        chunk_idx = next(
            (i for i, c in enumerate(chunks) if c is mapping.chunk),
            None,
        )
        if mapping.relevant_wiki_excerpts:
            for exc in mapping.relevant_wiki_excerpts:
                idx_str = f"[{chunk_idx}]" if chunk_idx is not None else "[?]"
                lines.append(
                    f"  {idx_str} → {exc.entity_name} ({exc.category}): {exc.content!r}"
                )
        else:
            idx_str = f"[{chunk_idx}]" if chunk_idx is not None else "[?]"
            lines.append(f"  {idx_str} → (no existing entities matched)")

    if preprocessor_output.new_entity_mentions:
        lines.append("\n### Potentially New Entities")
        lines.extend(f"  - {name}" for name in preprocessor_output.new_entity_mentions)
    else:
        lines.append("\n### Potentially New Entities\n  (none identified)")

    if wiki_pages:
        lines.append("\n## Existing Wiki Pages")
        for title, content in wiki_pages.items():
            lines.extend((f"\n### {title}", content))
    else:
        lines.append("\n## Existing Wiki Pages\n(none — this is a fresh wiki)")

    return "\n".join(lines)


def _parse_response(raw: str) -> PlannerOutput:
    """Parse the LLM JSON response into a PlannerOutput.

    :param raw: Raw JSON string from the LLM.
    :return: Parsed PlannerOutput.
    :raises ValueError: If the JSON is malformed or missing required fields.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"planner response is not valid JSON: {exc}"
        raise ValueError(msg) from exc

    if "entity_actions" not in data or "summary" not in data:
        msg = (
            "planner response missing required keys: 'entity_actions' and/or 'summary'"
        )
        raise ValueError(msg)

    entity_actions: list[EntityAction] = []
    for entry in data["entity_actions"]:
        refs = [
            SourceReference(
                chunk_index=int(r["chunk_index"]),
                quote=r["quote"],
                timestamp_seconds=r.get("timestamp_seconds"),
            )
            for r in entry.get("source_refs", [])
        ]
        entity_actions.append(
            EntityAction(
                entity_name=entry["entity_name"],
                category=entry["category"],
                action=entry["action"],
                info_to_add=entry["info_to_add"],
                source_refs=refs,
                rationale=entry["rationale"],
            )
        )

    return PlannerOutput(
        entity_actions=entity_actions,
        summary=data["summary"],
    )


async def run_planner(
    *,
    client: OpenRouterClient,
    preprocessor_output: PreprocessorOutput,
    chunks: list[TranscriptChunk],
    wiki_pages: dict[str, str],
    model: str = DEFAULT_PLANNER_MODEL,
) -> PlannerOutput:
    """Run the Stage 2 planner against pre-processor output.

    Sends the transcript, Stage 1 analysis, and existing wiki pages to the LLM,
    asking it to decide what create/update/merge actions are needed.

    :param client: OpenRouterClient to use for the LLM call.
    :param preprocessor_output: Output from the Stage 1 pre-processor.
    :param chunks: Original transcript chunks (indexed for source references).
    :param wiki_pages: Mapping of wiki page title → markdown content.
    :param model: Model identifier to use for the planner stage.
    :return: PlannerOutput with the list of entity actions and a summary.
    :raises ValueError: If the LLM response cannot be parsed as valid JSON.
    """
    user_message = _build_user_message(preprocessor_output, chunks, wiki_pages)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    raw = await client.chat(
        model,
        messages,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return _parse_response(raw)
