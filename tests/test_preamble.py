"""Tests for preamble.py."""

from __future__ import annotations

import pytest

from auto_lorebook.corrections import Correction, Corrections
from auto_lorebook.entity_index import EntityEntry, EntityIndex
from auto_lorebook.info_yaml import Info, SourceContext
from auto_lorebook.preamble import (
    PreambleTooLargeError,
    assemble,
)
from auto_lorebook.wiki_context import SettingContext, WikiContext


def _info(
    perspective: str | None = "Cor playing Kiki",
    source_nature: str | None = "actual-play",
    session_date: str | None = "2026-01-15",
    notes: str | None = None,
) -> Info:
    return Info(
        source_id="txt-abc1234567",
        source_type="text",
        fetched_at="2026-04-24T00:00:00Z",
        session_date=session_date,
        context=SourceContext(
            perspective=perspective,
            source_nature=source_nature,
            notes=notes,
        ),
    )


def _wiki_context(name: str | None = "Aether Chronicles") -> WikiContext:
    return WikiContext(
        setting=SettingContext(name=name, description="A high-fantasy setting."),
        naming_conventions="Characters by first name",
    )


def _corrections(pairs: list[tuple[str, str]] | None = None) -> Corrections:
    if pairs is None:
        return Corrections()
    return Corrections(corrections=[Correction(wrong=w, right=r) for w, r in pairs])


def _index(*entities: tuple[str, str]) -> EntityIndex:
    """Build an EntityIndex from (entity_name, category) tuples."""
    entries = [EntityEntry(entity=e, category=c, slug=e.lower()) for e, c in entities]
    return EntityIndex(entries)


# ---------------------------------------------------------------------------
# Full preamble (reduced=False)
# ---------------------------------------------------------------------------


def test_full_preamble_has_four_sections() -> None:
    p = assemble(_info(), _wiki_context(), _corrections(), _index(), reduced=False)
    assert "## Context for this source" in p.text
    assert "## Setting context" in p.text
    assert "## Known transcription corrections" in p.text
    assert "## Entities in this wiki" in p.text


def test_full_preamble_section_order() -> None:
    p = assemble(_info(), _wiki_context(), _corrections(), _index(), reduced=False)
    pos_source = p.text.index("## Context for this source")
    pos_setting = p.text.index("## Setting context")
    pos_corrections = p.text.index("## Known transcription corrections")
    pos_entities = p.text.index("## Entities in this wiki")
    assert pos_source < pos_setting < pos_corrections < pos_entities


# ---------------------------------------------------------------------------
# Reduced preamble (reduced=True)
# ---------------------------------------------------------------------------


def test_reduced_preamble_omits_source_context() -> None:
    p = assemble(_info(), _wiki_context(), _corrections(), _index(), reduced=True)
    assert "## Context for this source" not in p.text


def test_reduced_preamble_omits_setting_context() -> None:
    p = assemble(_info(), _wiki_context(), _corrections(), _index(), reduced=True)
    assert "## Setting context" not in p.text


def test_reduced_preamble_has_corrections_and_entities() -> None:
    p = assemble(_info(), _wiki_context(), _corrections(), _index(), reduced=True)
    assert "## Known transcription corrections" in p.text
    assert "## Entities in this wiki" in p.text


# ---------------------------------------------------------------------------
# Content checks
# ---------------------------------------------------------------------------


def test_corrections_rendered() -> None:
    cors = _corrections([("Aldera", "Aldara"), ("Valore", "Valoria")])
    p = assemble(_info(), _wiki_context(), cors, _index(), reduced=False)
    assert "Aldera → Aldara" in p.text
    assert "Valore → Valoria" in p.text


def test_no_corrections_shows_none() -> None:
    p = assemble(_info(), _wiki_context(), _corrections(), _index(), reduced=False)
    assert "(none)" in p.text


def test_entity_rendered() -> None:
    idx = _index(("Theron", "characters"), ("Aldara", "locations"))
    p = assemble(_info(), _wiki_context(), _corrections(), idx, reduced=False)
    assert "Theron" in p.text
    assert "Aldara" in p.text


def test_perspective_in_source_section() -> None:
    p = assemble(
        _info(perspective="Cor playing Kiki"),
        _wiki_context(),
        _corrections(),
        _index(),
        reduced=False,
    )
    assert "Cor playing Kiki" in p.text


def test_setting_name_in_setting_section() -> None:
    p = assemble(
        _info(),
        _wiki_context(name="My Setting"),
        _corrections(),
        _index(),
        reduced=False,
    )
    assert "My Setting" in p.text


# ---------------------------------------------------------------------------
# Sections dict
# ---------------------------------------------------------------------------


def test_sections_dict_keys_full() -> None:
    p = assemble(_info(), _wiki_context(), _corrections(), _index(), reduced=False)
    assert "Context for this source" in p.sections
    assert "Setting context" in p.sections
    assert "Known transcription corrections" in p.sections
    assert "Entities in this wiki" in p.sections


def test_sections_dict_keys_reduced() -> None:
    p = assemble(_info(), _wiki_context(), _corrections(), _index(), reduced=True)
    assert "Context for this source" not in p.sections
    assert "Setting context" not in p.sections
    assert "Known transcription corrections" in p.sections
    assert "Entities in this wiki" in p.sections


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_identical_inputs_produce_identical_output() -> None:
    cors = _corrections([("Aldera", "Aldara")])
    idx = _index(("Theron", "characters"))
    p1 = assemble(_info(), _wiki_context(), cors, idx, reduced=False)
    p2 = assemble(_info(), _wiki_context(), cors, idx, reduced=False)
    assert p1.text == p2.text


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------


def test_budget_ok_does_not_raise() -> None:
    p = assemble(_info(), _wiki_context(), _corrections(), _index(), reduced=False)
    p.check_budget(context_window=200_000, budget_fraction=0.8)  # should not raise


def test_budget_exceeded_raises() -> None:
    # create a preamble with lots of text by stuffing the wiki context
    big_text = "x" * 100_000
    wc = WikiContext(
        setting=SettingContext(description=big_text),
        naming_conventions=big_text,
    )
    p = assemble(_info(), wc, _corrections(), _index(), reduced=False)
    with pytest.raises(PreambleTooLargeError):
        p.check_budget(context_window=1000, budget_fraction=0.8)


def test_budget_error_names_largest_section() -> None:
    big_text = "y" * 200_000
    wc = WikiContext(setting=SettingContext(description=big_text))
    p = assemble(_info(), wc, _corrections(), _index(), reduced=False)
    with pytest.raises(PreambleTooLargeError) as exc_info:
        p.check_budget(context_window=1000, budget_fraction=0.8)
    assert exc_info.value.largest_section == "Setting context"
