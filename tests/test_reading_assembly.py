"""Tests for reading_assembly.py — wiki-side reading.md assembly."""

from __future__ import annotations

from auto_lorebook.reading_assembly import assemble
from auto_lorebook.reading_pipeline import build_segment_body
from auto_lorebook.segment_file import SegmentFile, SegmentFrontmatter
from tests._reading_fixtures import (
    _bullets,
    _info,
    _segment_files,
    _sidecar,
    _structure,
)

# Golden bytes: assembled output that the new assemble() must produce.
# Derived from the legacy reading.assemble() output, with reading_status: approved
# (legacy produced draft; wiki-side always approved).
_GOLDEN = (
    "---\n"
    "schema_version: 1\n"
    "source_id: yt-abc12345678\n"
    "source_name: Session 3\n"
    "source_url: https://youtube.com/watch?v=abc12345678\n"
    "source_type: youtube\n"
    "session_date: null\n"
    "ingested_at: '2026-04-20T14:35:12Z'\n"
    "reading_status: approved\n"
    "default_speaker: DM\n"
    "name_corrections: {}\n"
    "---\n"
    "\n"
    "# Reading: Session 3\n"
    "\n"
    "## [[0:00:00-0:02:00]](https://youtube.com/watch?v=abc12345678&t=0) Introduction\n"
    "\n"
    "Speaker: DM\n"
    "\n"
    "_No claims extracted from this segment._\n"
    "\n"
    "## [[0:02:00-0:04:30]]"
    "(https://youtube.com/watch?v=abc12345678&t=120)"
    " Rules discussion: grappling\n"
    "\n"
    "Speaker: mixed\n"
    "\n"
    "_No claims extracted from this segment._\n"
    "\n"
    "## [[0:04:30-0:10:00]]"
    "(https://youtube.com/watch?v=abc12345678&t=270)"
    " Founding of Aldara\n"
    "\n"
    "Speaker: DM\n"
    "\n"
    "- [0:05:47] uncertain name: a place name; unclear\n"
    "\n"
    "- King Theron founded Aldara in the Second Age"
    " [[0:04:32]](https://youtube.com/watch?v=abc12345678&t=272)\n"
    "\n"
    "- The founding displaced an earlier elven presence"
    " [[0:05:14]](https://youtube.com/watch?v=abc12345678&t=314)\n"
)


class TestGoldenByteMatch:
    def test_matches_legacy_output(self) -> None:
        result = assemble(segments=_segment_files(), sidecar=_sidecar(), info=_info())
        assert result == _GOLDEN

    def test_always_approved_status(self) -> None:
        result = assemble(segments=_segment_files(), sidecar=_sidecar(), info=_info())
        assert "reading_status: approved" in result
        assert "reading_status: draft" not in result


class TestNoSourceUrl:
    def test_no_url_segment_headers_plain(self) -> None:
        # segment headers rendered without URL links when source_url is None
        seg = SegmentFile(
            frontmatter=SegmentFrontmatter(
                segment_id="seg-001",
                segment_status="draft",
                start=0.0,
                end=120.0,
                title="Introduction",
                speaker="DM",
            ),
            body="_No claims extracted from this segment._\n",
        )
        result = assemble(
            segments=[seg], sidecar=_sidecar(), info=_info(source_url=None)
        )
        assert "## [0:00:00-0:02:00] Introduction" in result
        assert "[[0:00:00" not in result


class TestNameCorrections:
    def test_corrections_applied_to_segment_headers(self) -> None:
        result = assemble(
            segments=_segment_files(),
            sidecar=_sidecar(name_corrections={"Aldara": "Aldaria"}),
            info=_info(),
        )
        assert "Aldaria" in result

    def test_corrections_in_frontmatter(self) -> None:
        result = assemble(
            segments=_segment_files(),
            sidecar=_sidecar(name_corrections={"Aldara": "Aldaria"}),
            info=_info(),
        )
        assert "Aldara: Aldaria" in result


class TestEmptySegmentMarker:
    def test_empty_body_gets_marker(self) -> None:
        seg = SegmentFile(
            frontmatter=SegmentFrontmatter(
                segment_id="seg-001",
                segment_status="draft",
                start=0.0,
                end=60.0,
                title="Intro",
                speaker="DM",
            ),
            body="",
        )
        result = assemble(segments=[seg], sidecar=_sidecar(), info=_info())
        assert "_No claims extracted from this segment._" in result


class TestSegmentStatusIgnored:
    def test_approved_segment_still_assembled(self) -> None:
        segs = _segment_files()
        # flip one to approved — should not affect output
        sf = segs[0]
        approved_sf = SegmentFile(
            frontmatter=SegmentFrontmatter(
                segment_id=sf.frontmatter.segment_id,
                segment_status="approved",
                start=sf.frontmatter.start,
                end=sf.frontmatter.end,
                title=sf.frontmatter.title,
                speaker=sf.frontmatter.speaker,
            ),
            body=sf.body,
        )
        result = assemble(
            segments=[approved_sf, segs[1], segs[2]],
            sidecar=_sidecar(),
            info=_info(),
        )
        assert "Introduction" in result
        assert "reading_status: approved" in result


class TestPureNoFilesystem:
    def test_returns_string_not_path(self) -> None:
        result = assemble(segments=_segment_files(), sidecar=_sidecar(), info=_info())
        assert isinstance(result, str)


class TestFixtureMatchesPipeline:
    """Anchors _segment_files() bodies to actual pipeline output.

    Without this, the golden test could pass on a fixture that drifts from
    what generate-reading actually writes — making the byte-identity check
    a tautology rather than a true legacy capture.
    """

    def test_bodies_match_build_segment_body(self) -> None:
        structure = _structure()
        bullets = _bullets()
        info = _info()
        expected = _segment_files()
        for seg, expected_sf in zip(structure.segments, expected, strict=True):
            flags = [
                f
                for f in structure.uncertainty_flags
                if seg.start <= f.locator < seg.end
            ]
            body = build_segment_body(
                _seg=seg,
                bullets=bullets.segments[seg.id],
                flags=flags,
                source_url=info.source_url,
                name_corrections={},
            )
            assert body == expected_sf.body, (
                f"fixture body for {seg.id} drifted from pipeline build_segment_body"
            )
