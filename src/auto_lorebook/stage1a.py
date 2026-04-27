"""Stage 1a: segment + attribute transcript in one LLM call.

Emits a `Structure` validated by `structure.validate`.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from auto_lorebook import structure as structure_mod
from auto_lorebook.llm_helpers import build_system_prompt, parse_json_object
from auto_lorebook.structure import Structure, StructureValidationError
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    from auto_lorebook.openrouter import OpenRouterClient
    from auto_lorebook.transcript import LoadedTranscript

_logger = logging.getLogger(__name__)

# When the LLM ends the last segment a few seconds shy of the
# transcript's total duration (cue-boundary rounding, trailing
# music/credits/silence), extend the last segment to cover the gap so
# `structure.validate`'s strict 1s tolerance still catches real
# coverage drops.
_TAIL_CLAMP_THRESHOLD_SECONDS = 30.0

_TASK_INSTRUCTIONS = """\
You are segmenting an actual-play or lore transcript into a structured
reading for a wiki. Read the transcript and emit a single JSON object
matching this schema exactly:

{
  "default_speaker": "<str>",
  "segments": [
    {
      "id": "seg-001",
      "start": "h:mm:ss",
      "end":   "h:mm:ss",
      "title": "<short topic>",
      "speaker": "<speaker name or 'mixed'>",
      "notes": null | "<optional note for low-yield segments>",
      "overrides": [
        {
          "start": "h:mm:ss",
          "end":   "h:mm:ss",
          "speaker": "<who was actually speaking>",
          "voiced_by": null | "<who voiced them>",
          "note":      null | "<note>"
        }
      ]
    }
  ],
  "uncertainty_flags": [
    {
      "locator": "h:mm:ss",
      "span":    "<short quote or description>",
      "kind":    "name" | "attribution" | "other",
      "note":    null | "<note>"
    }
  ]
}

Hard rules:
- Segments MUST cover the transcript contiguously from 0:00:00 to the
  transcript's end. No gaps, no overlaps. If a stretch is silence or
  off-topic, still emit a segment (title: "silence", "break",
  "off-topic rules lookup", etc.) — explicit is better than absent.
- Segment IDs are `seg-NNN`, 1-indexed, in order.
- Timestamps are `h:mm:ss` (variable-width hour, zero-padded m/s).
- Overrides are optional and must fall inside their parent segment.
- Err on the side of flagging uncertainty: a dismissed flag costs
  seconds; a silently-swallowed uncertain name pollutes a downstream
  fact.

Emit ONLY the JSON object. No prose, no code fences, no commentary.
"""


class Stage1aError(RuntimeError):
    """Stage 1a failed: bad JSON, schema violation, or validation failure."""


def run(
    *,
    transcript: LoadedTranscript,
    preamble_text: str,
    source_id: str,
    client: OpenRouterClient,
    model: str,
) -> Structure:
    """Run Stage 1a on a loaded transcript.

    :raises Stage1aError: bad LLM output or mechanical-validation failure
    """
    messages = [
        {
            "role": "system",
            "content": build_system_prompt(preamble_text, _TASK_INSTRUCTIONS),
        },
        {"role": "user", "content": _build_user(transcript)},
    ]

    resp = client.complete(
        messages,
        model=model,
        response_format={"type": "json_object"},
    )

    try:
        payload = parse_json_object(resp.text, "Stage 1a")
    except ValueError as e:
        raise Stage1aError(str(e)) from e
    structure = _payload_to_structure(
        payload,
        source_id=source_id,
        generated_at=format_iso_now(),
    )
    structure = _clamp_tail(structure, transcript.total_duration)

    try:
        structure_mod.validate(structure, transcript.total_duration)
    except StructureValidationError as e:
        msg = f"Stage 1a mechanical validation failed: {e}"
        raise Stage1aError(msg) from e

    return structure


def _clamp_tail(structure: Structure, total_duration: float) -> Structure:
    """Extend last segment to total_duration when the gap is benign.

    Cue-boundary rounding and trailing music/credits routinely leave a
    few seconds of slack; extending is mechanically correct (no claims
    to lose). Gaps beyond the threshold mean the model dropped real
    content and should still fail validate.
    """
    if not structure.segments:
        return structure
    last = structure.segments[-1]
    gap = total_duration - last.end
    if 0 < gap <= _TAIL_CLAMP_THRESHOLD_SECONDS:
        clamped_last = replace(last, end=total_duration)
        new_segments = [*structure.segments[:-1], clamped_last]
        return replace(structure, segments=new_segments)
    return structure


def _build_user(transcript: LoadedTranscript) -> str:
    return (
        f"Transcript (total duration ~{transcript.total_duration:.0f}s):\n\n"
        f"{transcript.text_for_llm}"
    )


def _payload_to_structure(
    payload: dict[str, Any],
    *,
    source_id: str,
    generated_at: str,
) -> Structure:
    segments_raw = payload.get("segments")
    if not isinstance(segments_raw, list) or not segments_raw:
        msg = "Stage 1a response has no segments"
        raise Stage1aError(msg)
    try:
        segments = [structure_mod.parse_segment(s) for s in segments_raw]
    except (KeyError, ValueError, TypeError) as e:
        msg = f"Stage 1a segment schema violation: {e}"
        raise Stage1aError(msg) from e

    flags_raw = payload.get("uncertainty_flags") or []
    try:
        flags = [structure_mod.parse_flag(f) for f in flags_raw]
    except (KeyError, ValueError, TypeError) as e:
        msg = f"Stage 1a uncertainty_flag schema violation: {e}"
        raise Stage1aError(msg) from e

    return Structure(
        source_id=source_id,
        generated_at=generated_at,
        default_speaker=str(payload.get("default_speaker") or ""),
        segments=segments,
        uncertainty_flags=flags,
    )
