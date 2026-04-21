"""Deterministic prompt preamble assembly."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

# chars-per-token estimate for budget check (conservative)
_CHARS_PER_TOKEN = 4


class TokenBudgetError(Exception):
    """Preamble exceeds configured token budget."""


def _format_speakers(speakers: list[dict[str, Any]]) -> str:
    lines = []
    for sp in speakers:
        parts = [sp.get("name", "")]
        if role := sp.get("role"):
            parts.append(f"role: {role}")
        if char := sp.get("character") or sp.get("usual_character"):
            parts.append(f"character: {char}")
        lines.append("  - " + ", ".join(parts))
    return "\n".join(lines)


def _format_entity_index(entities: list[dict[str, Any]]) -> str:
    """Group entities by category and render as indented list."""
    by_cat: dict[str, list[str]] = defaultdict(list)
    for ent in entities:
        name = ent.get("name", "")
        aliases = ent.get("aliases", [])
        if aliases:
            alias_str = ", ".join(str(a) for a in aliases)
            entry = f"{name} (aliases: {alias_str})"
        else:
            entry = name
        cat = str(ent.get("category", "other")).lower()
        by_cat[cat].append(entry)

    lines: list[str] = []
    for cat in sorted(by_cat):
        lines.append(f"{cat.capitalize()}:")
        lines.extend(f"  - {entry}" for entry in by_cat[cat])
    return "\n".join(lines)


def assemble_preamble(
    info_ctx: dict[str, Any],
    wiki_ctx: dict[str, Any],
    corrections: list[dict[str, Any]],
    entities: list[dict[str, Any]],
    *,
    reduced: bool = False,
) -> str:
    """Assemble context preamble for LLM stages.

    :param info_ctx: source context block from info.yaml
    :param wiki_ctx: .wiki-context.yaml contents
    :param corrections: list of correction records from .transcription-corrections.yaml
    :param entities: entity index records (name, category, aliases)
    :param reduced: if True, omit source context and setting (extractor mode)
    :return: formatted preamble string
    """
    parts: list[str] = []

    if not reduced:
        # --- source context ---
        src_lines: list[str] = []
        if perspective := info_ctx.get("perspective"):
            src_lines.append(f"Perspective: {perspective}")
        if nature := info_ctx.get("source_nature"):
            src_lines.append(f"Source nature: {nature}")
        if date := info_ctx.get("session_date"):
            src_lines.append(f"Session date: {date}")
        if speakers := info_ctx.get("speakers"):
            src_lines.append("Speakers:\n" + _format_speakers(speakers))
        if notes := info_ctx.get("notes"):
            src_lines.append(f"Notes: {notes}")
        if src_lines:
            parts.append("## Context for this source\n\n" + "\n".join(src_lines))

        # --- setting context ---
        setting_lines: list[str] = []
        setting = wiki_ctx.get("setting") or {}
        if sname := setting.get("name"):
            setting_lines.append(f"Setting: {sname}")
        if sdesc := setting.get("description"):
            setting_lines.append(sdesc)
        if naming := wiki_ctx.get("naming_conventions"):
            setting_lines.append(f"Naming conventions: {naming}")
        if interp := wiki_ctx.get("interpretation_defaults"):
            setting_lines.append(f"Interpretation defaults: {interp}")
        if rec_speakers := wiki_ctx.get("recurring_speakers"):
            setting_lines.append(
                "Recurring speakers:\n" + _format_speakers(rec_speakers)
            )
        if setting_lines:
            parts.append("## Setting context\n\n" + "\n".join(setting_lines))

    # --- corrections ---
    if corrections:
        corr_lines = [
            f'  "{c["from"]}" → "{c["to"]}"'
            for c in corrections
            if c.get("from") and c.get("to")
        ]
        if corr_lines:
            parts.append(
                "## Known transcription corrections\n\n" + "\n".join(corr_lines)
            )
    elif not reduced:
        parts.append("## Known transcription corrections\n\n(none)")

    # --- entity index ---
    index_body = _format_entity_index(entities) if entities else "(none)"
    parts.append("## Entities in this wiki\n\n" + index_body)

    return "\n\n".join(parts)


def check_token_budget(
    preamble: str,
    *,
    model_context_window: int,
    budget_fraction: float,
) -> None:
    """Raise TokenBudgetError if preamble exceeds budget.

    Uses chars-per-token heuristic (4 chars ≈ 1 token).

    :param preamble: assembled preamble string
    :param model_context_window: model's total context window in tokens
    :param budget_fraction: fraction of context window available for preamble
    :raises TokenBudgetError: preamble exceeds budget
    """
    estimated_tokens = len(preamble) / _CHARS_PER_TOKEN
    limit = model_context_window * budget_fraction
    if estimated_tokens > limit:
        msg = (
            f"Preamble too large: ~{estimated_tokens:.0f} estimated tokens"
            f" exceeds budget of {limit:.0f}"
            f" ({budget_fraction:.0%} of {model_context_window}-token window)."
            " Reduce .wiki-context.yaml, transcription corrections, or entity index,"
            " or increase preamble_budget_fraction in config."
        )
        raise TokenBudgetError(msg)
