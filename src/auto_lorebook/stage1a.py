"""Stage 1a: segment + attribute transcript in one LLM call.

Emits a `Structure` validated by `structure.validate`.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from auto_lorebook import structure as structure_mod
from auto_lorebook.structure import Structure, StructureValidationError

if TYPE_CHECKING:
    from auto_lorebook.openrouter import OpenRouterClient
    from auto_lorebook.transcript import LoadedTranscript

_logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(?P<body>.*?)\n```\s*$", re.DOTALL)

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
    system_content = _build_system(preamble_text)
    user_content = _build_user(transcript)
    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

    resp = client.complete(
        messages,
        model=model,
        response_format={"type": "json_object"},
    )

    payload = _parse_json(resp.text)
    structure = _payload_to_structure(
        payload,
        source_id=source_id,
        generated_at=_now_iso(),
    )

    try:
        structure_mod.validate(structure, transcript.total_duration)
    except StructureValidationError as e:
        msg = f"Stage 1a mechanical validation failed: {e}"
        raise Stage1aError(msg) from e

    return structure


def _build_system(preamble_text: str) -> str:
    if preamble_text.strip():
        return f"{preamble_text}\n\n---\n\n{_TASK_INSTRUCTIONS}"
    return _TASK_INSTRUCTIONS


def _build_user(transcript: LoadedTranscript) -> str:
    return (
        f"Transcript (total duration ~{transcript.total_duration:.0f}s):\n\n"
        f"{transcript.text_for_llm}"
    )


def _parse_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    m = _CODE_FENCE_RE.match(raw)
    if m:
        raw = m.group("body").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        msg = f"Stage 1a response was not valid JSON: {e}"
        raise Stage1aError(msg) from e
    if not isinstance(parsed, dict):
        msg = f"Stage 1a response must be a JSON object, got {type(parsed).__name__}"
        raise Stage1aError(msg)
    return parsed


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
        segments = [structure_mod._parse_segment(s) for s in segments_raw]  # noqa: SLF001
    except (KeyError, ValueError, TypeError) as e:
        msg = f"Stage 1a segment schema violation: {e}"
        raise Stage1aError(msg) from e

    flags_raw = payload.get("uncertainty_flags") or []
    try:
        flags = [structure_mod._parse_flag(f) for f in flags_raw]  # noqa: SLF001
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


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
