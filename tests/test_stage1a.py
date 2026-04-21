"""Tests for pipeline.stage1a module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

import yaml

from auto_lorebook.config import ModelParams
from auto_lorebook.pipeline.stage1a import (
    StructureValidationError,
    build_1a_prompt,
    extract_yaml_block,
    format_transcript,
    make_structure_inputs,
    validate_structure,
    write_structure_yaml,
)
from auto_lorebook.sources.srt import SrtCue, ts_to_seconds


def _cue(start: str, end: str, text: str = "test") -> SrtCue:
    return SrtCue(
        index=1,
        start=start,
        end=end,
        start_seconds=ts_to_seconds(start),
        end_seconds=ts_to_seconds(end),
        text=text,
    )


_CUES = [
    _cue("0:00:00", "0:05:00", "Opening"),
    _cue("0:05:00", "0:10:00", "Middle"),
    _cue("0:10:00", "0:15:00", "End"),
]

_VALID_STRUCTURE: dict[str, object] = {
    "schema_version": 1,
    "source_id": "test-src",
    "default_speaker": "DM",
    "segments": [
        {
            "id": "seg-001",
            "start": "0:00:00",
            "end": "0:07:00",
            "title": "Opening",
            "overrides": [],
            "notes": None,
        },
        {
            "id": "seg-002",
            "start": "0:07:00",
            "end": "0:15:00",
            "title": "Middle",
            "overrides": [],
            "notes": None,
        },
    ],
    "uncertainty_flags": [],
}


def test_format_transcript_contains_timestamps() -> None:
    text = format_transcript(_CUES)
    assert "[0:00:00]" in text
    assert "[0:10:00]" in text


def test_format_transcript_contains_cue_text() -> None:
    text = format_transcript(_CUES)
    assert "Opening" in text
    assert "End" in text


def test_build_1a_prompt_contains_preamble_and_transcript() -> None:
    prompt = build_1a_prompt("MY_PREAMBLE", "TRANSCRIPT_TEXT")
    assert "MY_PREAMBLE" in prompt
    assert "TRANSCRIPT_TEXT" in prompt


def test_extract_yaml_block_strips_fences() -> None:
    text = "```yaml\nfoo: bar\n```"
    assert extract_yaml_block(text) == "foo: bar"


def test_extract_yaml_block_strips_yml_alias() -> None:
    text = "```yml\nfoo: bar\n```"
    assert extract_yaml_block(text) == "foo: bar"


def test_extract_yaml_block_passthrough_plain() -> None:
    assert extract_yaml_block("foo: bar") == "foo: bar"


def test_validate_structure_passes_valid() -> None:
    validate_structure(_VALID_STRUCTURE, _CUES)  # no exception


def test_validate_structure_detects_gap() -> None:
    structure: dict[str, object] = {
        "segments": [
            {
                "id": "s1",
                "start": "0:00:00",
                "end": "0:05:00",
                "title": "A",
                "overrides": [],
            },
            # gap: s2 starts at 0:11:00, but s1 ends at 0:05:00 (360s gap)
            {
                "id": "s2",
                "start": "0:11:00",
                "end": "0:15:00",
                "title": "B",
                "overrides": [],
            },
        ],
        "uncertainty_flags": [],
    }
    with pytest.raises(StructureValidationError, match="gap"):
        validate_structure(structure, _CUES)


def test_validate_structure_detects_end_beyond_transcript() -> None:
    structure: dict[str, object] = {
        "segments": [
            {
                "id": "s1",
                "start": "0:00:00",
                "end": "1:00:00",
                "title": "A",
                "overrides": [],
            },
        ],
        "uncertainty_flags": [],
    }
    with pytest.raises(StructureValidationError):
        validate_structure(structure, _CUES)


def test_validate_structure_detects_override_out_of_bounds() -> None:
    structure: dict[str, object] = {
        "segments": [
            {
                "id": "s1",
                "start": "0:00:00",
                "end": "0:15:00",
                "title": "A",
                "overrides": [{"start": "0:16:00", "end": "0:20:00"}],
            },
        ],
        "uncertainty_flags": [],
    }
    with pytest.raises(StructureValidationError, match="Override"):
        validate_structure(structure, _CUES)


def test_validate_structure_detects_flag_outside_segments() -> None:
    structure: dict[str, object] = {
        "segments": [
            {
                "id": "s1",
                "start": "0:00:00",
                "end": "0:15:00",
                "title": "A",
                "overrides": [],
            },
        ],
        "uncertainty_flags": [{"locator": "0:20:00", "description": "unclear"}],
    }
    with pytest.raises(StructureValidationError, match="Uncertainty flag"):
        validate_structure(structure, _CUES)


def test_validate_structure_empty_cues_noop() -> None:
    validate_structure(_VALID_STRUCTURE, [])  # no exception


def test_make_structure_inputs_has_required_keys() -> None:
    inputs = make_structure_inputs(
        transcript_bytes=b"transcript",
        info_bytes=b"info",
        wiki_bytes=b"wiki",
        corrections_bytes=b"corrections",
        entity_index=[],
        preamble="preamble",
        model="openrouter/x",
        params=ModelParams(),
    )
    for key in (
        "transcript_sha256",
        "info_yaml_sha256",
        "wiki_context_sha256",
        "corrections_sha256",
        "entity_index_sha256",
        "preamble_sha256",
        "model",
        "model_params_sha256",
    ):
        assert key in inputs, f"missing key: {key}"
    assert inputs["model"] == "openrouter/x"


def test_make_structure_inputs_deterministic() -> None:
    kwargs: dict[str, object] = {
        "transcript_bytes": b"t",
        "info_bytes": b"i",
        "wiki_bytes": b"w",
        "corrections_bytes": b"c",
        "entity_index": [],
        "preamble": "p",
        "model": "m",
        "params": ModelParams(),
    }
    a = make_structure_inputs(**kwargs)  # type: ignore[arg-type]
    b = make_structure_inputs(**kwargs)  # type: ignore[arg-type]
    assert a == b


def test_write_structure_yaml_creates_file(tmp_path: Path) -> None:
    out = tmp_path / "structure.yaml"
    inputs = make_structure_inputs(
        transcript_bytes=b"t",
        info_bytes=b"i",
        wiki_bytes=b"w",
        corrections_bytes=b"c",
        entity_index=[],
        preamble="p",
        model="m",
        params=ModelParams(),
    )
    write_structure_yaml(out, dict(_VALID_STRUCTURE), inputs, "2026-04-21T00:00:00Z")
    assert out.exists()
    data = yaml.safe_load(out.read_text())
    assert "inputs" in data
    assert data["inputs"]["model"] == "m"
    assert data["generated_at"] == "2026-04-21T00:00:00Z"
