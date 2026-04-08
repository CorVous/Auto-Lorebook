"""Tests for the Stage 3 writer."""

from __future__ import annotations

import json
from typing import Literal
from unittest.mock import AsyncMock

import pytest

from auto_lorebook.llm.writer import DEFAULT_WRITER_MODEL, run_writer
from auto_lorebook.models import (
    EntityAction,
    PlannerOutput,
    SourceMetadata,
    SourceReference,
    TranscriptChunk,
    WikiPage,
    WriterOutput,
)


def _make_chunk(
    text: str, index: int = 0, *, source_url: str | None = None
) -> TranscriptChunk:
    source = SourceMetadata(filename="test.srt", source_url=source_url)
    return TranscriptChunk(
        text=text,
        source=source,
        start_seconds=float(index),
        end_seconds=float(index + 1),
    )


def _make_client(response_json: dict) -> AsyncMock:  # type: ignore[type-arg]
    """Return a mock OpenRouterClient whose chat() returns the given JSON."""
    client = AsyncMock()
    client.chat = AsyncMock(return_value=json.dumps(response_json))
    return client


def _make_planner_output(
    actions: list[EntityAction] | None = None,
) -> PlannerOutput:
    return PlannerOutput(
        entity_actions=actions or [],
        summary="Planner summary.",
    )


def _valid_response(
    pages: list[dict] | None = None,  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    """Build a valid writer JSON response."""
    return {
        "pages": pages or [],
        "summary": "No pages to write.",
    }


def _page(
    entity_name: str = "Aldara",
    category: str = "locations",
    markdown: str = "# Aldara\n\nA kingdom in the north.\n",
) -> dict:  # type: ignore[type-arg]
    return {
        "entity_name": entity_name,
        "category": category,
        "markdown": markdown,
    }


def _entity_action(
    entity_name: str = "Aldara",
    category: str = "locations",
    action: Literal["create", "update", "merge"] = "create",
) -> EntityAction:
    return EntityAction(
        entity_name=entity_name,
        category=category,
        action=action,
        info_to_add="Kingdom in the Second Age.",
        source_refs=[
            SourceReference(chunk_index=0, quote="Aldara", timestamp_seconds=None),
        ],
        rationale="New entity not found in wiki.",
    )


@pytest.mark.trio
async def test_run_writer_returns_output() -> None:
    """Happy path: returns a WriterOutput from a valid LLM response."""
    chunks = [_make_chunk("The kingdom of Aldara.", 0)]
    planner_output = _make_planner_output([_entity_action()])
    client = _make_client(_valid_response(pages=[_page()]))
    result = await run_writer(
        client=client,
        chunks=chunks,
        planner_output=planner_output,
        wiki_pages={},
    )
    assert isinstance(result, WriterOutput)


@pytest.mark.trio
async def test_run_writer_empty_plan() -> None:
    """Empty plan (no entity_actions) returns WriterOutput with empty pages."""
    planner_output = _make_planner_output()
    client = _make_client(_valid_response())
    result = await run_writer(
        client=client,
        chunks=[],
        planner_output=planner_output,
        wiki_pages={},
    )
    assert result.pages == []
    assert result.summary == "No pages to write."


@pytest.mark.trio
async def test_run_writer_single_create_page() -> None:
    """A single create action produces one WikiPage."""
    chunks = [_make_chunk("Aldara is a great kingdom.", 0)]
    planner_output = _make_planner_output([_entity_action(action="create")])
    client = _make_client(_valid_response(pages=[_page()]))
    result = await run_writer(
        client=client,
        chunks=chunks,
        planner_output=planner_output,
        wiki_pages={},
    )
    assert len(result.pages) == 1
    assert isinstance(result.pages[0], WikiPage)
    assert result.pages[0].entity_name == "Aldara"


@pytest.mark.trio
async def test_run_writer_multiple_pages() -> None:
    """Multiple entity actions produce multiple WikiPage objects."""
    chunks = [
        _make_chunk("Aldara is a kingdom.", 0),
        _make_chunk("King Theron rules.", 1),
    ]
    planner_output = _make_planner_output([
        _entity_action(entity_name="Aldara", category="locations"),
        _entity_action(entity_name="King Theron", category="characters"),
    ])
    client = _make_client(
        _valid_response(
            pages=[
                _page(entity_name="Aldara", category="locations"),
                _page(
                    entity_name="King Theron",
                    category="characters",
                    markdown="# King Theron\n\nA ruler.\n",
                ),
            ]
        )
    )
    result = await run_writer(
        client=client,
        chunks=chunks,
        planner_output=planner_output,
        wiki_pages={},
    )
    assert len(result.pages) == 2


@pytest.mark.trio
async def test_run_writer_page_fields_populated() -> None:
    """WikiPage entity_name, category, and markdown are correctly populated."""
    chunks = [_make_chunk("Aldara stands tall.", 0)]
    planner_output = _make_planner_output([_entity_action()])
    md = (
        "# Aldara\n\nA kingdom founded in the Second Age."
        "\n\n## References\n\n1. test.srt\n"
    )
    client = _make_client(_valid_response(pages=[_page(markdown=md)]))
    result = await run_writer(
        client=client,
        chunks=chunks,
        planner_output=planner_output,
        wiki_pages={},
    )
    page = result.pages[0]
    assert page.entity_name == "Aldara"
    assert page.category == "locations"
    assert page.markdown == md


@pytest.mark.trio
async def test_run_writer_uses_default_model() -> None:
    """run_writer uses DEFAULT_WRITER_MODEL when model is not specified."""
    chunks = [_make_chunk("Some text.", 0)]
    planner_output = _make_planner_output()
    client = _make_client(_valid_response())
    await run_writer(
        client=client,
        chunks=chunks,
        planner_output=planner_output,
        wiki_pages={},
    )
    client.chat.assert_called_once()
    called_model = client.chat.call_args[0][0]
    assert called_model == DEFAULT_WRITER_MODEL


@pytest.mark.trio
async def test_run_writer_respects_custom_model() -> None:
    """run_writer passes the custom model to the client."""
    chunks = [_make_chunk("Some text.", 0)]
    planner_output = _make_planner_output()
    client = _make_client(_valid_response())
    await run_writer(
        client=client,
        chunks=chunks,
        planner_output=planner_output,
        wiki_pages={},
        model="google/gemini-2.0-flash",
    )
    called_model = client.chat.call_args[0][0]
    assert called_model == "google/gemini-2.0-flash"


@pytest.mark.trio
async def test_run_writer_malformed_json_raises() -> None:
    """Malformed LLM JSON response raises ValueError."""
    chunks = [_make_chunk("Text.", 0)]
    planner_output = _make_planner_output()
    client = AsyncMock()
    client.chat = AsyncMock(return_value="not valid json {{{")
    with pytest.raises(ValueError, match="writer"):
        await run_writer(
            client=client,
            chunks=chunks,
            planner_output=planner_output,
            wiki_pages={},
        )


@pytest.mark.trio
async def test_run_writer_missing_key_raises() -> None:
    """Response JSON missing required keys raises ValueError."""
    chunks = [_make_chunk("Text.", 0)]
    planner_output = _make_planner_output()
    client = _make_client({"pages": []})  # missing summary
    with pytest.raises(ValueError, match="writer"):
        await run_writer(
            client=client,
            chunks=chunks,
            planner_output=planner_output,
            wiki_pages={},
        )


@pytest.mark.trio
async def test_run_writer_summary_populated() -> None:
    """WriterOutput.summary matches the summary from the LLM response."""
    chunks = [_make_chunk("Aldara.", 0)]
    planner_output = _make_planner_output([_entity_action()])
    client = _make_client({"pages": [_page()], "summary": "Created Aldara page."})
    result = await run_writer(
        client=client,
        chunks=chunks,
        planner_output=planner_output,
        wiki_pages={},
    )
    assert result.summary == "Created Aldara page."


@pytest.mark.trio
async def test_run_writer_missing_page_field_raises() -> None:
    """Missing required field inside a page entry raises KeyError."""
    chunks = [_make_chunk("Text.", 0)]
    planner_output = _make_planner_output()
    # Missing "entity_name" key in page
    bad_page = {"category": "locations", "markdown": "# Foo\n"}
    client = _make_client({"pages": [bad_page], "summary": "Summary."})
    with pytest.raises(KeyError):
        await run_writer(
            client=client,
            chunks=chunks,
            planner_output=planner_output,
            wiki_pages={},
        )
