"""Transcript correction application and merging."""

from __future__ import annotations


def apply_corrections(
    text: str,
    corrections: list[dict[str, str]],
) -> str:
    """Apply transcription corrections as string substitutions.

    Duplicate 'from' keys are deduplicated first; later entries win.

    :param text: input text
    :param corrections: ordered list of {from, to} dicts
    :return: corrected text
    """
    # Deduplicate by 'from' key so later entries win before applying
    deduped: dict[str, str] = {}
    for corr in corrections:
        src = corr.get("from", "")
        dst = corr.get("to", "")
        if src:
            deduped[src] = dst
    for src, dst in deduped.items():
        text = text.replace(src, dst)
    return text


def merge_corrections(
    global_corrections: list[dict[str, str]],
    source_corrections: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Merge global and per-source corrections; per-source wins on conflict.

    :param global_corrections: from .transcription-corrections.yaml
    :param source_corrections: per-source overrides
    :return: merged list, source corrections override globals on conflict
    """
    merged: dict[str, str] = {}
    for c in global_corrections:
        src = c.get("from", "")
        dst = c.get("to", "")
        if src:
            merged[src] = dst
    for c in source_corrections:
        src = c.get("from", "")
        dst = c.get("to", "")
        if src:
            merged[src] = dst
    return [{"from": k, "to": v} for k, v in merged.items()]
