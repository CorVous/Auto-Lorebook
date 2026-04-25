"""Tests for stage1a.py — structure generation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from auto_lorebook.openrouter import OpenRouterResponse
from auto_lorebook.stage1a import Stage1aError, run
from auto_lorebook.structure import Structure
from auto_lorebook.transcript import LoadedTranscript


def _mock_client(text: str) -> MagicMock:
    client = MagicMock()
    client.complete.return_value = OpenRouterResponse(
        text=text, model="m/one", tokens_in=10, tokens_out=5
    )
    return client


def _valid_payload() -> str:
    return json.dumps({
        "default_speaker": "DM",
        "segments": [
            {
                "id": "seg-001",
                "start": "0:00:00",
                "end": "0:01:00",
                "title": "Intro",
                "speaker": "DM",
            },
            {
                "id": "seg-002",
                "start": "0:01:00",
                "end": "0:02:00",
                "title": "Body",
                "speaker": "DM",
            },
        ],
        "uncertainty_flags": [{"locator": "0:00:47", "span": "a name", "kind": "name"}],
    })


class TestRun:
    def test_happy_path(self) -> None:
        client = _mock_client(_valid_payload())
        transcript = LoadedTranscript(
            text_for_llm="[0:00:01] hello\n", total_duration=120.0
        )
        result = run(
            transcript=transcript,
            preamble_text="## Setting\n(none)\n",
            source_id="yt-x",
            client=client,
            model="m/one",
        )
        assert isinstance(result, Structure)
        assert result.source_id == "yt-x"
        assert len(result.segments) == 2
        assert result.segments[0].id == "seg-001"
        assert result.default_speaker == "DM"

    def test_sends_preamble_and_transcript(self) -> None:
        client = _mock_client(_valid_payload())
        transcript = LoadedTranscript(
            text_for_llm="[0:00:01] hello world\n", total_duration=120.0
        )
        run(
            transcript=transcript,
            preamble_text="## Setting\n(none)",
            source_id="yt-x",
            client=client,
            model="m/one",
        )
        client.complete.assert_called_once()
        call = client.complete.call_args
        messages = call.args[0]
        kwargs = call.kwargs
        assert kwargs["model"] == "m/one"
        assert kwargs["response_format"] == {"type": "json_object"}
        # system message contains preamble; user message contains transcript
        assert any(
            m["role"] == "system" and "Setting" in m["content"] for m in messages
        )
        assert any(
            m["role"] == "user" and "hello world" in m["content"] for m in messages
        )

    def test_json_wrapped_in_code_fence_tolerated(self) -> None:
        fenced = "```json\n" + _valid_payload() + "\n```\n"
        client = _mock_client(fenced)
        transcript = LoadedTranscript(
            text_for_llm="[0:00:01] hi\n", total_duration=120.0
        )
        result = run(
            transcript=transcript,
            preamble_text="",
            source_id="yt-x",
            client=client,
            model="m/one",
        )
        assert len(result.segments) == 2

    def test_malformed_json_raises(self) -> None:
        client = _mock_client("not valid json at all")
        transcript = LoadedTranscript(
            text_for_llm="[0:00:01] hi\n", total_duration=120.0
        )
        with pytest.raises(Stage1aError, match="JSON"):
            run(
                transcript=transcript,
                preamble_text="",
                source_id="yt-x",
                client=client,
                model="m/one",
            )

    def test_validation_failure_raises(self) -> None:
        # segments don't cover full 120s duration
        bad = json.dumps({
            "default_speaker": "DM",
            "segments": [
                {
                    "id": "seg-001",
                    "start": "0:00:00",
                    "end": "0:00:30",
                    "title": "Short",
                    "speaker": "DM",
                }
            ],
            "uncertainty_flags": [],
        })
        client = _mock_client(bad)
        transcript = LoadedTranscript(
            text_for_llm="[0:00:01] hi\n", total_duration=120.0
        )
        with pytest.raises(Stage1aError):
            run(
                transcript=transcript,
                preamble_text="",
                source_id="yt-x",
                client=client,
                model="m/one",
            )

    def test_missing_segments_raises(self) -> None:
        client = _mock_client('{"segments": []}')
        transcript = LoadedTranscript(
            text_for_llm="[0:00:01] hi\n", total_duration=120.0
        )
        with pytest.raises(Stage1aError):
            run(
                transcript=transcript,
                preamble_text="",
                source_id="yt-x",
                client=client,
                model="m/one",
            )
