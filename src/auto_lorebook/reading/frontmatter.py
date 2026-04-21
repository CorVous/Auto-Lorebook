"""YAML frontmatter reader/writer for reading.md."""

from __future__ import annotations

from typing import cast

import yaml

_FENCE = "---"


def split_frontmatter(content: str) -> tuple[dict[str, object], str]:
    """Split content into (frontmatter_dict, body).

    Expects content starting with a '---' fence.
    Returns ({}, content) if no valid frontmatter found.
    """
    prefix = _FENCE + "\n"
    if not content.startswith(prefix):
        return {}, content
    end = content.find("\n" + _FENCE, len(prefix) - 1)
    if end == -1:
        return {}, content
    fm_text = content[len(prefix) : end]
    body = content[end + len("\n" + _FENCE) :].lstrip("\n")
    raw = yaml.safe_load(fm_text)
    return cast("dict[str, object]", raw or {}), body


def join_frontmatter(frontmatter: dict[str, object], body: str) -> str:
    """Combine frontmatter dict and body into reading.md content string."""
    fm_text = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True)
    return f"{_FENCE}\n{fm_text}{_FENCE}\n{body}"
