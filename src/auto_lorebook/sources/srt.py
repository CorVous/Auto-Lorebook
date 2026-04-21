"""SRT subtitle parser."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TS_PATTERN = re.compile(r"(\d+):(\d{2}):(\d{2})[\.,]\d+")
_ARROW_PATTERN = re.compile(
    r"(\d+:\d{2}:\d{2}[\.,]\d+)\s+-->\s+(\d+:\d{2}:\d{2}[\.,]\d+)"
)
_BLOCK_SEP = re.compile(r"\r?\n\r?\n")


@dataclass(slots=True)
class SrtCue:
    """Single SRT subtitle cue."""

    index: int
    start: str  # canonical h:mm:ss
    end: str  # canonical h:mm:ss
    start_seconds: float
    end_seconds: float
    text: str


def ts_to_seconds(ts: str) -> float:
    """Convert SRT timestamp (HH:MM:SS,mmm) or canonical (h:mm:ss) to seconds.

    :param ts: timestamp string
    :raises ValueError: unrecognized format
    """
    m = _TS_PATTERN.match(ts)
    if m:
        return int(m.group(1)) * 3600.0 + int(m.group(2)) * 60.0 + int(m.group(3))
    parts = ts.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600.0 + int(parts[1]) * 60.0 + float(parts[2])
    msg = f"unrecognized timestamp: {ts!r}"
    raise ValueError(msg)


def seconds_to_canonical(seconds: float) -> str:
    """Convert total seconds to canonical h:mm:ss format."""
    total = int(seconds)
    h = total // 3600
    mn = (total % 3600) // 60
    s = total % 60
    return f"{h}:{mn:02d}:{s:02d}"


def parse_srt(text: str) -> list[SrtCue]:
    """Parse SRT text into a list of SrtCue records.

    :param text: raw SRT content
    :return: ordered list of cues; empty if text is blank
    """
    cues: list[SrtCue] = []
    blocks = _BLOCK_SEP.split(text.strip())
    for raw_block in blocks:
        block = raw_block.strip()
        if not block:
            continue
        lines = block.splitlines()
        if len(lines) < 2:
            continue
        # optional index line
        idx = 1
        try:
            cue_index = int(lines[0].strip())
        except ValueError:
            cue_index = len(cues) + 1
            idx = 0
        if idx >= len(lines):
            continue
        arrow = _ARROW_PATTERN.match(lines[idx])
        if not arrow:
            continue
        start_ts = arrow.group(1)
        end_ts = arrow.group(2)
        cue_text = "\n".join(lines[idx + 1 :]).strip()
        cues.append(
            SrtCue(
                index=cue_index,
                start=seconds_to_canonical(ts_to_seconds(start_ts)),
                end=seconds_to_canonical(ts_to_seconds(end_ts)),
                start_seconds=ts_to_seconds(start_ts),
                end_seconds=ts_to_seconds(end_ts),
                text=cue_text,
            )
        )
    return cues
