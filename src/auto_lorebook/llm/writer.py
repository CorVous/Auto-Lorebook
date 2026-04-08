"""Stage 3 writer: produces final wiki markdown from the planner's action plan."""

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING

from auto_lorebook.models import WikiPage, WriterOutput

if TYPE_CHECKING:
    from auto_lorebook.llm.client import OpenRouterClient
    from auto_lorebook.models import PlannerOutput, TranscriptChunk

DEFAULT_WRITER_MODEL = "anthropic/claude-sonnet-4"

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a wiki writer for a fantasy lore encyclopedia. You will receive:
    1. Numbered transcript sections with optional timestamps and source URLs.
    2. A plan from the previous stage specifying which entities to create or update,
       what information to add, and which transcript chunks support each action.
    3. Existing wiki page content for entities being updated or merged.

    Your job is to produce complete, polished wiki markdown for each entity in the plan.

    Rules:
    - Write encyclopedic prose, not bullet points. Each section should read naturally.
    - Cross-link related entities using relative markdown links in the format:
      [Entity Name](../category/entity-name.md)
      Use lowercase and hyphens for the filename (e.g. "King Theron" -> king-theron.md).
    - Every factual claim must have an inline citation as a superscript markdown link.
      Citations use sequential numbers per page: [1], [2], etc.
    - For YouTube sources with timestamps, format citation URLs as:
      https://youtube.com/watch?v=VIDEO_ID&t=SECONDS
    - For web sources, use the source URL directly.
    - For local file sources (no URL), cite as: source_filename
    - Include a "## References" section at the bottom of each page listing all citations
      with their full URLs or source descriptions.
    - For "update" or "merge" actions, integrate new information into the
      existing content. Preserve existing citations and add new ones with
      sequential numbering.
    - Start each page with a level-1 heading: # Entity Name

    Respond ONLY with valid JSON matching this exact schema:
    {
      "pages": [
        {
          "entity_name": <str>,
          "category": <str>,
          "markdown": <str>
        }
      ],
      "summary": <str>
    }

    If no pages need to be written, return an empty pages array and a brief summary.
""")


def _build_user_message(
    chunks: list[TranscriptChunk],
    planner_output: PlannerOutput,
    wiki_pages: dict[str, str],
) -> str:
    """Format the user message with transcript, plan, and existing wiki pages.

    :param chunks: Original transcript chunks (indexed).
    :param planner_output: Output from the Stage 2 planner.
    :param wiki_pages: Mapping of wiki page title -> markdown content.
    :return: Formatted user message string.
    """
    lines: list[str] = ["## Transcript Sections"]
    for i, chunk in enumerate(chunks):
        parts: list[str] = [f"[{i}]"]
        if chunk.start_seconds is not None:
            parts.append(f"[{chunk.start_seconds:.1f}s]")
        if chunk.source.source_url:
            parts.append(f"(source: {chunk.source.source_url})")
        else:
            parts.append(f"(source: {chunk.source.filename})")
        parts.append(chunk.text)
        lines.append(" ".join(parts))

    lines.append("\n## Plan")
    for action in planner_output.entity_actions:
        lines.extend((
            f"### {action.entity_name} ({action.category}) — {action.action}",
            f"Info to add: {action.info_to_add}",
        ))
        if action.source_refs:
            ref_strs: list[str] = []
            for ref in action.source_refs:
                ts = ""
                if ref.timestamp_seconds is not None:
                    ts = f" @ {ref.timestamp_seconds:.1f}s"
                ref_strs.append(f"[{ref.chunk_index}] {ref.quote!r}{ts}")
            lines.append(f"Source refs: {', '.join(ref_strs)}")
        lines.append(f"Rationale: {action.rationale}")

    if wiki_pages:
        lines.append("\n## Existing Wiki Pages")
        for title, content in wiki_pages.items():
            lines.extend((f"\n### {title}", content))
    else:
        lines.append("\n## Existing Wiki Pages\n(none — this is a fresh wiki)")

    return "\n".join(lines)


def _parse_response(raw: str) -> WriterOutput:
    """Parse the LLM JSON response into a WriterOutput.

    :param raw: Raw JSON string from the LLM.
    :return: Parsed WriterOutput.
    :raises ValueError: If the JSON is malformed or missing required fields.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"writer response is not valid JSON: {exc}"
        raise ValueError(msg) from exc

    if "pages" not in data or "summary" not in data:
        msg = "writer response missing required keys: 'pages' and/or 'summary'"
        raise ValueError(msg)

    pages: list[WikiPage] = [
        WikiPage(
            entity_name=entry["entity_name"],
            category=entry["category"],
            markdown=entry["markdown"],
        )
        for entry in data["pages"]
    ]

    return WriterOutput(pages=pages, summary=data["summary"])


async def run_writer(
    *,
    client: OpenRouterClient,
    chunks: list[TranscriptChunk],
    planner_output: PlannerOutput,
    wiki_pages: dict[str, str],
    model: str = DEFAULT_WRITER_MODEL,
) -> WriterOutput:
    """Run the Stage 3 writer against the planner's action plan.

    Sends the transcript, plan, and existing wiki pages to the LLM,
    asking it to produce final wiki markdown with citations and cross-links.

    :param client: OpenRouterClient to use for the LLM call.
    :param chunks: Original transcript chunks (indexed for source references).
    :param planner_output: Output from the Stage 2 planner.
    :param wiki_pages: Mapping of wiki page title -> markdown content.
    :param model: Model identifier to use for the writer stage.
    :return: WriterOutput with the list of wiki pages and a summary.
    :raises ValueError: If the LLM response cannot be parsed as valid JSON.
    """
    user_message = _build_user_message(chunks, planner_output, wiki_pages)
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
