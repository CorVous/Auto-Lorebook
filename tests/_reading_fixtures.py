"""Shared builder helpers for reading tests.

Underscore prefix prevents pytest collection.
"""

from __future__ import annotations

from auto_lorebook.info_yaml import Info, SourceContext
from auto_lorebook.reading_sidecar import Sidecar
from auto_lorebook.segment_file import SegmentFile, SegmentFrontmatter
from auto_lorebook.stage1b import Bullet, ReadingBullets
from auto_lorebook.structure import Segment, Structure, UncertaintyFlag


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
        context=SourceContext(),
    )


def _structure() -> Structure:
    return Structure(
        source_id="yt-abc12345678",
        generated_at="2026-04-20T14:32:00Z",
        default_speaker="DM",
        segments=[
            Segment(
                id="seg-001", start=0.0, end=120.0, title="Introduction", speaker="DM"
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
                locator=347.0, span="a place name", kind="name", note="unclear"
            )
        ],
    )


def _bullets() -> ReadingBullets:
    return ReadingBullets(
        source_id="yt-abc12345678",
        generated_at="2026-04-20T14:34:00Z",
        segments={
            "seg-001": [],
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


def _sidecar(name_corrections: dict[str, str] | None = None) -> Sidecar:
    return Sidecar(
        default_speaker="DM",
        name_corrections=name_corrections or {},
        session_date=None,
    )


def _segment_files() -> list[SegmentFile]:
    """Three segment files matching _structure() + _bullets()."""
    seg001 = SegmentFile(
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
    seg002 = SegmentFile(
        frontmatter=SegmentFrontmatter(
            segment_id="seg-002",
            segment_status="draft",
            start=120.0,
            end=270.0,
            title="Rules discussion: grappling",
            speaker="mixed",
            notes="off-topic",
        ),
        body="_No claims extracted from this segment._\n",
    )
    seg003 = SegmentFile(
        frontmatter=SegmentFrontmatter(
            segment_id="seg-003",
            segment_status="draft",
            start=270.0,
            end=600.0,
            title="Founding of Aldara",
            speaker="DM",
        ),
        body=(
            "- [0:05:47] uncertain name: a place name; unclear\n"
            "\n"
            "- King Theron founded Aldara in the Second Age"
            " [[0:04:32]](https://youtube.com/watch?v=abc12345678&t=272)\n"
            "\n"
            "- The founding displaced an earlier elven presence"
            " [[0:05:14]](https://youtube.com/watch?v=abc12345678&t=314)\n"
        ),
    )
    return [seg001, seg002, seg003]
