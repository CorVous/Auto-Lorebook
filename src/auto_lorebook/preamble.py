"""Preamble assembly and token budget check."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auto_lorebook.corrections import Corrections
    from auto_lorebook.entity_index import EntityIndex
    from auto_lorebook.info_yaml import Info
    from auto_lorebook.wiki_context import WikiContext

_SEC_SOURCE = "Context for this source"
_SEC_SETTING = "Setting context"
_SEC_CORRECTIONS = "Known transcription corrections"
_SEC_ENTITIES = "Entities in this wiki"


class PreambleTooLargeError(Exception):
    """Preamble exceeds the configured token budget.

    :param largest_section: name of the section with the most characters
    :param tokens_approx: approximate token count of the full preamble
    :param budget: maximum allowed token count
    """

    def __init__(self, largest_section: str, tokens_approx: int, budget: int) -> None:
        self.largest_section = largest_section
        self.tokens_approx = tokens_approx
        self.budget = budget
        super().__init__(
            f"Preamble too large (~{tokens_approx} tokens, budget {budget}). "
            f"Largest section: '{largest_section}'. Remedies:\n"
            "  1. Switch to a larger-context model in config.yaml.\n"
            "  2. Trim the named component "
            "(e.g. .wiki-context.yaml, transcription corrections).\n"
            "  3. Enable retrieval mode for the entity index (deferred)."
        )


@dataclass
class AssembledPreamble:
    """Result of preamble assembly."""

    text: str
    sections: dict[str, str]

    def check_budget(self, context_window: int, budget_fraction: float) -> None:
        """Raise PreambleTooLargeError if preamble exceeds the token budget.

        :param context_window: model's context window in tokens
        :param budget_fraction: fraction of context window allowed for preamble
        """
        tokens_approx = len(self.text) // 4
        budget = int(context_window * budget_fraction)
        if tokens_approx > budget:
            largest = max(self.sections, key=lambda k: len(self.sections[k]))
            raise PreambleTooLargeError(
                largest_section=largest,
                tokens_approx=tokens_approx,
                budget=budget,
            )


def _render_source_context(info: Info) -> str:
    ctx = info.context
    parts: dict[str, str] = {}
    if ctx.notes:
        parts["notes"] = ctx.notes
    if ctx.perspective:
        parts["perspective"] = ctx.perspective
    if info.session_date:
        parts["session_date"] = info.session_date
    if ctx.source_nature:
        parts["source_nature"] = ctx.source_nature
    if ctx.speakers:
        lines = ["speakers:"]
        for sp in sorted(ctx.speakers, key=lambda s: s.get("name", "")):
            items = ", ".join(f"{k}: {v}" for k, v in sorted(sp.items()))
            lines.append(f"  - {items}")
        parts["speakers"] = "\n".join(lines)

    if not parts:
        return ""
    return "\n".join(
        f"{k}: {v}" if k != "speakers" else v for k, v in sorted(parts.items())
    )


def _render_setting_context(wiki_context: WikiContext) -> str:
    parts: dict[str, str] = {}
    wc = wiki_context
    if wc.setting.description:
        parts["description"] = wc.setting.description.rstrip()
    if wc.interpretation_defaults:
        parts["interpretation_defaults"] = wc.interpretation_defaults.rstrip()
    if wc.setting.name:
        parts["name"] = wc.setting.name
    if wc.naming_conventions:
        parts["naming_conventions"] = wc.naming_conventions.rstrip()
    if wc.recurring_speakers:
        lines = ["recurring_speakers:"]
        for sp in sorted(wc.recurring_speakers, key=lambda s: s.get("name", "")):
            items = ", ".join(f"{k}: {v}" for k, v in sorted(sp.items()))
            lines.append(f"  - {items}")
        parts["recurring_speakers"] = "\n".join(lines)

    if not parts:
        return ""
    return "\n".join(
        f"{k}: {v}" if k != "recurring_speakers" else v
        for k, v in sorted(parts.items())
    )


def _render_corrections(corrections: Corrections) -> str:
    if not corrections.corrections:
        return "(none)"
    lines = sorted(f"{c.wrong} → {c.right}" for c in corrections.corrections)
    return "\n".join(lines)


def _render_entities(entity_index: EntityIndex) -> str:
    return entity_index.render_for_preamble()


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body}"


def assemble(
    info: Info,
    wiki_context: WikiContext,
    corrections: Corrections,
    entity_index: EntityIndex,
    *,
    reduced: bool,
) -> AssembledPreamble:
    """Assemble a deterministic preamble string.

    :param reduced: if True, emit only corrections + entity sections
                    (for the extractor stage)
    """
    sections: dict[str, str] = {}

    if not reduced:
        src_body = _render_source_context(info)
        sections[_SEC_SOURCE] = src_body or "(none)"

        setting_body = _render_setting_context(wiki_context)
        sections[_SEC_SETTING] = setting_body or "(none)"

    sections[_SEC_CORRECTIONS] = _render_corrections(corrections)
    sections[_SEC_ENTITIES] = _render_entities(entity_index)

    # Emit sections in spec order
    if reduced:
        order = [_SEC_CORRECTIONS, _SEC_ENTITIES]
    else:
        order = [_SEC_SOURCE, _SEC_SETTING, _SEC_CORRECTIONS, _SEC_ENTITIES]

    parts = [_section(title, sections[title]) for title in order]
    text = "\n\n".join(parts)

    return AssembledPreamble(text=text, sections=sections)
