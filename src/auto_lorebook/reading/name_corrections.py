"""name_corrections rendering for reading.md frontmatter."""

from __future__ import annotations


def apply_name_corrections(
    text: str,
    name_corrections: dict[str, str],
) -> str:
    """Apply name corrections map (original → corrected) to text.

    :param text: input text
    :param name_corrections: {original: corrected} mapping from frontmatter
    :return: corrected text
    """
    for src, dst in name_corrections.items():
        if src:
            text = text.replace(src, dst)
    return text


def merge_with_globals(
    global_corrections: list[dict[str, str]],
    name_corrections: dict[str, str],
) -> dict[str, str]:
    """Merge global corrections with per-source name_corrections.

    Per-source wins on conflict.

    :param global_corrections: list of {from, to} dicts from corrections YAML
    :param name_corrections: per-source {from: to} dict from reading.md frontmatter
    :return: merged {from: to} dict; per-source overrides globals
    """
    merged: dict[str, str] = {}
    for c in global_corrections:
        src = c.get("from", "")
        dst = c.get("to", "")
        if src:
            merged[src] = dst
    merged.update(name_corrections)
    return merged
