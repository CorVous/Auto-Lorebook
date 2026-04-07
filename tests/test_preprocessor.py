"""Tests for the Stage 1 pre-processor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from auto_lorebook.llm.preprocessor import DEFAULT_PREPROCESSOR_MODEL, run_preprocessor
from auto_lorebook.models import PreprocessorOutput, SourceMetadata, TranscriptChunk


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


def _valid_response(
    chunks: list[TranscriptChunk],
    *,
    new_entities: list[str] | None = None,
) -> dict:  # type: ignore[type-arg]
    """Build a valid pre-processor JSON response for the given chunks."""
    mappings = [{"chunk_index": i, "relevant_entities": []} for i in range(len(chunks))]
    return {
        "section_mappings": mappings,
        "new_entity_mentions": new_entities or [],
    }


@pytest.mark.trio
async def test_run_preprocessor_returns_output() -> None:
    """Happy path: returns a PreprocessorOutput from a valid LLM response."""
    chunks = [_make_chunk("The kingdom of Aldara.", 0)]
    client = _make_client(_valid_response(chunks))
    result = await run_preprocessor(client=client, chunks=chunks, wiki_pages={})
    assert isinstance(result, PreprocessorOutput)


@pytest.mark.trio
async def test_run_preprocessor_empty_chunks() -> None:
    """Empty chunk list returns PreprocessorOutput with empty mappings."""
    client = _make_client({"section_mappings": [], "new_entity_mentions": []})
    result = await run_preprocessor(client=client, chunks=[], wiki_pages={})
    assert result.section_mappings == []
    assert result.new_entity_mentions == []


@pytest.mark.trio
async def test_run_preprocessor_all_chunks_in_mappings() -> None:
    """Every input chunk has a corresponding entry in section_mappings."""
    chunks = [_make_chunk("Chunk one.", 0), _make_chunk("Chunk two.", 1)]
    client = _make_client(_valid_response(chunks))
    result = await run_preprocessor(client=client, chunks=chunks, wiki_pages={})
    assert len(result.section_mappings) == len(chunks)


@pytest.mark.trio
async def test_run_preprocessor_new_entity_mentions() -> None:
    """New entity mentions from the LLM response are included in output."""
    chunks = [_make_chunk("The Dragon Vex attacked.", 0)]
    client = _make_client(
        _valid_response(chunks, new_entities=["Dragon Vex", "The North Keep"])
    )
    result = await run_preprocessor(client=client, chunks=chunks, wiki_pages={})
    assert "Dragon Vex" in result.new_entity_mentions
    assert "The North Keep" in result.new_entity_mentions


@pytest.mark.trio
async def test_run_preprocessor_wiki_excerpts_populated() -> None:
    """Relevant wiki excerpts are parsed into SectionMapping objects."""
    chunks = [_make_chunk("Aldara stands tall.", 0)]
    response = {
        "section_mappings": [
            {
                "chunk_index": 0,
                "relevant_entities": [
                    {
                        "entity_name": "Aldara",
                        "category": "locations",
                        "excerpt": "Aldara is a city.",
                    },
                ],
            }
        ],
        "new_entity_mentions": [],
    }
    client = _make_client(response)
    result = await run_preprocessor(
        client=client,
        chunks=chunks,
        wiki_pages={"Aldara": "# Aldara\nAldara is a city."},
    )
    assert len(result.section_mappings[0].relevant_wiki_excerpts) == 1
    exc = result.section_mappings[0].relevant_wiki_excerpts[0]
    assert exc.entity_name == "Aldara"
    assert exc.category == "locations"


@pytest.mark.trio
async def test_run_preprocessor_uses_default_model() -> None:
    """run_preprocessor uses DEFAULT_PREPROCESSOR_MODEL when model is not specified."""
    chunks = [_make_chunk("Some text.", 0)]
    client = _make_client(_valid_response(chunks))
    await run_preprocessor(client=client, chunks=chunks, wiki_pages={})
    client.chat.assert_called_once()
    called_model = client.chat.call_args[0][0]
    assert called_model == DEFAULT_PREPROCESSOR_MODEL


@pytest.mark.trio
async def test_run_preprocessor_respects_custom_model() -> None:
    """run_preprocessor passes the custom model to the client."""
    chunks = [_make_chunk("Some text.", 0)]
    client = _make_client(_valid_response(chunks))
    await run_preprocessor(
        client=client,
        chunks=chunks,
        wiki_pages={},
        model="google/gemini-2.0-flash",
    )
    called_model = client.chat.call_args[0][0]
    assert called_model == "google/gemini-2.0-flash"


@pytest.mark.trio
async def test_run_preprocessor_malformed_json_raises() -> None:
    """Malformed LLM JSON response raises ValueError."""
    chunks = [_make_chunk("Text.", 0)]
    client = AsyncMock()
    client.chat = AsyncMock(return_value="not valid json {{{")
    with pytest.raises(ValueError, match="pre-processor"):
        await run_preprocessor(client=client, chunks=chunks, wiki_pages={})


@pytest.mark.trio
async def test_run_preprocessor_missing_key_raises() -> None:
    """Response JSON missing required keys raises ValueError."""
    chunks = [_make_chunk("Text.", 0)]
    client = _make_client({"section_mappings": []})  # missing new_entity_mentions
    with pytest.raises(ValueError, match="pre-processor"):
        await run_preprocessor(client=client, chunks=chunks, wiki_pages={})
