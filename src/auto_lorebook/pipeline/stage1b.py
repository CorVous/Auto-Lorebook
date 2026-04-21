"""Stage 1b: per-segment summarization with claim bullets."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import trio

from auto_lorebook.llm import complete
from auto_lorebook.sources.srt import SrtCue, seconds_to_canonical, ts_to_seconds

if TYPE_CHECKING:
    import httpx

    from auto_lorebook.config import ModelParams

_ANCHOR_RE = re.compile(r"\[(\d+:\d{2}:\d{2})\]\s*$")
_BULLET_RE = re.compile(r"^[-*]\s+(.+)$")
_DEFAULT_WINDOW = 30.0  # seconds for locator hint window


@dataclass(slots=True)
class Bullet:
    """Single claim bullet from Stage 1b."""

    text: str
    anchor: str  # h:mm:ss
    locator_hint: tuple[str, str]  # (start, end) window


@dataclass(slots=True)
class SegmentSummary:
    """Stage 1b output for a single segment."""

    segment_id: str
    bullets: list[Bullet] = field(default_factory=list)


def recentered_locator_hint(
    anchor: str,
    *,
    window_seconds: float = _DEFAULT_WINDOW,
) -> tuple[str, str]:
    """Compute (start, end) locator hint centered on anchor timestamp.

    :param anchor: h:mm:ss anchor
    :param window_seconds: total window width
    :return: (start, end) tuple in h:mm:ss format
    """
    secs = ts_to_seconds(anchor)
    half = window_seconds / 2.0
    start = seconds_to_canonical(max(0.0, secs - half))
    end = seconds_to_canonical(secs + half)
    return start, end


def _slice_cues(cues: list[SrtCue], start: str, end: str) -> list[SrtCue]:
    """Return cues whose start falls within [start_secs, end_secs]."""
    start_s = ts_to_seconds(start)
    end_s = ts_to_seconds(end)
    return [c for c in cues if start_s <= c.start_seconds <= end_s]


def format_segment_transcript(cues: list[SrtCue]) -> str:
    """Format a cue slice as prompt-ready plain text."""
    return "\n".join(f"[{c.start}] {c.text}" for c in cues)


_1B_INSTRUCTION = (
    "For each factual or narrative claim in the transcript above, output a bullet:\n"
    "  - Claim text. [h:mm:ss]\n\n"
    "Rules:\n"
    "- One bullet per distinct claim.\n"
    "- Embed the timestamp where the claim is stated.\n"
    "- If the segment has no extractable claims, output an empty list.\n"
    "- Output ONLY the bullet list. No headers, summaries, or explanations.\n"
)


def build_1b_prompt(
    preamble: str,
    segment: dict[str, object],
    cues: list[SrtCue],
) -> str:
    """Construct the Stage 1b prompt for a single segment.

    :param preamble: assembled context preamble
    :param segment: structure.yaml segment dict
    :param cues: all transcript cues (sliced to segment range)
    :return: full prompt string
    """
    start = str(segment.get("start", "0:00:00"))
    end = str(segment.get("end", "0:00:00"))
    title = str(segment.get("title", ""))
    speaker = str(segment.get("speaker") or "")
    sliced = _slice_cues(cues, start, end)
    transcript = format_segment_transcript(sliced)
    header = f"## Segment: {title}"
    if speaker:
        header += f" (speaker: {speaker})"
    return f"{preamble}\n\n---\n{header}\n\n{transcript}\n\n---\n{_1B_INSTRUCTION}"


def parse_1b_response(
    text: str,
    segment_id: str,
    *,
    window_seconds: float = _DEFAULT_WINDOW,
) -> SegmentSummary:
    """Parse 1b LLM response into a SegmentSummary with locator hints.

    :param text: LLM response text
    :param segment_id: segment identifier for the summary
    :param window_seconds: locator hint window width in seconds
    :return: SegmentSummary (empty bullets list is valid)
    """
    bullets: list[Bullet] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        bullet_m = _BULLET_RE.match(line)
        if not bullet_m:
            continue
        content = bullet_m.group(1).strip()
        anchor_m = _ANCHOR_RE.search(content)
        if anchor_m:
            anchor = anchor_m.group(1)
            text_part = content[: anchor_m.start()].strip().rstrip(".")
        else:
            anchor = "0:00:00"
            text_part = content
        hint = recentered_locator_hint(anchor, window_seconds=window_seconds)
        bullets.append(Bullet(text=text_part, anchor=anchor, locator_hint=hint))
    return SegmentSummary(segment_id=segment_id, bullets=bullets)


async def run_1b_segment(
    segment: dict[str, object],
    cues: list[SrtCue],
    preamble: str,
    *,
    model: str,
    params: ModelParams,
    api_key: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> SegmentSummary:
    """Run Stage 1b for a single segment.

    :param segment: structure.yaml segment dict
    :param cues: all transcript cues
    :param preamble: assembled context preamble
    :param model: LLM model string
    :param params: sampling parameters
    :param api_key: OpenRouter API key
    :param _transport: injectable transport for testing
    :return: SegmentSummary
    """
    prompt = build_1b_prompt(preamble, segment, cues)
    response = await complete(
        prompt,
        model=model,
        params=params,
        api_key=api_key,
        _transport=_transport,
    )
    return parse_1b_response(response, str(segment.get("id", "")))


async def run_stage_1b(
    structure: dict[str, object],
    cues: list[SrtCue],
    preamble: str,
    *,
    model: str,
    params: ModelParams,
    api_key: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> list[SegmentSummary]:
    """Run Stage 1b for all segments concurrently via trio.

    :param structure: parsed structure.yaml dict
    :param cues: all transcript cues
    :param preamble: assembled context preamble
    :param model: LLM model string
    :param params: sampling parameters
    :param api_key: OpenRouter API key
    :param _transport: injectable transport for testing
    :return: ordered list of SegmentSummary, one per segment
    """
    raw_segs = structure.get("segments")
    segments: list[dict[str, object]] = cast(
        "list[dict[str, object]]",
        [s if isinstance(s, dict) else {} for s in raw_segs]  # type: ignore[union-attr]
        if isinstance(raw_segs, list)
        else [],
    )
    results: list[SegmentSummary | None] = [None] * len(segments)

    async def _run_one(idx: int, seg: dict[str, object]) -> None:
        results[idx] = await run_1b_segment(
            seg,
            cues,
            preamble,
            model=model,
            params=params,
            api_key=api_key,
            _transport=_transport,
        )

    async with trio.open_nursery() as nursery:
        for i, seg in enumerate(segments):
            nursery.start_soon(_run_one, i, seg)

    return [r for r in results if r is not None]
