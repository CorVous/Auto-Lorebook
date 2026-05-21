"""Stage 1b: per-segment claim extraction, parallelized across segments.

Takes Stage 1a's Structure plus the loaded transcript; produces a
`ReadingBullets` artifact keyed by segment id. Each bullet carries an
anchor timestamp and a `locator_hint` window that flows into the
planner/extractor downstream.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text
from auto_lorebook.llm_helpers import build_system_prompt, parse_json_object
from auto_lorebook.schema import SchemaVersionError, read_schema_version
from auto_lorebook.timestamps import (
    TimestampError,
    format_iso_now,
    format_timestamp,
    parse_timestamp,
)

if TYPE_CHECKING:
    from pathlib import Path

    from auto_lorebook.openrouter import OpenRouterClient
    from auto_lorebook.structure import Segment, Structure
    from auto_lorebook.transcript import LoadedTranscript

_logger = logging.getLogger(__name__)

DEFAULT_HINT_WINDOW_SECONDS = 15.0


@dataclass(frozen=True)
class AcceptedContextEntry:
    """Accepted segment snapshot injected into regen user messages."""

    segment_id: str
    start: float
    end: float
    title: str
    speaker: str
    bullets_body: str  # verbatim body from seg-NNN.md


DEFAULT_ANCHOR_TOLERANCE_SECONDS = 2.0
DEFAULT_MAX_CONCURRENCY = 4

_TIMESTAMP_LINE_RE = re.compile(r"^\[(?P<ts>[0-9:,.]+)\]\s?(?P<body>.*)$")

_TASK_INSTRUCTIONS = """\
You are extracting FACTS from ONE segment of an actual-play / lore
transcript. A fact is a piece of standing knowledge about the SETTING —
its history, geography, peoples, factions, religions, powers, and
notable items — or about a CHARACTER who inhabits it: who they are,
where they come from, and how they are tied to the world. Emit a
single JSON object:

{
  "bullets": [
    {
      "text":   "<one short, self-contained fact about the world>",
      "anchor": "h:mm:ss"
    }
  ]
}

A fact IS:
- Standing setting knowledge asserted by anyone — DM narration, an NPC,
  or a player's character ("Aldara was founded in the Second Age").
- In-world history, including events from the world's past ("The War of
  the Dusk burned the elven city of Vethran").
- Who a character is: their origin, lineage, role, allegiances, and
  relationships. This includes the players' own characters — a PC's
  backstory, stated in fiction, is setting knowledge ("Sister Aldwin
  was orphaned by the War and raised in the Highmoor temple").
- A claim a character makes about the world, even if unreliable — an
  NPC's rumour still reveals what the setting believes.
- The durable RESULT when play changes the world's state — not the
  action behind it. If the party kills the king, the fact is "King
  Theron is dead", never "a player threw a dagger and rolled 23".

A fact is NOT the session's own play-narrative. Emit NOTHING for:
- The blow-by-blow of play: attacks, damage, dice, initiative, hit
  points, spells, conditions.
- What the party does moment to moment: where they go, what they
  search, who they speak to, how they travel, and what they find,
  collect, loot, or are given.
- The party's possessions and quest rewards, and what the party did in
  earlier sessions.
- Scene blocking and one-off detail: a door giving way, a guard rushing
  in, the weather of a single scene.
- Out-of-character talk: rules lookups, scheduling, snacks, table chat.

Hard rules:
- `bullets` MAY be empty, and often SHOULD be. A combat, travel, rules,
  or break segment typically yields no facts — emit `{"bullets": []}`.
- `anchor` is a plain `h:mm:ss` timestamp INSIDE the segment. Use the
  timestamps visible in the transcript lines.
- One fact per bullet; short and self-contained. Resolve pronouns to
  names ("the king" → "King Theron"); resolve first-person in-character
  speech to the speaking character ("I was orphaned" → "Sister Aldwin
  was orphaned"). Never generalise a fact so it loses its subject.
- When genuinely unsure whether something is durable setting knowledge,
  include it — a spurious fact costs the human seconds to reject. This
  licence covers borderline LORE only; it never licenses play-narrative.

Emit ONLY the JSON object. No prose, no code fences, no commentary.
"""


class Stage1bError(RuntimeError):
    """Stage 1b failed on at least one segment."""


@dataclass(frozen=True)
class Bullet:
    """One extracted claim with anchor and locator-hint window."""

    text: str
    anchor: float
    locator_hint_start: float
    locator_hint_end: float


@dataclass
class ReadingBullets:
    """Stage 1b output: bullets keyed by segment id."""

    source_id: str
    generated_at: str
    segments: dict[str, list[Bullet]] = field(default_factory=dict)


def slice_transcript_for_segment(
    transcript: LoadedTranscript,
    segment: Segment,
) -> str:
    """Return only the `[h:mm:ss] ...` lines whose timestamp lies in segment.

    Lines without a recognised `[h:mm:ss]` marker are preserved only if
    the transcript has no timestamp markers at all (plain text).
    """
    lines = transcript.text_for_llm.splitlines()
    if not any(_TIMESTAMP_LINE_RE.match(ln) for ln in lines):
        return transcript.text_for_llm

    kept: list[str] = []
    for ln in lines:
        m = _TIMESTAMP_LINE_RE.match(ln)
        if not m:
            continue
        try:
            t = parse_timestamp(m.group("ts"))
        except TimestampError:
            continue
        if segment.start <= t < segment.end:
            kept.append(ln)
    return "\n".join(kept)


def run(
    *,
    transcript: LoadedTranscript,
    structure: Structure,
    preamble_text: str,
    client: OpenRouterClient,
    model: str,
    hint_window_seconds: float = DEFAULT_HINT_WINDOW_SECONDS,
    anchor_tolerance_seconds: float = DEFAULT_ANCHOR_TOLERANCE_SECONDS,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    segment_ids: list[str] | None = None,
    accepted_context: list[AcceptedContextEntry] | None = None,
) -> ReadingBullets:
    """Run Stage 1b in parallel across `structure.segments`.

    :param segment_ids: if given, run only these segments (unknown ids
        raise). Default: run all segments.
    :param anchor_tolerance_seconds: anchors this far outside segment bounds
        are clamped; further raises Stage1bError.
    :param accepted_context: accepted segments injected as context block into
        user messages. None or empty → legacy message format.
    :raises Stage1bError: any segment's LLM response fails parsing /
        validation
    """
    if segment_ids is not None:
        known = {s.id for s in structure.segments}
        unknown = [sid for sid in segment_ids if sid not in known]
        if unknown:
            msg = f"unknown segment ids: {unknown}"
            raise Stage1bError(msg)
        targets = [s for s in structure.segments if s.id in set(segment_ids)]
    else:
        targets = list(structure.segments)

    results: dict[str, list[Bullet]] = {}
    with ThreadPoolExecutor(max_workers=max_concurrency) as ex:
        futures = {
            ex.submit(
                _run_one,
                seg,
                transcript,
                preamble_text,
                client,
                model,
                hint_window_seconds,
                anchor_tolerance_seconds,
                accepted_context,
            ): seg.id
            for seg in targets
        }
        for future, seg_id in futures.items():
            results[seg_id] = future.result()

    return ReadingBullets(
        source_id=structure.source_id,
        generated_at=format_iso_now(),
        segments=results,
    )


def _run_one(
    segment: Segment,
    transcript: LoadedTranscript,
    preamble_text: str,
    client: OpenRouterClient,
    model: str,
    hint_window_seconds: float,
    anchor_tolerance_seconds: float = DEFAULT_ANCHOR_TOLERANCE_SECONDS,
    accepted_context: list[AcceptedContextEntry] | None = None,
) -> list[Bullet]:
    messages = [
        {
            "role": "system",
            "content": build_system_prompt(preamble_text, _TASK_INSTRUCTIONS),
        },
        {
            "role": "user",
            "content": _build_user(
                segment,
                slice_transcript_for_segment(transcript, segment),
                accepted_context,
            ),
        },
    ]
    resp = client.complete(
        messages,
        model=model,
        response_format={"type": "json_object"},
    )
    try:
        payload = parse_json_object(resp.text, f"Stage 1b for {segment.id}")
    except ValueError as e:
        raise Stage1bError(str(e)) from e
    raw_bullets = payload.get("bullets") or []
    if not isinstance(raw_bullets, list):
        msg = f"Stage 1b for {segment.id}: 'bullets' must be a list"
        raise Stage1bError(msg)

    return [
        _parse_bullet(raw, segment, hint_window_seconds, anchor_tolerance_seconds)
        for raw in raw_bullets
    ]


def _build_user(
    segment: Segment,
    segment_text: str,
    accepted_context: list[AcceptedContextEntry] | None = None,
) -> str:
    parts: list[str] = []
    if accepted_context:
        parts.append("Accepted segments (context only — do not re-extract):\n")
        for entry in accepted_context:
            start_ts = format_timestamp(entry.start)
            end_ts = format_timestamp(entry.end)
            parts.append(
                f"## {entry.segment_id} [{start_ts}–{end_ts}]"  # noqa: RUF001
                f" {entry.title} ({entry.speaker})\n"
                f"{entry.bullets_body}"
            )
        parts.append("---\n")
    parts.append(
        f'Segment {segment.id}: "{segment.title}" '
        f"(speaker: {segment.speaker})\n"
        f"Range: {segment.start:.0f}s - {segment.end:.0f}s\n\n"
        f"Transcript for this segment:\n\n{segment_text}"
    )
    return "\n".join(parts)


def _parse_bullet(
    raw: dict[str, Any],
    segment: Segment,
    hint_window_seconds: float,
    anchor_tolerance_seconds: float = DEFAULT_ANCHOR_TOLERANCE_SECONDS,
) -> Bullet:
    if not isinstance(raw, dict):
        msg = f"Stage 1b for {segment.id}: each bullet must be an object"
        raise Stage1bError(msg)
    text = str(raw.get("text") or "").strip()
    if not text:
        msg = f"Stage 1b for {segment.id}: bullet has empty text"
        raise Stage1bError(msg)
    try:
        anchor = parse_timestamp(str(raw.get("anchor") or ""))
    except TimestampError as e:
        msg = f"Stage 1b for {segment.id}: bad anchor timestamp: {e}"
        raise Stage1bError(msg) from e
    if not (segment.start <= anchor <= segment.end):
        # check if within tolerance window; clamp if so, else raise
        if segment.end < anchor <= segment.end + anchor_tolerance_seconds:
            _logger.warning(
                "stage1b: clamping anchor %ss to segment %s end %ss (was %ss past)",
                anchor,
                segment.id,
                segment.end,
                anchor - segment.end,
            )
            anchor = segment.end
        elif segment.start - anchor_tolerance_seconds <= anchor < segment.start:
            _logger.warning(
                "stage1b: clamping anchor %ss to segment %s start %ss (was %ss before)",
                anchor,
                segment.id,
                segment.start,
                segment.start - anchor,
            )
            anchor = segment.start
        else:
            msg = (
                f"Stage 1b for {segment.id}: bullet anchor {anchor}s outside "
                f"segment ({segment.start}-{segment.end})"
            )
            raise Stage1bError(msg)
    hint_start = max(segment.start, anchor - hint_window_seconds)
    hint_end = min(segment.end, anchor + hint_window_seconds)
    return Bullet(
        text=text,
        anchor=anchor,
        locator_hint_start=hint_start,
        locator_hint_end=hint_end,
    )


_BULLETS_SCHEMA = 1


def write_bullets(bullets: ReadingBullets, path: Path) -> None:
    """Atomically write a ReadingBullets snapshot for regeneration."""
    data: dict[str, Any] = {
        "schema_version": _BULLETS_SCHEMA,
        "source_id": bullets.source_id,
        "generated_at": bullets.generated_at,
        "segments": {
            seg_id: [
                {
                    "text": b.text,
                    "anchor": format_timestamp(b.anchor),
                    "locator_hint_start": format_timestamp(b.locator_hint_start),
                    "locator_hint_end": format_timestamp(b.locator_hint_end),
                }
                for b in bullet_list
            ]
            for seg_id, bullet_list in bullets.segments.items()
        },
    }
    text = yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    atomic_write_text(path, text)


def read_bullets(path: Path) -> ReadingBullets:
    """Read a bullets.yaml snapshot."""
    if not path.exists():
        msg = f"{path}: file not found"
        raise Stage1bError(msg)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a YAML mapping"
        raise Stage1bError(msg)
    try:
        read_schema_version(raw, str(path), max_supported=_BULLETS_SCHEMA)
    except SchemaVersionError as e:
        raise Stage1bError(str(e)) from e
    segments_raw = raw.get("segments") or {}
    segments: dict[str, list[Bullet]] = {}
    for seg_id, bullet_list in segments_raw.items():
        segments[str(seg_id)] = [
            Bullet(
                text=str(b["text"]),
                anchor=parse_timestamp(str(b["anchor"])),
                locator_hint_start=parse_timestamp(str(b["locator_hint_start"])),
                locator_hint_end=parse_timestamp(str(b["locator_hint_end"])),
            )
            for b in bullet_list
        ]
    return ReadingBullets(
        source_id=str(raw.get("source_id") or ""),
        generated_at=str(raw.get("generated_at") or ""),
        segments=segments,
    )
