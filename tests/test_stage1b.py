"""Tests for stage1b.py — per-segment summarize."""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from auto_lorebook.openrouter import OpenRouterResponse
from auto_lorebook.stage1b import (
    AcceptedContextEntry,
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
        # anchor is in seg-001 range but returned for seg-002; far outside tolerance
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

    def test_anchor_slightly_past_end_clamps_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # anchor 1.5s past segment.end (within default tolerance 2.0) — clamps, no raise
        s = Structure(
            source_id="yt-x",
            generated_at="2026-04-20T00:00:00Z",
            default_speaker="DM",
            segments=[
                Segment(id="seg-002", start=7.0, end=30.0, title="x", speaker="DM"),
            ],
        )
        # 30 + 1.5 = 31.5s → "0:00:31" is the closest h:mm:ss representation
        client = MagicMock()
        client.complete.return_value = OpenRouterResponse(
            text=_bullets_payload([{"text": "claim", "anchor": "0:00:31"}]),
            model="m",
            tokens_in=0,
            tokens_out=0,
        )
        transcript = LoadedTranscript(text_for_llm="plain text", total_duration=60.0)
        with caplog.at_level(logging.WARNING, logger="auto_lorebook.stage1b"):
            result = run(
                transcript=transcript,
                structure=s,
                preamble_text="",
                client=client,
                model="m/one",
            )
        bullets = result.segments["seg-002"]
        assert len(bullets) == 1
        assert bullets[0].anchor == pytest.approx(30.0)  # clamped to segment.end
        assert any("clamp" in r.message.lower() for r in caplog.records)

    def test_anchor_slightly_before_start_clamps_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # anchor 1.0s before segment.start — clamps to start, no raise
        s = Structure(
            source_id="yt-x",
            generated_at="2026-04-20T00:00:00Z",
            default_speaker="DM",
            segments=[
                Segment(id="seg-002", start=10.0, end=40.0, title="x", speaker="DM"),
            ],
        )
        client = MagicMock()
        client.complete.return_value = OpenRouterResponse(
            text=_bullets_payload([{"text": "claim", "anchor": "0:00:09"}]),
            model="m",
            tokens_in=0,
            tokens_out=0,
        )
        transcript = LoadedTranscript(text_for_llm="plain text", total_duration=60.0)
        with caplog.at_level(logging.WARNING, logger="auto_lorebook.stage1b"):
            result = run(
                transcript=transcript,
                structure=s,
                preamble_text="",
                client=client,
                model="m/one",
            )
        bullets = result.segments["seg-002"]
        assert len(bullets) == 1
        assert bullets[0].anchor == pytest.approx(10.0)  # clamped to segment.start
        assert any("clamp" in r.message.lower() for r in caplog.records)

    def test_clamped_locator_hints_stay_in_segment(self) -> None:
        # after clamping, hints stay within segment bounds
        s = Structure(
            source_id="yt-x",
            generated_at="2026-04-20T00:00:00Z",
            default_speaker="DM",
            segments=[
                Segment(id="seg-002", start=7.0, end=30.0, title="x", speaker="DM"),
            ],
        )
        client = MagicMock()
        client.complete.return_value = OpenRouterResponse(
            text=_bullets_payload([{"text": "claim", "anchor": "0:00:31"}]),
            model="m",
            tokens_in=0,
            tokens_out=0,
        )
        transcript = LoadedTranscript(text_for_llm="plain text", total_duration=60.0)
        result = run(
            transcript=transcript,
            structure=s,
            preamble_text="",
            client=client,
            model="m/one",
        )
        b = result.segments["seg-002"][0]
        assert b.locator_hint_start >= 7.0
        assert b.locator_hint_end <= 30.0

    def test_anchor_beyond_tolerance_raises(self) -> None:
        # anchor 4.0s past segment.end with default tolerance 2.0 — must raise
        s = Structure(
            source_id="yt-x",
            generated_at="2026-04-20T00:00:00Z",
            default_speaker="DM",
            segments=[
                Segment(id="seg-002", start=7.0, end=30.0, title="x", speaker="DM"),
            ],
        )
        client = MagicMock()
        client.complete.return_value = OpenRouterResponse(
            text=_bullets_payload([{"text": "claim", "anchor": "0:00:34"}]),
            model="m",
            tokens_in=0,
            tokens_out=0,
        )
        transcript = LoadedTranscript(text_for_llm="plain text", total_duration=60.0)
        with pytest.raises(Stage1bError, match="anchor"):
            run(
                transcript=transcript,
                structure=s,
                preamble_text="",
                client=client,
                model="m/one",
            )

    def test_custom_anchor_tolerance_plumbs_through(self) -> None:
        # custom tolerance of 5.0s allows an anchor 4.0s past segment.end
        s = Structure(
            source_id="yt-x",
            generated_at="2026-04-20T00:00:00Z",
            default_speaker="DM",
            segments=[
                Segment(id="seg-002", start=7.0, end=30.0, title="x", speaker="DM"),
            ],
        )
        client = MagicMock()
        client.complete.return_value = OpenRouterResponse(
            text=_bullets_payload([{"text": "claim", "anchor": "0:00:34"}]),
            model="m",
            tokens_in=0,
            tokens_out=0,
        )
        transcript = LoadedTranscript(text_for_llm="plain text", total_duration=60.0)
        result = run(
            transcript=transcript,
            structure=s,
            preamble_text="",
            client=client,
            model="m/one",
            anchor_tolerance_seconds=5.0,
        )
        b = result.segments["seg-002"][0]
        assert b.anchor == pytest.approx(30.0)  # clamped to end


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


class TestAcceptedContext:
    """accepted_context kwarg on run() / _build_user."""

    def _client_routing(
        self,
        per_segment: dict[str, list[dict[str, object]]],
        captured: dict[str, str],
    ) -> MagicMock:
        """Capture user message per segment; return canned bullets."""
        client = MagicMock()
        lock = threading.Lock()

        def side_effect(
            messages: list[dict[str, str]], **_kwargs: object
        ) -> OpenRouterResponse:
            user = next(m for m in messages if m["role"] == "user")
            for seg_id, bullets in per_segment.items():
                if seg_id in user["content"]:
                    with lock:
                        captured[seg_id] = user["content"]
                        return OpenRouterResponse(
                            text=_bullets_payload(bullets),
                            model="m",
                            tokens_in=0,
                            tokens_out=0,
                        )
            msg = f"no canned response: {user['content'][:80]}"
            raise AssertionError(msg)

        client.complete.side_effect = side_effect
        return client

    def test_accepted_context_block_in_user_message(self) -> None:
        captured: dict[str, str] = {}
        ctx = [
            AcceptedContextEntry(
                segment_id="seg-001",
                start=0.0,
                end=135.0,
                title="Introduction",
                speaker="DM",
                bullets_body="- Intro bullet\n",
            ),
            AcceptedContextEntry(
                segment_id="seg-003",
                start=270.0,
                end=480.0,
                title="Founding of Aldara",
                speaker="DM",
                bullets_body="- King Theron founded Aldara\n",
            ),
        ]
        client = self._client_routing(
            {"seg-002": [{"text": "x", "anchor": "0:02:30"}]}, captured
        )
        s = Structure(
            source_id="yt-x",
            generated_at="2026-04-20T00:00:00Z",
            default_speaker="DM",
            segments=[
                Segment(
                    id="seg-002",
                    start=120.0,
                    end=270.0,
                    title="Rules",
                    speaker="mixed",
                ),
            ],
        )
        transcript = LoadedTranscript(
            text_for_llm="[0:02:30] some rules text\n", total_duration=600.0
        )
        run(
            transcript=transcript,
            structure=s,
            preamble_text="",
            client=client,
            model="m",
            accepted_context=ctx,
        )
        msg = captured["seg-002"]
        # accepted-context block present
        assert "Accepted segments (context only" in msg
        # both segment headers present
        assert "## seg-001 [0:00:00–0:02:15] Introduction (DM)" in msg  # noqa: RUF001
        assert "## seg-003 [0:04:30–0:08:00] Founding of Aldara (DM)" in msg  # noqa: RUF001
        # bullets verbatim
        assert "- Intro bullet" in msg
        assert "- King Theron founded Aldara" in msg
        # separator present
        assert "---" in msg
        # target segment transcript still there
        assert "Transcript for this segment:" in msg
        assert "some rules text" in msg
        # target segment header present
        assert "Segment seg-002" in msg

    def test_no_accepted_context_block_when_param_omitted(self) -> None:
        captured: dict[str, str] = {}
        client = self._client_routing({"seg-001": []}, captured)
        s = Structure(
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
            ],
        )
        transcript = LoadedTranscript(text_for_llm="plain text", total_duration=120.0)
        run(
            transcript=transcript,
            structure=s,
            preamble_text="",
            client=client,
            model="m",
        )
        msg = captured["seg-001"]
        assert "Accepted segments" not in msg

    def test_accepted_context_with_empty_list_omits_block(self) -> None:
        captured: dict[str, str] = {}
        client = self._client_routing({"seg-001": []}, captured)
        s = Structure(
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
            ],
        )
        transcript = LoadedTranscript(text_for_llm="plain text", total_duration=120.0)
        run(
            transcript=transcript,
            structure=s,
            preamble_text="",
            client=client,
            model="m",
            accepted_context=[],
        )
        msg = captured["seg-001"]
        assert "Accepted segments" not in msg
