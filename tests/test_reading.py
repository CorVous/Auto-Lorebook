"""Tests for reading.py — reading.md assembly + frontmatter helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from auto_lorebook.info_yaml import Info, SourceContext
from auto_lorebook.reading import (
    ReadingError,
    apply_name_corrections,
    assemble,
    linkify_timestamp,
    read_frontmatter,
    set_status,
    write,
)
from auto_lorebook.stage1b import Bullet, ReadingBullets
from auto_lorebook.structure import Segment, Structure, UncertaintyFlag

if TYPE_CHECKING:
    from pathlib import Path


def _info(
    *,
    source_url: str | None = "https://youtube.com/watch?v=abc12345678",
    title: str | None = "Session 3",
) -> Info:
    return Info(
        source_id="yt-abc12345678",
        source_type="youtube",
        fetched_at="2026-04-20T14:35:12Z",
        source_url=source_url,
        title=title,
        duration_seconds=600,
        transcript_filename="transcript.en.srt",
        context=SourceContext(),
    )


def _structure() -> Structure:
    return Structure(
        source_id="yt-abc12345678",
        generated_at="2026-04-20T14:32:00Z",
        default_speaker="DM",
        segments=[
            Segment(
                id="seg-001",
                start=0.0,
                end=120.0,
                title="Introduction",
                speaker="DM",
            ),
            Segment(
                id="seg-002",
                start=120.0,
                end=270.0,
                title="Rules discussion: grappling",
                speaker="mixed",
                notes="off-topic",
            ),
            Segment(
                id="seg-003",
                start=270.0,
                end=600.0,
                title="Founding of Aldara",
                speaker="DM",
            ),
        ],
        uncertainty_flags=[
            UncertaintyFlag(
                locator=347.0,
                span="a place name",
                kind="name",
                note="unclear",
            )
        ],
    )


def _bullets() -> ReadingBullets:
    return ReadingBullets(
        source_id="yt-abc12345678",
        generated_at="2026-04-20T14:34:00Z",
        segments={
            "seg-001": [],  # empty is permitted
            "seg-002": [],
            "seg-003": [
                Bullet(
                    text="King Theron founded Aldara in the Second Age",
                    anchor=272.0,
                    locator_hint_start=257.0,
                    locator_hint_end=287.0,
                ),
                Bullet(
                    text="The founding displaced an earlier elven presence",
                    anchor=314.0,
                    locator_hint_start=299.0,
                    locator_hint_end=329.0,
                ),
            ],
        },
    )


class TestLinkifyTimestamp:
    def test_youtube_watch_url(self) -> None:
        assert (
            linkify_timestamp("https://youtube.com/watch?v=abc", 270)
            == "https://youtube.com/watch?v=abc&t=270"
        )

    def test_youtube_short_url(self) -> None:
        assert (
            linkify_timestamp("https://youtu.be/abc", 270)
            == "https://youtu.be/abc?t=270"
        )

    def test_no_url_returns_none(self) -> None:
        assert linkify_timestamp(None, 270) is None

    def test_preserves_existing_query(self) -> None:
        got = linkify_timestamp("https://example.com/path?x=1", 5)
        assert got == "https://example.com/path?x=1&t=5"


class TestApplyNameCorrections:
    def test_empty_returns_unchanged(self) -> None:
        assert apply_name_corrections("King Fair-on", {}) == "King Fair-on"

    def test_substitutes_literally(self) -> None:
        got = apply_name_corrections("King Fair-on", {"Fair-on": "Theron"})
        assert got == "King Theron"

    def test_handles_multiple(self) -> None:
        got = apply_name_corrections("A says B", {"A": "Alice", "B": "Bob"})
        assert got == "Alice says Bob"


class TestAssemble:
    def test_frontmatter_contains_expected_keys(self) -> None:
        md = assemble(info=_info(), structure=_structure(), bullets=_bullets())
        assert md.startswith("---\n")
        assert "schema_version: 1" in md
        assert "source_id: yt-abc12345678" in md
        assert "reading_status: draft" in md
        assert "default_speaker: DM" in md

    def test_segment_headers_linkified(self) -> None:
        md = assemble(info=_info(), structure=_structure(), bullets=_bullets())
        # seg-003 starts at 270s
        assert (
            "[[0:04:30-0:10:00]]"
            "(https://youtube.com/watch?v=abc12345678&t=270)"
            " Founding of Aldara"
        ) in md

    def test_speaker_line_emitted(self) -> None:
        md = assemble(info=_info(), structure=_structure(), bullets=_bullets())
        assert "Speaker: DM" in md
        assert "Speaker: mixed" in md

    def test_empty_segment_gets_explicit_marker(self) -> None:
        md = assemble(info=_info(), structure=_structure(), bullets=_bullets())
        assert "_No claims extracted from this segment._" in md

    def test_bullets_rendered_with_clickable_anchor(self) -> None:
        md = assemble(info=_info(), structure=_structure(), bullets=_bullets())
        assert (
            "- King Theron founded Aldara in the Second Age "
            "[[0:04:32]](https://youtube.com/watch?v=abc12345678&t=272)"
        ) in md

    def test_uncertainty_flags_rendered(self) -> None:
        md = assemble(info=_info(), structure=_structure(), bullets=_bullets())
        assert "[0:05:47]" in md
        assert "a place name" in md

    def test_no_source_url_omits_links(self) -> None:
        info = _info(source_url=None)
        md = assemble(info=info, structure=_structure(), bullets=_bullets())
        # header still appears, just without URL
        assert "## [0:00:00-0:02:00] Introduction" in md

    def test_existing_frontmatter_name_corrections_applied(self) -> None:
        # if the human added corrections to the frontmatter map on a
        # previous pass, assemble() applies them to rendered text.
        md = assemble(
            info=_info(),
            structure=_structure(),
            bullets=_bullets(),
            name_corrections={"Aldara": "Aldaria"},
        )
        assert "Aldaria" in md
        assert (
            "King Theron founded Aldara in the Second Age" not in md or "Aldaria" in md
        )


class TestWriteReadFrontmatter:
    def test_write_and_read_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "reading.md"
        text = assemble(info=_info(), structure=_structure(), bullets=_bullets())
        write(path, text)
        fm = read_frontmatter(path)
        assert fm["source_id"] == "yt-abc12345678"
        assert fm["reading_status"] == "draft"

    def test_read_frontmatter_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ReadingError):
            read_frontmatter(tmp_path / "nope.md")

    def test_read_frontmatter_no_fence_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "reading.md"
        path.write_text("no frontmatter here", encoding="utf-8")
        with pytest.raises(ReadingError):
            read_frontmatter(path)

    def test_set_status_flip_to_approved(self, tmp_path: Path) -> None:
        path = tmp_path / "reading.md"
        text = assemble(info=_info(), structure=_structure(), bullets=_bullets())
        write(path, text)
        set_status(path, "approved")
        fm = read_frontmatter(path)
        assert fm["reading_status"] == "approved"

    def test_set_status_rejects_bad_value(self, tmp_path: Path) -> None:
        path = tmp_path / "reading.md"
        text = assemble(info=_info(), structure=_structure(), bullets=_bullets())
        write(path, text)
        with pytest.raises(ReadingError):
            set_status(path, "published")
