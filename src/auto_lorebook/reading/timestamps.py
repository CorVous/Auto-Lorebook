"""Clickable timestamp post-processing for reading.md."""

from __future__ import annotations

import re

_TS_RE = re.compile(r"\[(\d+:\d{2}:\d{2})\]")


def _normalize_ts(ts: str) -> str:
    """Normalize to h:mm:ss (strips any redundant leading zeros on the hour)."""
    parts = ts.split(":")
    h = int(parts[0])
    m = int(parts[1])
    s = int(parts[2])
    return f"{h}:{m:02d}:{s:02d}"


def _ts_to_seconds_int(ts: str) -> int:
    """Convert normalized h:mm:ss to integer seconds."""
    parts = ts.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def _url_with_t(source_url: str, seconds: int) -> str:
    """Append ?t=Ns or &t=Ns to source_url."""
    sep = "&" if "?" in source_url else "?"
    return f"{source_url}{sep}t={seconds}s"


def apply_timestamp_links(text: str, source_url: str | None) -> str:
    """Convert [h:mm:ss] plain timestamps to markdown links.

    For sources without a URL, timestamps are normalized but not linked.

    :param text: markdown text containing [h:mm:ss] anchors
    :param source_url: source URL for link construction; None = plain text
    :return: text with linkified (or normalized) timestamps
    """

    def _replace(m: re.Match[str]) -> str:
        ts = _normalize_ts(m.group(1))
        if source_url is None:
            return f"[{ts}]"
        url = _url_with_t(source_url, _ts_to_seconds_int(ts))
        return f"[{ts}]({url})"

    return _TS_RE.sub(_replace, text)
