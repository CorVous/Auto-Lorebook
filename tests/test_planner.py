"""Tests for the Stage 2 planner."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from auto_lorebook.llm.planner import DEFAULT_PLANNER_MODEL, run_planner
from auto_lorebook.models import (
    EntityAction,
    PlannerOutput,
    PreprocessorOutput,
    SectionMapping,
    SourceMetadata,
    TranscriptChunk,
)


def _make_chunk(text: str, index: int = 0) -> TranscriptChunk:
    source = SourceMetadata(filename="test.srt")
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


def _make_preprocessor_output(
    chunks: list[TranscriptChunk],
    new_entities: list[str] | None = None,
) -> PreprocessorOutput:
    mappings = [SectionMapping(chunk=c) for c in chunks]
    return PreprocessorOutput(
        section_mappings=mappings,
        new_entity_mentions=new_entities or [],
    )


def _valid_response(
    actions: list[dict] | None = None,  # type: ignore[type-arg]
) -> dict:  # type: ignore[type-arg]
    """Build a valid planner JSON response."""
    return {
        "entity_actions": actions or [],
        "summary": "No actions required.",
    }


def _action(
    entity_name: str = "Aldara",
    category: str = "locations",
    action: str = "create",
    info_to_add: str = "Kingdom in the Second Age.",
    source_refs: list[dict] | None = None,  # type: ignore[type-arg]
    rationale: str = "New entity not found in wiki.",
) -> dict:  # type: ignore[type-arg]
    return {
        "entity_name": entity_name,
        "category": category,
        "action": action,
        "info_to_add": info_to_add,
        "source_refs": source_refs
        or [{"chunk_index": 0, "quote": "Aldara", "timestamp_seconds": None}],
        "rationale": rationale,
    }


@pytest.mark.trio
async def test_run_planner_returns_output() -> None:
    """Happy path: returns a PlannerOutput from a valid LLM response."""
    chunks = [_make_chunk("The kingdom of Aldara.", 0)]
    preprocessor_output = _make_preprocessor_output(chunks, new_entities=["Aldara"])
    client = _make_client(_valid_response())
    result = await run_planner(
        client=client,
        preprocessor_output=preprocessor_output,
        chunks=chunks,
        wiki_pages={},
    )
    assert isinstance(result, PlannerOutput)


@pytest.mark.trio
async def test_run_planner_empty_input() -> None:
    """Empty chunks and no new entities → PlannerOutput with empty entity_actions."""
    preprocessor_output = _make_preprocessor_output([])
    client = _make_client(_valid_response())
    result = await run_planner(
        client=client,
        preprocessor_output=preprocessor_output,
        chunks=[],
        wiki_pages={},
    )
    assert result.entity_actions == []
    assert result.summary == "No actions required."


@pytest.mark.trio
async def test_run_planner_create_action() -> None:
    """A create action is parsed correctly into an EntityAction."""
    chunks = [_make_chunk("Aldara is a great kingdom.", 0)]
    preprocessor_output = _make_preprocessor_output(chunks, new_entities=["Aldara"])
    client = _make_client(_valid_response(actions=[_action(action="create")]))
    result = await run_planner(
        client=client,
        preprocessor_output=preprocessor_output,
        chunks=chunks,
        wiki_pages={},
    )
    assert len(result.entity_actions) == 1
    act = result.entity_actions[0]
    assert isinstance(act, EntityAction)
    assert act.entity_name == "Aldara"
    assert act.action == "create"
    assert act.category == "locations"


@pytest.mark.trio
async def test_run_planner_update_action() -> None:
    """An update action is parsed correctly into an EntityAction."""
    chunks = [_make_chunk("King Theron rules Aldara.", 0)]
    preprocessor_output = _make_preprocessor_output(chunks)
    client = _make_client(
        _valid_response(
            actions=[
                _action(
                    entity_name="King Theron",
                    category="characters",
                    action="update",
                    info_to_add="Rules Aldara.",
                )
            ]
        )
    )
    result = await run_planner(
        client=client,
        preprocessor_output=preprocessor_output,
        chunks=chunks,
        wiki_pages={"King Theron": "# King Theron\nA ruler."},
    )
    assert result.entity_actions[0].action == "update"
    assert result.entity_actions[0].entity_name == "King Theron"


@pytest.mark.trio
async def test_run_planner_source_refs_populated() -> None:
    """source_refs on EntityAction are populated with chunk_index and quote."""
    chunks = [_make_chunk("The Dragon Vex attacked Aldara.", 0)]
    preprocessor_output = _make_preprocessor_output(chunks, new_entities=["Dragon Vex"])
    client = _make_client(
        _valid_response(
            actions=[
                _action(
                    entity_name="Dragon Vex",
                    category="characters",
                    action="create",
                    source_refs=[
                        {
                            "chunk_index": 0,
                            "quote": "Dragon Vex attacked",
                            "timestamp_seconds": 0.0,
                        }
                    ],
                )
            ]
        )
    )
    result = await run_planner(
        client=client,
        preprocessor_output=preprocessor_output,
        chunks=chunks,
        wiki_pages={},
    )
    refs = result.entity_actions[0].source_refs
    assert len(refs) == 1
    assert refs[0].chunk_index == 0
    assert refs[0].quote == "Dragon Vex attacked"
    assert refs[0].timestamp_seconds is not None
    assert abs(refs[0].timestamp_seconds - 0.0) < 1e-9


@pytest.mark.trio
async def test_run_planner_uses_default_model() -> None:
    """run_planner uses DEFAULT_PLANNER_MODEL when model is not specified."""
    chunks = [_make_chunk("Some text.", 0)]
    preprocessor_output = _make_preprocessor_output(chunks)
    client = _make_client(_valid_response())
    await run_planner(
        client=client,
        preprocessor_output=preprocessor_output,
        chunks=chunks,
        wiki_pages={},
    )
    client.chat.assert_called_once()
    called_model = client.chat.call_args[0][0]
    assert called_model == DEFAULT_PLANNER_MODEL


@pytest.mark.trio
async def test_run_planner_respects_custom_model() -> None:
    """run_planner passes the custom model to the client."""
    chunks = [_make_chunk("Some text.", 0)]
    preprocessor_output = _make_preprocessor_output(chunks)
    client = _make_client(_valid_response())
    await run_planner(
        client=client,
        preprocessor_output=preprocessor_output,
        chunks=chunks,
        wiki_pages={},
        model="google/gemini-2.0-flash",
    )
    called_model = client.chat.call_args[0][0]
    assert called_model == "google/gemini-2.0-flash"


@pytest.mark.trio
async def test_run_planner_malformed_json_raises() -> None:
    """Malformed LLM JSON response raises ValueError."""
    chunks = [_make_chunk("Text.", 0)]
    preprocessor_output = _make_preprocessor_output(chunks)
    client = AsyncMock()
    client.chat = AsyncMock(return_value="not valid json {{{")
    with pytest.raises(ValueError, match="planner"):
        await run_planner(
            client=client,
            preprocessor_output=preprocessor_output,
            chunks=chunks,
            wiki_pages={},
        )


@pytest.mark.trio
async def test_run_planner_missing_key_raises() -> None:
    """Response JSON missing required keys raises ValueError."""
    chunks = [_make_chunk("Text.", 0)]
    preprocessor_output = _make_preprocessor_output(chunks)
    client = _make_client({"entity_actions": []})  # missing summary
    with pytest.raises(ValueError, match="planner"):
        await run_planner(
            client=client,
            preprocessor_output=preprocessor_output,
            chunks=chunks,
            wiki_pages={},
        )
