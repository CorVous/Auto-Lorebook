"""Tests for reading.staleness module."""

from __future__ import annotations

import pytest

from auto_lorebook.config import ModelParams
from auto_lorebook.reading.staleness import (
    StalenessError,
    check_staleness,
    make_reading_inputs,
)


def _base_inputs(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = make_reading_inputs(  # ty: ignore[invalid-assignment]
        transcript_bytes=b"transcript",
        info_bytes=b"info",
        wiki_bytes=b"wiki",
        corrections_bytes=b"corrections",
        entity_index=[],
        preamble="preamble",
        structure_bytes=b"structure",
        model="openrouter/x",
        params=ModelParams(),
    )
    result.update(overrides)
    return result


def test_make_reading_inputs_has_required_keys() -> None:
    result = make_reading_inputs(
        transcript_bytes=b"t",
        info_bytes=b"i",
        wiki_bytes=b"w",
        corrections_bytes=b"c",
        entity_index=[],
        preamble="p",
        structure_bytes=b"s",
        model="m",
        params=ModelParams(),
    )
    for key in (
        "transcript_sha256",
        "info_yaml_sha256",
        "wiki_context_sha256",
        "corrections_sha256",
        "entity_index_sha256",
        "preamble_sha256",
        "structure_sha256",
        "model",
        "model_params_sha256",
    ):
        assert key in result, f"missing key: {key}"


def test_make_reading_inputs_deterministic() -> None:
    a = _base_inputs()
    b = _base_inputs()
    assert a == b


def test_check_staleness_passes_identical() -> None:
    inp = _base_inputs()
    check_staleness(inp, inp)  # no exception


def test_check_staleness_raises_on_transcript_change() -> None:
    recorded = _base_inputs()
    current = make_reading_inputs(
        transcript_bytes=b"DIFFERENT",
        info_bytes=b"info",
        wiki_bytes=b"wiki",
        corrections_bytes=b"corrections",
        entity_index=[],
        preamble="preamble",
        structure_bytes=b"structure",
        model="openrouter/x",
        params=ModelParams(),
    )
    with pytest.raises(StalenessError) as exc_info:
        check_staleness(recorded, current)  # type: ignore[arg-type]
    assert "transcript_sha256" in str(exc_info.value)


def test_check_staleness_raises_on_model_change() -> None:
    recorded = _base_inputs()
    current = _base_inputs(model="openrouter/different-model")
    with pytest.raises(StalenessError) as exc_info:
        check_staleness(recorded, current)
    assert "model" in str(exc_info.value)


def test_staleness_error_names_remedy() -> None:
    err = StalenessError("transcript_sha256")
    assert "regenerate-reading" in str(err)
    assert "transcript_sha256" in str(err)


def test_staleness_error_stores_changed_input() -> None:
    err = StalenessError("wiki_context_sha256")
    assert err.changed_input == "wiki_context_sha256"
