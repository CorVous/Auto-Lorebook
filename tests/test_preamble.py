"""Tests for preamble assembly and token-budget check."""

import pytest

from auto_lorebook.preamble import (
    TokenBudgetError,
    assemble_preamble,
    check_token_budget,
)

_INFO_CTX = {
    "perspective": "Cor playing Kiki",
    "source_nature": "actual-play",
    "session_date": "2026-01-15",
    "speakers": [{"name": "Finn", "role": "guest-player", "character": "Brannoc"}],
    "notes": "Picks up mid-session.",
}

_WIKI_CTX = {
    "setting": {
        "name": "Aether Chronicles",
        "description": "High-fantasy setting.",
    },
    "naming_conventions": "Characters referred to by first name.",
    "interpretation_defaults": "DM narration is authoritative.",
    "recurring_speakers": [
        {"name": "Cor", "role": "player", "usual_character": "Kiki"},
        {"name": "Jess", "role": "DM"},
    ],
}

_CORRECTIONS = [
    {"from": "Fair-on", "to": "Theron"},
    {"from": "all-dara", "to": "Aldara"},
]

_ENTITIES = [
    {"name": "Theron", "category": "characters", "aliases": ["King Theron"]},
    {"name": "Aldara", "category": "locations", "aliases": []},
]


def test_preamble_contains_source_context() -> None:
    preamble = assemble_preamble(_INFO_CTX, _WIKI_CTX, _CORRECTIONS, [])
    assert "Cor playing Kiki" in preamble
    assert "actual-play" in preamble
    assert "2026-01-15" in preamble
    assert "Picks up mid-session" in preamble


def test_preamble_contains_setting_context() -> None:
    preamble = assemble_preamble(_INFO_CTX, _WIKI_CTX, _CORRECTIONS, [])
    assert "Aether Chronicles" in preamble
    assert "High-fantasy" in preamble
    assert "DM narration is authoritative" in preamble


def test_preamble_contains_corrections() -> None:
    preamble = assemble_preamble(_INFO_CTX, _WIKI_CTX, _CORRECTIONS, [])
    assert "Fair-on" in preamble
    assert "Theron" in preamble


def test_preamble_contains_entity_index() -> None:
    preamble = assemble_preamble(_INFO_CTX, _WIKI_CTX, _CORRECTIONS, _ENTITIES)
    assert "Theron" in preamble
    assert "King Theron" in preamble
    assert "Aldara" in preamble


def test_preamble_empty_entity_index() -> None:
    preamble = assemble_preamble(_INFO_CTX, _WIKI_CTX, _CORRECTIONS, [])
    assert "Entities in this wiki" in preamble


def test_preamble_empty_contexts() -> None:
    """Tolerates missing/empty context objects."""
    preamble = assemble_preamble({}, {}, [], [])
    assert isinstance(preamble, str)
    assert len(preamble) > 0


def test_preamble_reduced_skips_setting_and_source() -> None:
    """Reduced preamble: omits source/setting; keeps corrections + aliases."""
    preamble = assemble_preamble(
        _INFO_CTX, _WIKI_CTX, _CORRECTIONS, _ENTITIES, reduced=True
    )
    assert "Fair-on" in preamble
    assert "Theron" in preamble
    assert "Cor playing Kiki" not in preamble
    assert "High-fantasy" not in preamble


def test_check_token_budget_passes_under_limit() -> None:
    check_token_budget(
        "Hello world", model_context_window=128_000, budget_fraction=0.80
    )


def test_check_token_budget_raises_over_limit() -> None:
    large_preamble = "x" * 10_000  # ~2500 tokens at 4 chars/token
    with pytest.raises(TokenBudgetError):
        check_token_budget(
            large_preamble, model_context_window=1_000, budget_fraction=0.80
        )
