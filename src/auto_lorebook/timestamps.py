"""Canonical timestamp handling.

Writers emit `h:mm:ss` (variable-width hour, zero-padded minutes/seconds).
Parser is lenient: accepts canonical form, leading-zero hours, `mm:ss`,
fractional seconds with `.` or SRT-style `,` decimal separators.
"""

from __future__ import annotations


class TimestampError(ValueError):
    """Raised for invalid timestamp input."""


def format_timestamp(seconds: float) -> str:
    """Format seconds as canonical `h:mm:ss`.

    :raises TimestampError: negative input
    """
    if seconds < 0:
        msg = f"negative timestamp: {seconds!r}"
        raise TimestampError(msg)
    whole = int(seconds)
    h, rem = divmod(whole, 3_600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def parse_timestamp(text: str) -> float:
    """Parse a lenient timestamp string to seconds.

    :raises TimestampError: empty, malformed, or negative
    """
    if not isinstance(text, str):
        msg = f"timestamp must be str, got {type(text).__name__}"
        raise TimestampError(msg)
    raw = text.strip()
    if not raw:
        msg = "empty timestamp"
        raise TimestampError(msg)

    # SRT-style comma decimal → dot
    normalized = raw.replace(",", ".")
    parts = normalized.split(":")
    if len(parts) not in {2, 3}:
        msg = f"expected h:mm:ss or mm:ss, got {text!r}"
        raise TimestampError(msg)

    try:
        if len(parts) == 3:
            h = int(parts[0])
            m = int(parts[1])
            s = float(parts[2])
        else:
            h = 0
            m = int(parts[0])
            s = float(parts[1])
    except ValueError as e:
        msg = f"malformed timestamp {text!r}: {e}"
        raise TimestampError(msg) from e

    if h < 0 or m < 0 or s < 0:
        msg = f"negative component in {text!r}"
        raise TimestampError(msg)

    return h * 3_600 + m * 60 + s
