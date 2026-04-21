"""Tests for reading.assembly module."""

from __future__ import annotations

from auto_lorebook.pipeline.stage1b import Bullet, SegmentSummary
from auto_lorebook.reading.assembly import assemble_reading_md
from auto_lorebook.sources.info_yaml import ContextBlock, InfoYaml


def _make_info(
    title: str = "Test Source",
    source_url: str | None = "https://youtube.com/watch?v=abc",
) -> InfoYaml:
    return InfoYaml(
        schema_version=1,
        source_id="src-001",
        source_type="youtube",
        source_url=source_url,
        title=title,
        duration_seconds=900.0,
        caption_type="auto",
        fetched_at="2026-04-21T00:00:00Z",
        session_date="2026-04-20",
        context=ContextBlock(
            perspective=None,
            source_nature=None,
            setting=None,
            speakers=[],
            notes=None,
        ),
    )


_STRUCTURE: dict[str, object] = {
    "default_speaker": "DM",
    "segments": [
        {
            "id": "seg-001",
            "start": "0:00:00",
            "end": "0:07:00",
            "title": "Opening",
            "speaker": None,
            "overrides": [],
        },
        {
            "id": "seg-002",
            "start": "0:07:00",
            "end": "0:15:00",
            "title": "The Conflict",
            "speaker": None,
            "overrides": [],
        },
    ],
    "uncertainty_flags": [
        {"locator": "0:03:00", "description": "unclear speaker"},
    ],
}


def test_frontmatter_source_id_present() -> None:
    content = assemble_reading_md(
        _make_info(), _STRUCTURE, [], ingested_at="2026-04-21T00:00:00Z"
    )
    assert "source_id: src-001" in content


def test_frontmatter_reading_status_draft() -> None:
    content = assemble_reading_md(
        _make_info(), _STRUCTURE, [], ingested_at="2026-04-21T00:00:00Z"
    )
    assert "reading_status: draft" in content


def test_frontmatter_name_corrections_empty() -> None:
    content = assemble_reading_md(
        _make_info(), _STRUCTURE, [], ingested_at="2026-04-21T00:00:00Z"
    )
    assert "name_corrections:" in content


def test_segment_headers_present() -> None:
    content = assemble_reading_md(
        _make_info(), _STRUCTURE, [], ingested_at="2026-04-21T00:00:00Z"
    )
    assert "Opening" in content
    assert "The Conflict" in content


def test_segment_headers_include_timestamp() -> None:
    content = assemble_reading_md(
        _make_info(), _STRUCTURE, [], ingested_at="2026-04-21T00:00:00Z"
    )
    assert "[0:00:00]" in content
    assert "[0:07:00]" in content


def test_empty_segment_marker_shown() -> None:
    content = assemble_reading_md(
        _make_info(), _STRUCTURE, [], ingested_at="2026-04-21T00:00:00Z"
    )
    assert "_No claims extracted from this segment._" in content


def test_bullets_rendered() -> None:
    summaries = [
        SegmentSummary(
            segment_id="seg-001",
            bullets=[
                Bullet(
                    text="Theron spoke",
                    anchor="0:03:00",
                    locator_hint=("0:02:45", "0:03:15"),
                )
            ],
        ),
        SegmentSummary(segment_id="seg-002", bullets=[]),
    ]
    content = assemble_reading_md(
        _make_info(), _STRUCTURE, summaries, ingested_at="2026-04-21T00:00:00Z"
    )
    assert "Theron spoke" in content
    assert "[0:03:00]" in content


def test_uncertainty_flag_rendered_in_correct_segment() -> None:
    content = assemble_reading_md(
        _make_info(), _STRUCTURE, [], ingested_at="2026-04-21T00:00:00Z"
    )
    assert "unclear speaker" in content
    assert "**Uncertainty:**" in content
