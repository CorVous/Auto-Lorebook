"""Stage 1 pre-processor: maps transcript chunks to existing wiki entities."""

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING

from auto_lorebook.models import (
    PreprocessorOutput,
    SectionMapping,
    SourceMetadata,
    TranscriptChunk,
    WikiExcerpt,
)

if TYPE_CHECKING:
    from auto_lorebook.llm.client import OpenRouterClient

DEFAULT_PREPROCESSOR_MODEL = "anthropic/claude-3.5-haiku"

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a lore analysis assistant. You will receive numbered transcript sections
    and a set of existing wiki pages. Your job is to:

    1. For each transcript section, identify which wiki entities it relates to and
       provide a brief relevant excerpt from that wiki page.
    2. Identify any entity names in the transcript that do not appear in any wiki page
       — these are potentially new entities worth creating pages for.

    Respond ONLY with valid JSON matching this exact schema:
    {
      "section_mappings": [
        {
          "chunk_index": <int>,
          "relevant_entities": [
            {"entity_name": <str>, "category": <str>, "excerpt": <str>}
          ]
        }
      ],
      "new_entity_mentions": [<str>, ...]
    }
""")


def _build_user_message(
    chunks: list[TranscriptChunk], wiki_pages: dict[str, str]
) -> str:
    """Format the user message with transcript sections and wiki page content.

    :param chunks: Transcript chunks to analyse.
    :param wiki_pages: Mapping of wiki page title → markdown content.
    :return: Formatted user message string.
    """
    lines: list[str] = ["## Transcript Sections"]
    for i, chunk in enumerate(chunks):
        lines.append(f"[{i}] {chunk.text}")

    if wiki_pages:
        lines.append("\n## Wiki Pages")
        for title, content in wiki_pages.items():
            lines.extend((f"\n### {title}", content))
    else:
        lines.append("\n## Wiki Pages\n(none — this is a fresh wiki)")

    return "\n".join(lines)


def _parse_response(raw: str) -> PreprocessorOutput:
    """Parse the LLM JSON response into a PreprocessorOutput.

    :param raw: Raw JSON string from the LLM.
    :return: Parsed PreprocessorOutput.
    :raises ValueError: If the JSON is malformed or missing required fields.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"pre-processor response is not valid JSON: {exc}"
        raise ValueError(msg) from exc

    if "section_mappings" not in data or "new_entity_mentions" not in data:
        msg = (
            "pre-processor response missing required keys:"
            " 'section_mappings' and/or 'new_entity_mentions'"
        )
        raise ValueError(msg)

    dummy_source = SourceMetadata(filename="<preprocessor>")
    section_mappings: list[SectionMapping] = []

    for entry in data["section_mappings"]:
        chunk_index = int(entry["chunk_index"])
        excerpts = [
            WikiExcerpt(
                entity_name=e["entity_name"],
                category=e["category"],
                content=e["excerpt"],
            )
            for e in entry.get("relevant_entities", [])
        ]
        # Placeholder chunk — replaced with real chunk by index after parsing.
        placeholder_chunk = TranscriptChunk(
            text=f"<chunk {chunk_index}>",
            source=dummy_source,
        )
        section_mappings.append(
            SectionMapping(chunk=placeholder_chunk, relevant_wiki_excerpts=excerpts)
        )

    return PreprocessorOutput(
        section_mappings=section_mappings,
        new_entity_mentions=list(data["new_entity_mentions"]),
    )


async def run_preprocessor(
    *,
    client: OpenRouterClient,
    chunks: list[TranscriptChunk],
    wiki_pages: dict[str, str],
    model: str = DEFAULT_PREPROCESSOR_MODEL,
) -> PreprocessorOutput:
    """Run the Stage 1 pre-processor against the transcript chunks.

    Sends the full transcript and all wiki pages to the LLM in a single call,
    asking it to map sections to existing wiki entities and flag new ones.

    :param client: OpenRouterClient to use for the LLM call.
    :param chunks: Parsed transcript chunks to analyse.
    :param wiki_pages: Mapping of wiki page title → markdown content.
    :param model: Model identifier to use for the pre-processor stage.
    :return: PreprocessorOutput with section mappings and new entity mentions.
    :raises ValueError: If the LLM response cannot be parsed as valid JSON.
    """
    user_message = _build_user_message(chunks, wiki_pages)
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
    output = _parse_response(raw)

    # Replace placeholder chunks with the actual input chunks by index.
    for i, mapping in enumerate(output.section_mappings):
        if i < len(chunks):
            output.section_mappings[i] = SectionMapping(
                chunk=chunks[i],
                relevant_wiki_excerpts=mapping.relevant_wiki_excerpts,
            )

    return output
