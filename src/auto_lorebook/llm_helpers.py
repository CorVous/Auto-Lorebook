"""Shared helpers for LLM stage modules.

Holds the bits each stage re-uses: code-fence-tolerant JSON parsing and
preamble-plus-task-instructions system-prompt assembly.
"""

from __future__ import annotations

import json
import re
from typing import Any

CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(?P<body>.*?)\n```\s*$", re.DOTALL)


def parse_json_object(text: str, context: str) -> dict[str, Any]:
    """Parse an LLM response into a JSON object, tolerating ```json fences.

    :raises ValueError: body is not valid JSON or not an object.
    """
    raw = text.strip()
    m = CODE_FENCE_RE.match(raw)
    if m:
        raw = m.group("body").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        msg = f"{context}: response was not valid JSON: {e}"
        raise ValueError(msg) from e
    if not isinstance(parsed, dict):
        # ValueError (not TypeError) so callers catch one class for bad payloads
        msg = f"{context}: response must be a JSON object, got {type(parsed).__name__}"
        raise ValueError(msg)  # noqa: TRY004
    return parsed


def build_system_prompt(preamble_text: str, task_instructions: str) -> str:
    """Concat preamble (if non-empty) and task instructions with a separator."""
    if preamble_text.strip():
        return f"{preamble_text}\n\n---\n\n{task_instructions}"
    return task_instructions
