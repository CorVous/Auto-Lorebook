"""reading.md frontmatter helpers and wiki-side write path.

Provides `linkify_timestamp`, `apply_name_corrections`, `read_frontmatter`,
and `write`. Assembly lives in reading_assembly.py.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\n(?P<body>.*?)\n---\n(?P<rest>.*)$", re.DOTALL)


class ReadingError(ValueError):
    """Raised for missing files, missing frontmatter, or invalid YAML."""


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
