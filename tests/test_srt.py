"""Tests for SRT parser."""

from __future__ import annotations

import pytest

from auto_lorebook.sources.srt import (
    SrtCue,
    parse_srt,
    seconds_to_canonical,
    ts_to_seconds,
)

_SIMPLE_SRT = """\
1
00:00:00,000 --> 00:00:05,000
Hello world

2
00:00:05,000 --> 00:01:00,000
Second cue
"""

_CRLF_SRT = "1\r\n00:00:00,000 --> 00:00:05,000\r\nHello\r\n\r\n"

_MULTI_LINE_SRT = """\
1
00:00:00,000 --> 00:00:05,000
Line one
Line two

"""


def test_parse_srt_basic_count() -> None:
    """Two-cue SRT produces two SrtCue records."""
    cues = parse_srt(_SIMPLE_SRT)
    assert len(cues) == 2


def test_parse_srt_start_timestamps() -> None:
    """Start timestamps converted to canonical h:mm:ss."""
    cues = parse_srt(_SIMPLE_SRT)
    assert cues[0].start == "0:00:00"
    assert cues[1].start == "0:00:05"


def test_parse_srt_end_timestamps() -> None:
    """End timestamps converted to canonical h:mm:ss."""
    cues = parse_srt(_SIMPLE_SRT)
    assert cues[0].end == "0:00:05"
    assert cues[1].end == "0:01:00"


def test_parse_srt_text() -> None:
    """Cue text is extracted correctly."""
    cues = parse_srt(_SIMPLE_SRT)
    assert cues[0].text == "Hello world"
    assert cues[1].text == "Second cue"


def test_parse_srt_empty() -> None:
    """Empty input returns empty list."""
    assert parse_srt("") == []


def test_parse_srt_whitespace_only() -> None:
    """Whitespace-only input returns empty list."""
    assert parse_srt("   \n\n   ") == []


def test_parse_srt_extra_blank_lines() -> None:
    """Extra blank lines between cues are tolerated."""
    srt = (
        "1\n00:00:00,000 --> 00:00:05,000\nHello\n\n\n"
        "2\n00:00:05,000 --> 00:00:10,000\nWorld\n"
    )
    assert len(parse_srt(srt)) == 2


def test_parse_srt_crlf() -> None:
    """Windows CRLF line endings are handled."""
    cues = parse_srt(_CRLF_SRT)
    assert len(cues) == 1
    assert cues[0].text == "Hello"


def test_parse_srt_multi_line_text() -> None:
    """Multi-line cue text is joined with newlines."""
    cues = parse_srt(_MULTI_LINE_SRT)
    assert cues[0].text == "Line one\nLine two"


def test_parse_srt_seconds_values() -> None:
    """start_seconds and end_seconds are numeric."""
    cues = parse_srt(_SIMPLE_SRT)
    assert cues[0].start_seconds == pytest.approx(0.0)
    assert cues[0].end_seconds == pytest.approx(5.0)
    assert cues[1].end_seconds == pytest.approx(60.0)


def test_parse_srt_returns_srt_cue_instances() -> None:
    """parse_srt returns list of SrtCue."""
    cues = parse_srt(_SIMPLE_SRT)
    assert all(isinstance(c, SrtCue) for c in cues)


def test_ts_to_seconds_srt_format() -> None:
    """SRT-format timestamp HH:MM:SS,mmm converted correctly."""
    assert ts_to_seconds("00:01:30,000") == pytest.approx(90.0)


def test_ts_to_seconds_canonical() -> None:
    """Canonical h:mm:ss format converted correctly."""
    assert ts_to_seconds("1:23:45") == pytest.approx(5025.0)


def test_ts_to_seconds_zero() -> None:
    """Zero timestamp works."""
    assert ts_to_seconds("00:00:00,000") == pytest.approx(0.0)


def test_ts_to_seconds_invalid_raises() -> None:
    """Unrecognized timestamp format raises ValueError."""
    with pytest.raises(ValueError, match="unrecognized"):
        ts_to_seconds("not-a-timestamp")


def test_seconds_to_canonical_zero() -> None:
    """Zero seconds yields 0:00:00."""
    assert seconds_to_canonical(0) == "0:00:00"


def test_seconds_to_canonical_one_hour() -> None:
    """3661 seconds yields 1:01:01."""
    assert seconds_to_canonical(3661) == "1:01:01"


def test_seconds_to_canonical_minutes_padded() -> None:
    """Minutes and seconds are zero-padded to 2 digits."""
    assert seconds_to_canonical(65) == "0:01:05"
