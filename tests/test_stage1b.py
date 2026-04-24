"""Tests for stage1b.py — per-segment summarize."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from auto_lorebook.openrouter import OpenRouterResponse
from auto_lorebook.stage1b import (
    Bullet,
    ReadingBullets,
    Stage1bError,
    read_bullets,
    run,
    slice_transcript_for_segment,
    write_bullets,
)
from auto_lorebook.structure import Segment, Structure
from auto_lorebook.transcript import LoadedTranscript

if TYPE_CHECKING:
    from pathlib import Path

_TRANSCRIPT = (
    "[0:00:00] Welcome to the session.\n"
    "[0:00:30] Today we'll discuss Aldara.\n"
    "[0:02:00] King Theron founded Aldara.\n"
    "[0:03:30] It was in the Second Age.\n"
    "[0:05:00] The War of the Dusk followed.\n"
)


def _struct() -> Structure:
    return Structure(
        source_id="yt-x",
        generated_at="2026-04-20T00:00:00Z",
        default_speaker="DM",
        segments=[
            Segment(
                id="seg-001",
                start=0.0,
                end=120.0,
                title="Intro",
                speaker="DM",
            ),
            Segment(
                id="seg-002",
                start=120.0,
                end=300.0,
                title="Founding of Aldara",
                speaker="DM",
            ),
            Segment(
                id="seg-003",
                start=300.0,
                end=600.0,
                title="War of the Dusk",
                speaker="DM",
            ),
        ],
    )


def _bullets_payload(bullets: list[dict[str, object]]) -> str:
    return json.dumps({"bullets": bullets})


class TestSliceTranscript:
    def test_slices_by_time(self) -> None:
        transcript = LoadedTranscript(text_for_llm=_TRANSCRIPT, total_duration=600.0)
        seg = Segment(
            id="seg-002",
            start=120.0,
            end=300.0,
            title="Founding",
            speaker="DM",
        )
        sliced = slice_transcript_for_segment(transcript, seg)
        assert "King Theron" in sliced
        assert "Welcome" not in sliced
        assert "War of the Dusk" not in sliced

    def test_empty_slice_for_out_of_range_segment(self) -> None:
        transcript = LoadedTranscript(text_for_llm="[0:00:01] a\n", total_duration=10.0)
        seg = Segment(
            id="seg-001",
            start=100.0,
            end=200.0,
            title="x",
            speaker="DM",
        )
        sliced = slice_transcript_for_segment(transcript, seg)
        assert not sliced

    def test_text_without_brackets_returned_in_full(self) -> None:
        # plain-text sources have no [h:mm:ss] markers; whole text used
        transcript = LoadedTranscript(
            text_for_llm="Line one. Line two.", total_duration=100.0
        )
        seg = Segment(
            id="seg-001",
            start=0.0,
            end=50.0,
            title="x",
            speaker="DM",
        )
        sliced = slice_transcript_for_segment(transcript, seg)
        assert sliced == "Line one. Line two."


class TestRun:
    def _client_per_segment(
        self, per_segment: dict[str, list[dict[str, object]]]
    ) -> MagicMock:
        """Route responses by looking up the segment id in the user message."""
        client = MagicMock()
        lock = threading.Lock()

        def side_effect(
            messages: list[dict[str, str]], **_kwargs: object
        ) -> OpenRouterResponse:
            user = next((m for m in messages if m["role"] == "user"), None)
            assert user is not None
            for seg_id, bullets in per_segment.items():
                if seg_id in user["content"]:
                    with lock:
                        return OpenRouterResponse(
                            text=_bullets_payload(bullets),
                            model="m",
                            tokens_in=0,
                            tokens_out=0,
                        )
            msg = f"no canned response for message: {user['content'][:100]}"
            raise AssertionError(msg)

        client.complete.side_effect = side_effect
        return client

    def test_all_segments_get_bullets(self) -> None:
        client = self._client_per_segment({
            "seg-001": [{"text": "Intro note", "anchor": "0:00:05"}],
            "seg-002": [
                {"text": "King Theron founded Aldara", "anchor": "0:02:00"},
                {"text": "Second Age", "anchor": "0:03:30"},
            ],
            "seg-003": [],
        })
        transcript = LoadedTranscript(text_for_llm=_TRANSCRIPT, total_duration=600.0)
        result = run(
            transcript=transcript,
            structure=_struct(),
            preamble_text="",
            client=client,
            model="m/one",
        )
        assert isinstance(result, ReadingBullets)
        assert len(result.segments) == 3
        assert {b.text for b in result.segments["seg-002"]} == {
            "King Theron founded Aldara",
            "Second Age",
        }
        assert result.segments["seg-003"] == []
        assert client.complete.call_count == 3

    def test_empty_bullet_list_permitted(self) -> None:
        client = self._client_per_segment({
            "seg-001": [],
            "seg-002": [],
            "seg-003": [],
        })
        transcript = LoadedTranscript(text_for_llm=_TRANSCRIPT, total_duration=600.0)
        result = run(
            transcript=transcript,
            structure=_struct(),
            preamble_text="",
            client=client,
            model="m/one",
        )
        for bullets in result.segments.values():
            assert bullets == []

    def test_locator_hint_pads_anchor(self) -> None:
        client = self._client_per_segment({
            "seg-001": [{"text": "x", "anchor": "0:00:30"}],
            "seg-002": [{"text": "y", "anchor": "0:02:30"}],
            "seg-003": [{"text": "z", "anchor": "0:05:30"}],
        })
        transcript = LoadedTranscript(text_for_llm=_TRANSCRIPT, total_duration=600.0)
        result = run(
            transcript=transcript,
            structure=_struct(),
            preamble_text="",
            client=client,
            model="m/one",
            hint_window_seconds=15.0,
        )
        b = result.segments["seg-001"][0]
        assert b.anchor == pytest.approx(30.0)
        assert b.locator_hint_start == pytest.approx(15.0)
        assert b.locator_hint_end == pytest.approx(45.0)

    def test_locator_hint_clamps_to_segment(self) -> None:
        client = self._client_per_segment({
            # anchor at segment start, hint should clamp to start
            "seg-001": [{"text": "x", "anchor": "0:00:00"}],
            # anchor near segment end, hint clamps to end
            "seg-002": [{"text": "y", "anchor": "0:04:59"}],
            "seg-003": [],
        })
        transcript = LoadedTranscript(text_for_llm=_TRANSCRIPT, total_duration=600.0)
        result = run(
            transcript=transcript,
            structure=_struct(),
            preamble_text="",
            client=client,
            model="m/one",
            hint_window_seconds=30.0,
        )
        b1 = result.segments["seg-001"][0]
        assert b1.locator_hint_start == pytest.approx(0.0)
        b2 = result.segments["seg-002"][0]
        assert b2.locator_hint_end == pytest.approx(300.0)

    def test_malformed_json_raises(self) -> None:
        client = MagicMock()
        client.complete.return_value = OpenRouterResponse(
            text="not json", model="m", tokens_in=0, tokens_out=0
        )
        transcript = LoadedTranscript(text_for_llm=_TRANSCRIPT, total_duration=600.0)
        with pytest.raises(Stage1bError, match="JSON"):
            run(
                transcript=transcript,
                structure=_struct(),
                preamble_text="",
                client=client,
                model="m/one",
            )

    def test_bullet_anchor_outside_segment_raises(self) -> None:
        # anchor is in seg-001 range but returned for seg-002
        client = MagicMock()
        client.complete.return_value = OpenRouterResponse(
            text=_bullets_payload([{"text": "x", "anchor": "0:00:10"}]),
            model="m",
            tokens_in=0,
            tokens_out=0,
        )
        s = Structure(
            source_id="yt-x",
            generated_at="2026-04-20T00:00:00Z",
            default_speaker="DM",
            segments=[
                Segment(
                    id="seg-002",
                    start=120.0,
                    end=300.0,
                    title="x",
                    speaker="DM",
                ),
            ],
        )
        transcript = LoadedTranscript(
            text_for_llm="[0:02:30] x\n", total_duration=300.0
        )
        with pytest.raises(Stage1bError, match="anchor"):
            run(
                transcript=transcript,
                structure=s,
                preamble_text="",
                client=client,
                model="m/one",
            )


class TestBullet:
    def test_bullet_dataclass_fields(self) -> None:
        b = Bullet(
            text="hello",
            anchor=30.0,
            locator_hint_start=15.0,
            locator_hint_end=45.0,
        )
        assert b.text == "hello"
        assert b.anchor == pytest.approx(30.0)


class TestBulletsIO:
    def test_round_trip(self, tmp_path: Path) -> None:
        bullets = ReadingBullets(
            source_id="yt-x",
            generated_at="2026-04-20T00:00:00Z",
            segments={
                "seg-001": [
                    Bullet(
                        text="Hello Aldara",
                        anchor=30.0,
                        locator_hint_start=15.0,
                        locator_hint_end=45.0,
                    )
                ],
                "seg-002": [],
            },
        )
        path = tmp_path / "bullets.yaml"
        write_bullets(bullets, path)
        loaded = read_bullets(path)
        assert loaded.source_id == "yt-x"
        assert len(loaded.segments["seg-001"]) == 1
        assert loaded.segments["seg-001"][0].text == "Hello Aldara"
        assert loaded.segments["seg-002"] == []
