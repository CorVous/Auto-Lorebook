"""Tests for reading.timestamps module."""

from __future__ import annotations

from auto_lorebook.reading.timestamps import apply_timestamp_links


def test_youtube_url_appends_t_param() -> None:
    text = "Theron spoke [0:05:32]"
    result = apply_timestamp_links(text, "https://youtube.com/watch?v=abc")
    # 5*60+32 = 332
    assert "[0:05:32](https://youtube.com/watch?v=abc&t=332s)" in result


def test_no_url_leaves_plain_bracketed_timestamp() -> None:
    text = "Theron spoke [0:05:32]"
    result = apply_timestamp_links(text, None)
    assert "[0:05:32]" in result
    assert "](http" not in result


def test_normalization_hour_preserved() -> None:
    text = "[0:05:30]"
    result = apply_timestamp_links(text, None)
    assert "[0:05:30]" in result


def test_multiple_timestamps_all_linkified() -> None:
    text = "A [0:01:00] B [0:02:00]"
    result = apply_timestamp_links(text, "https://yt.be/xyz")
    assert "t=60s" in result
    assert "t=120s" in result


def test_url_without_existing_query_uses_question_mark() -> None:
    result = apply_timestamp_links("[0:01:00]", "https://example.com/video")
    assert "?t=60s" in result


def test_url_with_existing_query_uses_ampersand() -> None:
    result = apply_timestamp_links("[0:01:00]", "https://youtube.com/watch?v=abc")
    assert "&t=60s" in result


def test_zero_seconds_timestamp() -> None:
    result = apply_timestamp_links("[0:00:00]", "https://youtube.com/watch?v=abc")
    assert "t=0s" in result


def test_hour_boundary_timestamp() -> None:
    result = apply_timestamp_links("[1:00:00]", "https://youtube.com/watch?v=abc")
    assert "t=3600s" in result
