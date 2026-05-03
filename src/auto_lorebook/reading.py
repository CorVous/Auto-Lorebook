"""reading.md frontmatter helpers and wiki-side write path.

Provides `linkify_timestamp`, `apply_name_corrections`, `read_frontmatter`,
`with_status`, `set_status`, and `write` — used by commands/review.py and
the wiki-side write path. Assembly is now in reading_assembly.py.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path

VALID_STATUSES = frozenset({"draft", "approved"})

_FRONTMATTER_RE = re.compile(r"^---\n(?P<body>.*?)\n---\n(?P<rest>.*)$", re.DOTALL)


class ReadingError(ValueError):
    """Raised for missing files, missing frontmatter, or invalid status."""


def linkify_timestamp(source_url: str | None, seconds: float) -> str | None:
    """Return a URL with a `t=` query for the given timestamp, or None."""
    if not source_url:
        return None
    sep = "&" if "?" in source_url else "?"
    return f"{source_url}{sep}t={int(seconds)}"


def apply_name_corrections(text: str, name_corrections: dict[str, str]) -> str:
    """Literally substitute each wrong→right mapping in text."""
    out = text
    for wrong, right in name_corrections.items():
        out = out.replace(wrong, right)
    return out


def write(path: Path, text: str) -> None:
    """Atomically write reading.md."""
    atomic_write_text(path, text)


def read_frontmatter(path: Path) -> dict[str, Any]:
    """Parse the YAML frontmatter block of a reading.md file.

    :raises ReadingError: missing file, missing frontmatter fence, or
        invalid YAML.
    """
    if not path.exists():
        msg = f"{path}: file not found"
        raise ReadingError(msg)
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        msg = f"{path}: no '---' frontmatter block at top of file"
        raise ReadingError(msg)
    try:
        parsed = yaml.safe_load(m.group("body"))
    except yaml.YAMLError as e:
        msg = f"{path}: frontmatter YAML parse error: {e}"
        raise ReadingError(msg) from e
    if not isinstance(parsed, dict):
        msg = f"{path}: frontmatter is not a mapping"
        raise ReadingError(msg)
    return parsed


def with_status(path: Path, status: str) -> str:
    """Return the reading.md text with `reading_status` set to `status`.

    :raises FileNotFoundError: path doesn't exist
    :raises ReadingError: invalid status, missing/malformed frontmatter
    """
    if status not in VALID_STATUSES:
        msg = (
            f"invalid reading_status {status!r}; "
            f"expected one of {sorted(VALID_STATUSES)}"
        )
        raise ReadingError(msg)
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        msg = f"{path}: no frontmatter block"
        raise ReadingError(msg)
    parsed = yaml.safe_load(m.group("body")) or {}
    if not isinstance(parsed, dict):
        msg = f"{path}: frontmatter is not a mapping"
        raise ReadingError(msg)
    parsed["reading_status"] = status
    new_fm = yaml.safe_dump(
        parsed, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).rstrip("\n")
    return f"---\n{new_fm}\n---\n{m.group('rest')}"


def set_status(path: Path, status: str) -> None:
    """Rewrite the `reading_status` field in place."""
    atomic_write_text(path, with_status(path, status))
