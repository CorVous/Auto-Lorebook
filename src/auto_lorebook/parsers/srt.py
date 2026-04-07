"""SRT subtitle file parser.

Parses ``.srt`` subtitle files, extracts dialogue/narration with
timestamps, and exposes the results as structured dataclasses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Matches a timestamp arrow line: "00:00:01,000 --> 00:00:04,000"
_TIMESTAMP_RE = re.compile(
    r"^(?P<start>\d{2}:\d{2}:\d{2},\d{3})"
    r"\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})"
)

# Matches a single SRT timestamp token: "HH:MM:SS,mmm"
_TOKEN_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")


def parse_timestamp(value: str) -> float:
    """Convert an SRT timestamp string to seconds.

    :param value: Timestamp in ``HH:MM:SS,mmm`` format.
    :return: Time in seconds (including fractional milliseconds).
    :raises ValueError: If *value* does not match the expected format.
    """
    m = _TOKEN_RE.match(value.strip())
    if not m:
        msg = f"Invalid timestamp format: {value!r}"
        raise ValueError(msg)
    hours, minutes, seconds, millis = (int(g) for g in m.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def seconds_to_timestamp(total_seconds: float) -> str:
    """Convert a float number of seconds to an SRT timestamp string.

    :param total_seconds: Non-negative number of seconds.
    :return: Timestamp in ``HH:MM:SS,mmm`` format.
    """
    total_ms = round(total_seconds * 1000)
    millis = total_ms % 1000
    total_s = total_ms // 1000
    seconds = total_s % 60
    total_m = total_s // 60
    minutes = total_m % 60
    hours = total_m // 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


@dataclass(frozen=True)
class SubtitleBlock:
    """A single subtitle block from an SRT file.

    :param sequence: 1-based sequence number from the SRT file.
    :param start: Start time in seconds.
    :param end: End time in seconds.
    :param text: Subtitle text (multi-line joined with a space).
    """

    sequence: int
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        """Return the duration of this block in seconds."""
        return self.end - self.start


@dataclass
class ParsedSRT:
    """The result of parsing an SRT file.

    :param blocks: Subtitle blocks in sequence order.
    """

    blocks: list[SubtitleBlock]


def parse_srt(content: str) -> ParsedSRT:
    r"""Parse raw SRT file content into a :class:`ParsedSRT`.

    Handles both Unix (``\n``) and Windows (``\r\n``) line endings.
    Multi-line subtitle text is joined with a single space.

    :param content: Raw string content of an ``.srt`` file.
    :return: Parsed SRT with all subtitle blocks in order.
    """
    # Normalise line endings
    normalised = content.replace("\r\n", "\n").replace("\r", "\n")

    blocks: list[SubtitleBlock] = []

    # Split on blank lines to get individual subtitle records
    for record in re.split(r"\n{2,}", normalised.strip()):
        lines = [ln.strip() for ln in record.splitlines() if ln.strip()]
        if not lines:
            continue

        # First line should be the sequence number
        try:
            seq = int(lines[0])
        except ValueError:
            continue

        if len(lines) < 2:
            continue

        # Second line should be the timestamp arrow
        ts_match = _TIMESTAMP_RE.match(lines[1])
        if not ts_match:
            continue

        start = parse_timestamp(ts_match.group("start"))
        end = parse_timestamp(ts_match.group("end"))

        # Remaining lines are the subtitle text
        text = " ".join(lines[2:])

        blocks.append(SubtitleBlock(sequence=seq, start=start, end=end, text=text))

    # Ensure sequence order
    blocks.sort(key=lambda b: b.sequence)

    return ParsedSRT(blocks=blocks)
