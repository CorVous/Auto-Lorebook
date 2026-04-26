"""Tests for timestamps.py."""

from __future__ import annotations

import pytest

from auto_lorebook.timestamps import (
    TimestampError,
    format_timestamp,
    parse_locator_hint,
    parse_timestamp,
)


class TestFormat:
    def test_zero(self) -> None:
        assert format_timestamp(0) == "0:00:00"

    def test_seconds_only(self) -> None:
        assert format_timestamp(7) == "0:00:07"

    def test_minutes(self) -> None:
        assert format_timestamp(65) == "0:01:05"

    def test_hours(self) -> None:
        assert format_timestamp(3_661) == "1:01:01"

    def test_multi_digit_hours(self) -> None:
        assert format_timestamp(36_000) == "10:00:00"

    def test_float_truncates_to_whole_seconds(self) -> None:
        # canonical form drops sub-second precision
        assert format_timestamp(3_661.9) == "1:01:01"

    def test_rejects_negative(self) -> None:
        with pytest.raises(TimestampError):
            format_timestamp(-1)


class TestParse:
    def test_canonical(self) -> None:
        assert parse_timestamp("1:02:03") == pytest.approx(3_723.0)

    def test_zero_hour(self) -> None:
        assert parse_timestamp("0:00:07") == pytest.approx(7.0)

    def test_leading_zero_hour(self) -> None:
        assert parse_timestamp("01:02:03") == pytest.approx(3_723.0)

    def test_mm_ss_allowed(self) -> None:
        assert parse_timestamp("02:03") == pytest.approx(123.0)

    def test_fractional_seconds_dot(self) -> None:
        assert parse_timestamp("0:00:01.500") == pytest.approx(1.5)

    def test_srt_comma_decimal(self) -> None:
        # SRT uses comma as decimal separator
        assert parse_timestamp("00:00:01,500") == pytest.approx(1.5)

    def test_whitespace_tolerated(self) -> None:
        assert parse_timestamp("  1:02:03  ") == pytest.approx(3_723.0)

    def test_empty_raises(self) -> None:
        with pytest.raises(TimestampError):
            parse_timestamp("")

    def test_garbage_raises(self) -> None:
        with pytest.raises(TimestampError):
            parse_timestamp("not a time")

    def test_too_many_parts_raises(self) -> None:
        with pytest.raises(TimestampError):
            parse_timestamp("1:2:3:4")

    def test_negative_component_raises(self) -> None:
        with pytest.raises(TimestampError):
            parse_timestamp("-1:00:00")


class TestRoundTrip:
    def test_canonical_round_trip(self) -> None:
        for s in (0, 7, 65, 3_661, 36_000):
            assert parse_timestamp(format_timestamp(s)) == pytest.approx(float(s))


class TestParseLocatorHint:
    def test_canonical_range(self) -> None:
        start, end = parse_locator_hint("0:04:25-0:04:50")
        assert start == pytest.approx(265.0)
        assert end == pytest.approx(290.0)

    def test_lenient_components(self) -> None:
        start, end = parse_locator_hint("4:25-4:50")
        assert start == pytest.approx(265.0)
        assert end == pytest.approx(290.0)

    def test_whitespace_around_range(self) -> None:
        start, end = parse_locator_hint("  0:00:01 - 0:00:02  ")
        assert start == pytest.approx(1.0)
        assert end == pytest.approx(2.0)

    def test_missing_dash_raises(self) -> None:
        with pytest.raises(TimestampError):
            parse_locator_hint("0:04:25")

    def test_empty_raises(self) -> None:
        with pytest.raises(TimestampError):
            parse_locator_hint("")

    def test_end_before_start_raises(self) -> None:
        with pytest.raises(TimestampError):
            parse_locator_hint("0:00:10-0:00:05")
