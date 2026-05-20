"""Stage 3: Extractor.

Locates the verbatim transcript span for each planned claim. One LLM
call per `PlannedClaim` (not per target — targets sharing one bullet share
the located span via claim-group dedup). The LLM is asked only for the
literal span plus the corrected text and any applied corrections; locator,
context, speaker, and section are derived mechanically from the plan, the
transcript cues, and the source `Info`.

No filesystem side effects in this module — orchestration writes the
proposal files via :func:`auto_lorebook.proposal_yaml.write`.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auto_lorebook import entity_yaml as entity_yaml_mod
from auto_lorebook import proposal_yaml as proposal_yaml_mod
from auto_lorebook import transcript as transcript_mod
from auto_lorebook.llm_helpers import build_system_prompt, parse_json_object
from auto_lorebook.proposal_yaml import (
    Correction,
    Proposal,
    ProposalTarget,
)
from auto_lorebook.timestamps import (
    TimestampError,
    format_timestamp,
    parse_locator_hint,
    parse_timestamp,
)

if TYPE_CHECKING:
    from auto_lorebook.info_yaml import Info
    from auto_lorebook.openrouter import OpenRouterClient
    from auto_lorebook.plan_yaml import Plan, PlannedClaim
    from auto_lorebook.structure import Segment, Structure
    from auto_lorebook.transcript import LoadedTranscript

_logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENCY = 4

_READING_SECTION_PREFIX_RE = re.compile(
    r"^\s*\[(?P<start>[0-9:,.]+)\s*-\s*(?P<end>[0-9:,.]+)\]"
)

_TASK_INSTRUCTIONS = """\
You locate the verbatim transcript span for ONE planned claim from an
already-approved wiki reading. The user message gives you the bullet
text plus a windowed slice of the raw transcript covering the claim's
locator hint. Find the cleanest contiguous span in that window that
states the claim, return it verbatim, and report any
transcription/name corrections applied to produce the cleaned `text`.

Emit a single JSON object:

{
  "text": "<corrected, human-readable claim>",
  "raw_transcript_span": "<literal substring of the transcript window>",
  "text_corrects_transcript": true | false,
  "corrections_applied": [
    {
      "from": "<token in raw_transcript_span>",
      "to":   "<replacement in text>",
      "source": "global-transcription-correction" | "reading-name-correction"
    }
  ]
}

Hard rules:
- `raw_transcript_span` MUST be a literal substring of the transcript
  window. Do NOT paraphrase, fix grammar, or remove filler.
- `text` differs from `raw_transcript_span` only via the listed
  corrections. No rewriting.
- Use ONE contiguous span. If the claim is not present in a single
  contiguous run, emit your best attempt and the substring check will
  flag it.
- `corrections_applied[].source` MUST be one of the two literal strings
  shown above. Use `global-transcription-correction` for substitutions
  driven by `.transcription-corrections.yaml`, and
  `reading-name-correction` for the reading's `name_corrections` map.
- If no corrections were applied, emit `corrections_applied: []` and
  `text_corrects_transcript: false`.

Emit ONLY the JSON object. No prose, no code fences, no commentary.
"""


class Stage3Error(RuntimeError):
    """Stage 3 failed: bad LLM output, schema violation, or unanchorable input."""


@dataclass(frozen=True)
class _Allocation:
    """Pre-allocated id for one claim."""

    proposed_id: str


def find_segment_for_reading_section(
    structure: Structure, reading_section: str, *, tolerance: float = 1.0
) -> Segment | None:
    """Match `[h:mm:ss-h:mm:ss] ...` prefix against `structure.segments[*].start`."""
    m = _READING_SECTION_PREFIX_RE.match(reading_section)
    if not m:
        return None
    try:
        start = parse_timestamp(m.group("start"))
    except TimestampError:
        return None
    for seg in structure.segments:
        if abs(seg.start - start) <= tolerance:
            return seg
    return None


def allocate_proposed_ids(
    plan: Plan,
    *,
    existing_fact_counts: dict[str, int],
    existing_slugs: dict[str, str],
) -> dict[str, _Allocation]:
    """Assign one `proposed_id` per claim group, single-threaded.

    ID uses the first target's slug as prefix, counter from
    `existing_fact_counts[first_target_entity]`.
    Returns a map keyed by `claim_group_id`.
    """
    counters: dict[str, int] = {}
    allocations: dict[str, _Allocation] = {}
    for claim in plan.planned_claims:
        first_target = claim.targets[0]
        slug = existing_slugs.get(
            first_target.entity, entity_yaml_mod.slugify(first_target.entity)
        )
        if not slug:
            msg = (
                f"cannot derive slug for target entity {first_target.entity!r}; "
                f"entity_yaml.slugify returned empty"
            )
            raise Stage3Error(msg)
        base = counters.get(
            first_target.entity,
            existing_fact_counts.get(first_target.entity, 0),
        )
        n = base + 1
        counters[first_target.entity] = n
        allocations[claim.claim_group_id] = _Allocation(
            proposed_id=f"{slug}-f{n:03d}",
        )
    return allocations


def _build_user(claim: PlannedClaim, window_text: str) -> str:
    bullet_lines = [
        f"Claim group: {claim.claim_group_id}",
        f"Reading section: {claim.reading_section}",
        f"Bullet (from approved reading): {claim.targets[0].entity}",
        "",
        "Bullet text being located:",
        f"  {claim.reading_section}  bullet[{claim.reading_bullet_index}]",
        "",
        f"Locator hint: {claim.locator_hint}",
        "",
        "Transcript window (locate the span inside this slice):",
        "",
        window_text.rstrip() or "(empty window)",
    ]
    return "\n".join(bullet_lines)


@dataclass(frozen=True)
class _ExtractedSpan:
    """LLM-derived span before mechanical assembly."""

    text: str
    raw_transcript_span: str
    text_corrects_transcript: bool
    corrections: tuple[Correction, ...]


def _parse_extracted_span(payload: dict[str, Any], *, context: str) -> _ExtractedSpan:
    raw_span = payload.get("raw_transcript_span")
    text = payload.get("text")
    if not isinstance(raw_span, str) or not raw_span:
        msg = f"{context}: missing raw_transcript_span"
        raise Stage3Error(msg)
    if not isinstance(text, str) or not text:
        msg = f"{context}: missing text"
        raise Stage3Error(msg)
    text_corrects = bool(payload.get("text_corrects_transcript"))
    corrections_raw = payload.get("corrections_applied") or []
    if not isinstance(corrections_raw, list):
        msg = f"{context}: corrections_applied must be a list"
        raise Stage3Error(msg)
    corrections: list[Correction] = []
    for entry in corrections_raw:
        try:
            corrections.append(proposal_yaml_mod.parse_correction(entry))
        except proposal_yaml_mod.ProposalError as e:
            # LLM output quality issue — drop the malformed entry rather than
            # killing the whole proposal. Disk reads stay strict via
            # `proposal_yaml.read`.
            _logger.warning(
                "%s: dropping malformed correction %r: %s", context, entry, e
            )
    return _ExtractedSpan(
        text=text,
        raw_transcript_span=raw_span,
        text_corrects_transcript=text_corrects,
        corrections=tuple(corrections),
    )


@dataclass(frozen=True)
class _LocatedSpan:
    """Result of mechanical span location in the transcript."""

    locator: str
    context_before: str
    context_after: str


def _locate_span_in_cues(
    transcript: LoadedTranscript,
    cues: list[Any],
    raw_span: str,
) -> _LocatedSpan | None:
    """Find `raw_span` in the rendered cue text and derive locator + context.

    Returns None if `raw_span` is not a substring of the rendered window.
    """
    if not cues:
        return None
    rendered = "\n".join(c.text for c in cues)
    if raw_span not in rendered:
        return None

    # Find the cue range that contains the span by walking offsets.
    offset = 0
    starts: list[int] = []
    for c in cues:
        starts.append(offset)
        offset += len(c.text) + 1  # +1 for the join newline
    span_start = rendered.index(raw_span)
    span_end = span_start + len(raw_span)

    first_idx = 0
    for i, s in enumerate(starts):
        next_s = starts[i + 1] if i + 1 < len(starts) else len(rendered) + 1
        if s <= span_start < next_s:
            first_idx = i
            break

    last_idx = first_idx
    for i in range(first_idx, len(starts)):
        s = starts[i]
        next_s = starts[i + 1] if i + 1 < len(starts) else len(rendered) + 1
        if s < span_end <= next_s:
            last_idx = i
            break
        last_idx = i

    first_cue = cues[first_idx]
    last_cue = cues[last_idx]
    locator_start = first_cue.start
    if last_idx + 1 < len(cues):
        locator_end = cues[last_idx + 1].start
    elif transcript.cues is not None and last_cue is transcript.cues[-1]:
        locator_end = transcript.total_duration
    else:
        # last cue of window but not of transcript: peek next cue in transcript
        all_cues = transcript.cues or ()
        try:
            global_idx = all_cues.index(last_cue)
            locator_end = (
                all_cues[global_idx + 1].start
                if global_idx + 1 < len(all_cues)
                else transcript.total_duration
            )
        except ValueError:
            locator_end = last_cue.end

    locator = f"{format_timestamp(locator_start)}-{format_timestamp(locator_end)}"
    all_cues = transcript.cues or ()
    try:
        global_first = all_cues.index(first_cue)
        global_last = all_cues.index(last_cue)
    except ValueError:
        global_first = global_last = -1
    context_before = all_cues[global_first - 1].text if global_first > 0 else ""
    context_after = (
        all_cues[global_last + 1].text
        if global_last >= 0 and global_last + 1 < len(all_cues)
        else ""
    )
    return _LocatedSpan(
        locator=locator,
        context_before=context_before,
        context_after=context_after,
    )


def _build_proposal(
    *,
    claim: PlannedClaim,
    allocation: _Allocation,
    extracted: _ExtractedSpan,
    located: _LocatedSpan,
    info: Info,
    source_id: str,
    hint_widened: bool,
) -> Proposal:
    session_date = info.session_date or ""
    targets = [
        ProposalTarget(
            entity=target.entity,
            section=target.proposed_section,
            speaker=claim.proposed_speaker,
            proposal_type=(
                "new_entity_with_facts" if target.entity_state == "new" else "new_fact"
            ),
            proposed_category=getattr(target, "proposed_category", None),
        )
        for target in claim.targets
    ]
    return Proposal(
        proposed_id=allocation.proposed_id,
        claim_group_id=claim.claim_group_id,
        targets=targets,
        text=extracted.text,
        raw_transcript_span=extracted.raw_transcript_span,
        text_corrects_transcript=extracted.text_corrects_transcript,
        corrections_applied=list(extracted.corrections),
        source_id=source_id,
        locator=located.locator,
        reading_section=claim.reading_section,
        reading_bullet_index=claim.reading_bullet_index,
        status=claim.proposed_status,
        status_reason=claim.proposed_status_reason,
        session_date=session_date,
        context_before=located.context_before,
        context_after=located.context_after,
        hint_widened=hint_widened,
        extractor_flagged=False,
        flag_reason=None,
    )


_EMPTY_EXTRACTED = _ExtractedSpan(
    text="",
    raw_transcript_span="",
    text_corrects_transcript=False,
    corrections=(),
)


def _build_flagged_proposal(
    *,
    claim: PlannedClaim,
    allocation: _Allocation,
    extracted: _ExtractedSpan,
    info: Info,
    source_id: str,
    flag_reason: str,
) -> Proposal:
    """Emit one flagged proposal when the span can't be anchored."""
    session_date = info.session_date or ""
    targets = [
        ProposalTarget(
            entity=target.entity,
            section=target.proposed_section,
            speaker=claim.proposed_speaker,
            proposal_type=(
                "new_entity_with_facts" if target.entity_state == "new" else "new_fact"
            ),
            proposed_category=getattr(target, "proposed_category", None),
        )
        for target in claim.targets
    ]
    return Proposal(
        proposed_id=allocation.proposed_id,
        claim_group_id=claim.claim_group_id,
        targets=targets,
        text=extracted.text,
        raw_transcript_span=extracted.raw_transcript_span,
        text_corrects_transcript=extracted.text_corrects_transcript,
        corrections_applied=list(extracted.corrections),
        source_id=source_id,
        locator=claim.locator_hint,
        reading_section=claim.reading_section,
        reading_bullet_index=claim.reading_bullet_index,
        status=claim.proposed_status,
        status_reason=claim.proposed_status_reason,
        session_date=session_date,
        context_before="",
        context_after="",
        hint_widened=False,
        extractor_flagged=True,
        flag_reason=flag_reason,
    )


def _extract_one(
    *,
    claim: PlannedClaim,
    allocation: _Allocation,
    transcript: LoadedTranscript,
    structure: Structure,
    info: Info,
    preamble_text: str,
    source_id: str,
    client: OpenRouterClient,
    model: str,
) -> Proposal:
    """Run one LLM call per claim group; emit one Proposal with N targets."""
    try:
        hint_start, hint_end = parse_locator_hint(claim.locator_hint)
    except TimestampError as e:
        msg = f"claim {claim.claim_group_id}: bad locator_hint: {e}"
        raise Stage3Error(msg) from e
    hint_text, hint_cues = transcript_mod.transcript_window(
        transcript, hint_start, hint_end
    )

    messages = [
        {
            "role": "system",
            "content": build_system_prompt(preamble_text, _TASK_INSTRUCTIONS),
        },
        {"role": "user", "content": _build_user(claim, hint_text)},
    ]
    resp = client.complete(
        messages,
        model=model,
        response_format={"type": "json_object"},
    )
    context = f"Stage 3 / {claim.claim_group_id}"
    try:
        payload = parse_json_object(resp.text, context)
    except ValueError as e:
        # LLM emitted unparseable JSON. Don't kill the whole run — flag the
        # claim so the reviewer sees it and can edit/reject.
        _logger.warning("%s: malformed JSON; flagging claim group: %s", context, e)
        return _build_flagged_proposal(
            claim=claim,
            allocation=allocation,
            extracted=_EMPTY_EXTRACTED,
            info=info,
            source_id=source_id,
            flag_reason=f"Stage 3 LLM returned unparseable JSON: {e}",
        )
    try:
        extracted = _parse_extracted_span(payload, context=context)
    except Stage3Error as e:
        # Schema-level degeneracy (missing raw_transcript_span / text). Same
        # treatment: flag and continue so other claim groups land their work.
        _logger.warning("%s: schema violation; flagging claim group: %s", context, e)
        return _build_flagged_proposal(
            claim=claim,
            allocation=allocation,
            extracted=_EMPTY_EXTRACTED,
            info=info,
            source_id=source_id,
            flag_reason=f"Stage 3 LLM output missing required fields: {e}",
        )

    # Try to anchor in the hint window first.
    located = _locate_span_in_cues(transcript, hint_cues, extracted.raw_transcript_span)
    hint_widened = False
    if located is None:
        # Mechanical retry against the parent segment's window — no 2nd LLM call.
        segment = find_segment_for_reading_section(structure, claim.reading_section)
        if segment is not None:
            seg_text, seg_cues = transcript_mod.transcript_window(
                transcript, segment.start, segment.end
            )
            seg_located = _locate_span_in_cues(
                transcript, seg_cues, extracted.raw_transcript_span
            )
            if seg_located is not None:
                located = seg_located
                hint_widened = True
                _ = seg_text  # keep for symmetry / future debugging

    if located is None:
        return _build_flagged_proposal(
            claim=claim,
            allocation=allocation,
            extracted=extracted,
            info=info,
            source_id=source_id,
            flag_reason=(
                "raw_transcript_span not found in hint window or parent segment"
            ),
        )

    return _build_proposal(
        claim=claim,
        allocation=allocation,
        extracted=extracted,
        located=located,
        info=info,
        source_id=source_id,
        hint_widened=hint_widened,
    )


def run(
    *,
    plan: Plan,
    transcript: LoadedTranscript,
    structure: Structure,
    info: Info,
    preamble_text: str,
    source_id: str,
    client: OpenRouterClient,
    model: str,
    existing_fact_counts: dict[str, int],
    existing_slugs: dict[str, str],
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> list[Proposal]:
    """Run Stage 3 against an approved plan and produce a list of Proposals.

    One Proposal per claim group (with N targets). A merely-unlocatable
    span yields an ``extractor_flagged=True`` proposal, not an exception.

    :raises Stage3Error: on bad LLM JSON, schema violation, or
        unanchorable input (e.g. plain-text transcript).
    """
    if transcript.cues is None:
        msg = "Stage 3 needs SRT timestamps; this source is plain text"
        raise Stage3Error(msg)
    if not plan.planned_claims:
        return []

    allocations = allocate_proposed_ids(
        plan,
        existing_fact_counts=existing_fact_counts,
        existing_slugs=existing_slugs,
    )

    results: list[Proposal | None] = [None] * len(plan.planned_claims)
    with ThreadPoolExecutor(max_workers=max_concurrency) as ex:
        futures = {
            ex.submit(
                _extract_one,
                claim=claim,
                allocation=allocations[claim.claim_group_id],
                transcript=transcript,
                structure=structure,
                info=info,
                preamble_text=preamble_text,
                source_id=source_id,
                client=client,
                model=model,
            ): i
            for i, claim in enumerate(plan.planned_claims)
        }
        for future, i in futures.items():
            results[i] = future.result()

    return [p for p in results if p is not None]
