"""SRT subtitle parser.

Produces a list of cues with start/end in seconds and text.
Tolerates BOM, CRLF, extra blank lines, non-integer indices.
Raises on missing `-->`, unparseable timestamps, or end-before-start.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook.timestamps import TimestampError, parse_timestamp

if TYPE_CHECKING:
    from pathlib import Path

_ARROW = "-->"


class SrtError(ValueError):
    """Raised for unrecoverable SRT parse errors."""


@dataclass(frozen=True)
class Cue:
    """One SRT cue. Times in seconds."""

    index: int
    start: float
    end: float
    text: str


def parse(text: str) -> list[Cue]:
    """Parse SRT text into cues."""
    # strip BOM, normalize line endings
    text = text.removeprefix("﻿")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []

    cues: list[Cue] = []
    # split on runs of blank lines
    blocks = _split_blocks(text)
    for raw_block in blocks:
        lines = raw_block.split("\n")
        if not lines:
            continue
        cue = _parse_block(lines, fallback_index=len(cues) + 1)
        cues.append(cue)
    return cues


def parse_file(path: Path) -> list[Cue]:
    """Read and parse an SRT file."""
    return parse(path.read_text(encoding="utf-8"))


def _split_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _parse_block(lines: list[str], *, fallback_index: int) -> Cue:
    # first line may be index (int) or timestamp line
    timestamp_idx = 0
    index = fallback_index
    if _ARROW not in lines[0]:
        # consume index line (may be non-integer; tolerate)
        raw = lines[0].strip()
        try:
            index = int(raw)
        except ValueError:
            index = fallback_index
        timestamp_idx = 1

    if timestamp_idx >= len(lines):
        msg = f"cue missing timestamp line: {lines!r}"
        raise SrtError(msg)

    ts_line = lines[timestamp_idx]
    if _ARROW not in ts_line:
        msg = f"cue missing '-->' separator: {ts_line!r}"
        raise SrtError(msg)

    left, _, right = ts_line.partition(_ARROW)
    try:
        start = parse_timestamp(left)
        end = parse_timestamp(right.split()[0] if right.split() else right)
    except TimestampError as e:
        msg = f"bad timestamp in {ts_line!r}: {e}"
        raise SrtError(msg) from e

    if end < start:
        msg = f"end before start in {ts_line!r}"
        raise SrtError(msg)

    body_lines = lines[timestamp_idx + 1 :]
    text = "\n".join(body_lines).strip()
    return Cue(index=index, start=start, end=end, text=text)
