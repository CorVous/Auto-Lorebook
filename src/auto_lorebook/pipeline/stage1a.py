"""Stage 1a: transcript structure extraction."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import yaml

from auto_lorebook.llm import complete, params_sha256
from auto_lorebook.pipeline.corrections import apply_corrections
from auto_lorebook.sources.srt import SrtCue, seconds_to_canonical, ts_to_seconds

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

    from auto_lorebook.config import ModelParams

_TOLERANCE = 5.0  # seconds, for boundary checks
_YAML_FENCE = re.compile(r"```ya?ml\s*\n(.*?)\n```", re.DOTALL)

_1A_INSTRUCTION = """
Analyze the transcript above and produce a structure.yaml document.

Output ONLY valid YAML between ```yaml and ``` fences.

Required schema:
```yaml
schema_version: 1
source_id: <source_id>
default_speaker: <name or null>
segments:
  - id: seg-001
    start: "h:mm:ss"
    end: "h:mm:ss"
    title: "Descriptive segment title"
    speaker: null
    overrides: []
    notes: null
uncertainty_flags:
  - locator: "h:mm:ss"
    description: "What is uncertain"
```

Guidelines:
- Divide the transcript into meaningful thematic segments.
- Segments must cover the full transcript without gaps.
- All timestamps must be in h:mm:ss format (e.g., 0:05:32).
- Err toward over-flagging uncertainty.
"""


class StructureValidationError(ValueError):
    """structure.yaml mechanical validation failure."""


def format_transcript(cues: list[SrtCue]) -> str:
    """Format SRT cues as plain-text lines for the prompt.

    :param cues: parsed SRT cues
    :return: newline-separated [h:mm:ss] text lines
    """
    return "\n".join(f"[{cue.start}] {cue.text}" for cue in cues)


def build_1a_prompt(preamble: str, corrected_transcript: str) -> str:
    """Construct the Stage 1a prompt.

    :param preamble: assembled context preamble
    :param corrected_transcript: transcript with corrections applied
    :return: full prompt string
    """
    return f"{preamble}\n\n---\n\n{corrected_transcript}\n\n---\n{_1A_INSTRUCTION}"


def extract_yaml_block(text: str) -> str:
    """Strip ```yaml / ``` fences from LLM output, returning raw YAML.

    :param text: LLM response text
    :return: YAML content string (fenced or plain)
    """
    m = _YAML_FENCE.search(text)
    return m.group(1) if m else text.strip()


def validate_structure(
    structure: dict[str, object],
    cues: list[SrtCue],
) -> None:
    """Mechanical validation of a parsed structure.yaml dict.

    Checks: timestamp validity, transcript coverage, no gaps between
    consecutive segments, override ranges within parent, uncertainty
    flag locators within some segment.

    :param structure: parsed structure dict
    :param cues: SRT cues for bounds reference
    :raises StructureValidationError: on any violation
    """
    if not cues:
        return

    transcript_start = min(c.start_seconds for c in cues)
    transcript_end = max(c.end_seconds for c in cues)

    raw_segs = structure.get("segments")
    segments: list[dict[str, object]] = cast(
        "list[dict[str, object]]",
        list(raw_segs) if isinstance(raw_segs, list) else [],
    )

    seg_intervals: list[tuple[float, float]] = []
    prev_end: float | None = None

    for seg in segments:
        seg_dict = dict(seg) if isinstance(seg, dict) else {}
        seg_id = str(seg_dict.get("id", "?"))

        try:
            start_s = ts_to_seconds(str(seg_dict["start"]))
            end_s = ts_to_seconds(str(seg_dict["end"]))
        except (KeyError, ValueError) as exc:
            msg = f"Segment {seg_id}: invalid timestamp — {exc}"
            raise StructureValidationError(msg) from exc

        if start_s >= end_s:
            msg = f"Segment {seg_id}: start >= end"
            raise StructureValidationError(msg)

        if end_s > transcript_end + _TOLERANCE:
            ts_end = seconds_to_canonical(transcript_end)
            msg = (
                f"Segment {seg_id}: end {seg_dict['end']!r} exceeds"
                f" transcript end {ts_end}"
            )
            raise StructureValidationError(msg)

        if prev_end is not None and start_s > prev_end + _TOLERANCE:
            gap = start_s - prev_end
            msg = f"Segment {seg_id}: gap of {gap:.1f}s from previous segment"
            raise StructureValidationError(msg)

        prev_end = end_s
        seg_intervals.append((start_s, end_s))

        raw_ovs = seg_dict.get("overrides")
        overrides: list[object] = list(raw_ovs) if isinstance(raw_ovs, list) else []
        for ov in overrides:
            ov_dict = dict(ov) if isinstance(ov, dict) else {}  # type: ignore[arg-type]
            try:
                ov_s = ts_to_seconds(str(ov_dict["start"]))
                ov_e = ts_to_seconds(str(ov_dict["end"]))
            except (KeyError, ValueError) as exc:
                msg = f"Override in {seg_id}: invalid timestamp — {exc}"
                raise StructureValidationError(msg) from exc
            if ov_s < start_s or ov_e > end_s:
                msg = (
                    f"Override in {seg_id}:"
                    f" [{ov_dict.get('start')}, {ov_dict.get('end')}]"
                    f" outside segment [{seg_dict['start']}, {seg_dict['end']}]"
                )
                raise StructureValidationError(msg)

    if seg_intervals:
        if seg_intervals[0][0] > transcript_start + _TOLERANCE:
            ts = seconds_to_canonical(transcript_start)
            msg = f"Segments don't cover transcript start ({ts})"
            raise StructureValidationError(msg)
        if seg_intervals[-1][1] < transcript_end - _TOLERANCE:
            ts = seconds_to_canonical(transcript_end)
            msg = f"Segments don't cover transcript end ({ts})"
            raise StructureValidationError(msg)

    raw_flags = structure.get("uncertainty_flags")
    flags: list[object] = list(raw_flags) if isinstance(raw_flags, list) else []
    for flag in flags:
        flag_dict = dict(flag) if isinstance(flag, dict) else {}  # type: ignore[arg-type]
        try:
            loc_s = ts_to_seconds(str(flag_dict["locator"]))
        except (KeyError, ValueError) as exc:
            msg = f"Uncertainty flag: invalid locator — {exc}"
            raise StructureValidationError(msg) from exc
        if not any(s <= loc_s <= e for s, e in seg_intervals):
            msg = (
                f"Uncertainty flag at {flag_dict.get('locator')!r}"
                " is not within any segment"
            )
            raise StructureValidationError(msg)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_structure_inputs(
    *,
    transcript_bytes: bytes,
    info_bytes: bytes,
    wiki_bytes: bytes,
    corrections_bytes: bytes,
    entity_index: list[dict[str, object]],
    preamble: str,
    model: str,
    params: ModelParams,
) -> dict[str, str]:
    """Build the staleness inputs block for structure.yaml.

    :param transcript_bytes: raw .srt bytes
    :param info_bytes: info.yaml bytes
    :param wiki_bytes: .wiki-context.yaml bytes
    :param corrections_bytes: .transcription-corrections.yaml bytes
    :param entity_index: entity records
    :param preamble: assembled preamble string
    :param model: LLM model string
    :param params: sampling parameters
    :return: dict of SHA-256 hashes and identity fields
    """
    entity_canon = json.dumps(entity_index, sort_keys=True, ensure_ascii=False).encode()
    return {
        "transcript_sha256": _sha256(transcript_bytes),
        "info_yaml_sha256": _sha256(info_bytes),
        "wiki_context_sha256": _sha256(wiki_bytes),
        "corrections_sha256": _sha256(corrections_bytes),
        "entity_index_sha256": _sha256(entity_canon),
        "preamble_sha256": _sha256(preamble.encode()),
        "model": model,
        "model_params_sha256": params_sha256(params),
    }


def write_structure_yaml(
    path: Path,
    structure: dict[str, object],
    inputs: dict[str, str],
    generated_at: str | None = None,
) -> None:
    """Write structure.yaml with embedded inputs staleness block.

    :param path: destination path (parent dirs created as needed)
    :param structure: structure dict from LLM
    :param inputs: staleness inputs block
    :param generated_at: RFC 3339 UTC timestamp (defaults to now)
    """
    data = dict(structure)
    data["inputs"] = inputs
    data["generated_at"] = generated_at or datetime.now(tz=UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)


async def run_stage_1a(
    cues: list[SrtCue],
    preamble: str,
    corrections: list[dict[str, str]],
    *,
    source_id: str,
    model: str,
    params: ModelParams,
    output_path: Path,
    transcript_bytes: bytes,
    info_bytes: bytes,
    wiki_bytes: bytes,
    corrections_bytes: bytes,
    entity_index: list[dict[str, object]],
    api_key: str | None = None,
    _transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    """Orchestrate Stage 1a: prompt LLM, validate, write structure.yaml.

    :param cues: parsed SRT cues
    :param preamble: assembled context preamble
    :param corrections: transcription corrections to apply
    :param source_id: source identifier (injected into structure)
    :param model: LLM model string
    :param params: sampling parameters
    :param output_path: where to write structure.yaml
    :param transcript_bytes: raw .srt bytes for staleness hashing
    :param info_bytes: info.yaml bytes
    :param wiki_bytes: .wiki-context.yaml bytes
    :param corrections_bytes: .transcription-corrections.yaml bytes
    :param entity_index: entity records
    :param api_key: OpenRouter API key
    :param _transport: injectable transport for testing
    :return: validated structure dict
    """
    corrected = apply_corrections(format_transcript(cues), corrections)
    prompt = build_1a_prompt(preamble, corrected)
    response = await complete(
        prompt,
        model=model,
        params=params,
        api_key=api_key,
        _transport=_transport,
    )
    raw_yaml = extract_yaml_block(response)
    structure: dict[str, object] = yaml.safe_load(raw_yaml) or {}
    structure.setdefault("source_id", source_id)
    validate_structure(structure, cues)
    inputs = make_structure_inputs(
        transcript_bytes=transcript_bytes,
        info_bytes=info_bytes,
        wiki_bytes=wiki_bytes,
        corrections_bytes=corrections_bytes,
        entity_index=entity_index,
        preamble=preamble,
        model=model,
        params=params,
    )
    write_structure_yaml(output_path, structure, inputs)
    return structure
