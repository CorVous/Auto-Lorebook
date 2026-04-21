"""Tests for pipeline.stage1b module."""

from __future__ import annotations

import httpx
import pytest

from auto_lorebook.config import ModelParams
from auto_lorebook.pipeline.stage1b import (
    build_1b_prompt,
    parse_1b_response,
    recentered_locator_hint,
    run_stage_1b,
)
from auto_lorebook.sources.srt import SrtCue, ts_to_seconds


def _cue(start: str, end: str, text: str = "") -> SrtCue:
    return SrtCue(
        index=1,
        start=start,
        end=end,
        start_seconds=ts_to_seconds(start),
        end_seconds=ts_to_seconds(end),
        text=text,
    )


_CUES = [
    _cue("0:00:00", "0:05:00", "First cue"),
    _cue("0:05:00", "0:10:00", "Second cue"),
]

_SEG: dict[str, object] = {
    "id": "seg-001",
    "start": "0:00:00",
    "end": "0:10:00",
    "title": "Test Segment",
    "speaker": None,
}


def test_build_1b_prompt_contains_preamble() -> None:
    prompt = build_1b_prompt("MY_PREAMBLE", _SEG, _CUES)
    assert "MY_PREAMBLE" in prompt


def test_build_1b_prompt_contains_segment_title() -> None:
    prompt = build_1b_prompt("pre", _SEG, _CUES)
    assert "Test Segment" in prompt


def test_build_1b_prompt_contains_transcript_slice() -> None:
    prompt = build_1b_prompt("pre", _SEG, _CUES)
    assert "First cue" in prompt
    assert "Second cue" in prompt


def test_parse_1b_response_extracts_bullets() -> None:
    response = "- Theron declared war. [0:05:32]\n- Kiki objected. [0:06:15]"
    summary = parse_1b_response(response, "seg-001")
    assert len(summary.bullets) == 2
    assert summary.bullets[0].anchor == "0:05:32"
    assert "Theron" in summary.bullets[0].text


def test_parse_1b_response_strips_anchor_from_text() -> None:
    response = "- The king spoke. [0:03:00]"
    summary = parse_1b_response(response, "seg-001")
    assert "[0:03:00]" not in summary.bullets[0].text


def test_parse_1b_response_empty_is_valid() -> None:
    summary = parse_1b_response("", "seg-001")
    assert summary.bullets == []


def test_parse_1b_response_no_anchor_uses_default() -> None:
    response = "- Some claim with no timestamp."
    summary = parse_1b_response(response, "seg-001")
    assert len(summary.bullets) == 1
    assert summary.bullets[0].anchor == "0:00:00"


def test_parse_1b_response_locator_hint_set() -> None:
    response = "- Claim. [0:05:00]"
    summary = parse_1b_response(response, "seg-001", window_seconds=30.0)
    start, end = summary.bullets[0].locator_hint
    assert start == "0:04:45"
    assert end == "0:05:15"


def test_recentered_locator_hint_centered() -> None:
    start, end = recentered_locator_hint("0:05:00", window_seconds=30.0)
    assert start == "0:04:45"
    assert end == "0:05:15"


def test_recentered_locator_hint_clamped_at_zero() -> None:
    start, _ = recentered_locator_hint("0:00:10", window_seconds=30.0)
    assert start == "0:00:00"


class _StaticTransport(httpx.AsyncBaseTransport):
    def __init__(self, content: str) -> None:
        self._content = content

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:  # noqa: ARG002
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": self._content}}]},
        )


@pytest.mark.trio
async def test_run_stage_1b_returns_one_summary_per_segment() -> None:
    structure: dict[str, object] = {
        "segments": [
            {
                "id": "s1",
                "start": "0:00:00",
                "end": "0:05:00",
                "title": "A",
                "speaker": None,
            },
            {
                "id": "s2",
                "start": "0:05:00",
                "end": "0:10:00",
                "title": "B",
                "speaker": None,
            },
        ]
    }
    result = await run_stage_1b(
        structure,
        _CUES,
        "preamble",
        model="m",
        params=ModelParams(),
        api_key="k",
        _transport=_StaticTransport("- Claim. [0:02:00]"),
    )
    assert len(result) == 2
    assert result[0].segment_id == "s1"
    assert result[1].segment_id == "s2"


@pytest.mark.trio
async def test_run_stage_1b_empty_bullets_valid() -> None:
    structure: dict[str, object] = {
        "segments": [
            {
                "id": "s1",
                "start": "0:00:00",
                "end": "0:05:00",
                "title": "A",
                "speaker": None,
            },
        ]
    }
    result = await run_stage_1b(
        structure,
        _CUES,
        "preamble",
        model="m",
        params=ModelParams(),
        api_key="k",
        _transport=_StaticTransport(""),
    )
    assert result[0].bullets == []
