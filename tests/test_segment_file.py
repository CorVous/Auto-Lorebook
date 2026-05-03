"""Tests for segment_file.py — per-segment frontmatter + body I/O."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook.segment_file import (
    SegmentFile,
    SegmentFileError,
    SegmentFrontmatter,
    read,
    set_status,
    with_status,
    write,
)
from auto_lorebook.structure import Override

if TYPE_CHECKING:
    from pathlib import Path


def _fm(
    *,
    segment_id: str = "seg-001",
    segment_status: str = "draft",
    start: float = 0.0,
    end: float = 120.0,
    title: str = "Introduction",
    speaker: str = "DM",
    notes: str | None = None,
    overrides: list[Override] | None = None,
) -> SegmentFrontmatter:
    return SegmentFrontmatter(
        segment_id=segment_id,
        segment_status=segment_status,
        start=start,
        end=end,
        title=title,
        speaker=speaker,
        notes=notes,
        overrides=overrides or [],
    )


def _sf(body: str = "- Intro bullet [0:00:15]\n") -> SegmentFile:
    return SegmentFile(frontmatter=_fm(), body=body)


class TestRoundTripFull:
    def test_round_trip(self, tmp_path: Path) -> None:
        sf = _sf()
        p = tmp_path / "seg-001.md"
        write(sf, p)
        loaded = read(p)
        assert loaded.frontmatter.segment_id == sf.frontmatter.segment_id
        assert loaded.frontmatter.segment_status == sf.frontmatter.segment_status
        assert loaded.frontmatter.start == sf.frontmatter.start
        assert loaded.frontmatter.end == sf.frontmatter.end
        assert loaded.frontmatter.title == sf.frontmatter.title
        assert loaded.frontmatter.speaker == sf.frontmatter.speaker
        assert loaded.body == sf.body

    def test_schema_version_first(self, tmp_path: Path) -> None:
        p = tmp_path / "seg-001.md"
        write(_sf(), p)
        first_line = p.read_text(encoding="utf-8").splitlines()[0]
        assert first_line == "---"
        lines = p.read_text(encoding="utf-8").splitlines()
        # first line after opening --- must be schema_version
        assert lines[1] == "schema_version: 1"

    def test_frontmatter_key_order(self, tmp_path: Path) -> None:
        p = tmp_path / "seg-001.md"
        write(_sf(), p)
        # extract frontmatter block
        text = p.read_text(encoding="utf-8")
        fm_text = text.split("---\n")[1]
        parsed = yaml.safe_load(fm_text)
        keys = list(parsed.keys())
        # expected order: schema_version, segment_id, segment_status,
        # start, end, title, speaker, notes, overrides
        assert keys[0] == "schema_version"
        assert keys[1] == "segment_id"
        assert keys[2] == "segment_status"

    def test_byte_identical_round_trip(self, tmp_path: Path) -> None:
        sf = SegmentFile(
            frontmatter=_fm(
                notes="off-topic",
                overrides=[Override(start=60.0, end=90.0, speaker="Player")],
            ),
            body="- Some claim [0:01:30]\n",
        )
        p = tmp_path / "seg-001.md"
        write(sf, p)
        first_bytes = p.read_bytes()
        loaded = read(p)
        write(loaded, p)
        assert p.read_bytes() == first_bytes


class TestDefaultStatusDraft:
    def test_default_status_is_draft(self, tmp_path: Path) -> None:
        p = tmp_path / "seg-001.md"
        write(_sf(), p)
        loaded = read(p)
        assert loaded.frontmatter.segment_status == "draft"


class TestSetStatus:
    def test_set_status_flips_to_approved(self, tmp_path: Path) -> None:
        p = tmp_path / "seg-001.md"
        write(_sf(), p)
        set_status(p, "approved")
        loaded = read(p)
        assert loaded.frontmatter.segment_status == "approved"

    def test_set_status_rejects_unknown(self, tmp_path: Path) -> None:
        p = tmp_path / "seg-001.md"
        write(_sf(), p)
        with pytest.raises(SegmentFileError, match=r"invalid.*status"):
            set_status(p, "published")

    def test_with_status_returns_new_text(self, tmp_path: Path) -> None:
        p = tmp_path / "seg-001.md"
        write(_sf(), p)
        new_text = with_status(p, "approved")
        assert "segment_status: approved" in new_text
        # Original file unchanged
        loaded = read(p)
        assert loaded.frontmatter.segment_status == "draft"


class TestBadStatusOnRead:
    def test_bad_status_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "seg-001.md"
        write(_sf(), p)
        text = p.read_text(encoding="utf-8")
        text = text.replace("segment_status: draft", "segment_status: bogus")
        p.write_text(text, encoding="utf-8")
        with pytest.raises(SegmentFileError, match="segment_status"):
            read(p)


class TestOverridesPreserved:
    def test_overrides_round_trip(self, tmp_path: Path) -> None:
        overrides = [
            Override(
                start=30.0, end=60.0, speaker="Alice", voiced_by=None, note="aside"
            ),
            Override(start=90.0, end=100.0, speaker="Bob"),
        ]
        sf = SegmentFile(
            frontmatter=_fm(overrides=overrides),
            body="- A claim [0:00:45]\n",
        )
        p = tmp_path / "seg-001.md"
        write(sf, p)
        loaded = read(p)
        assert len(loaded.frontmatter.overrides) == 2
        assert loaded.frontmatter.overrides[0].speaker == "Alice"
        assert loaded.frontmatter.overrides[0].note == "aside"
        assert loaded.frontmatter.overrides[1].speaker == "Bob"


class TestMissingFrontmatterRaises:
    def test_no_frontmatter_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "seg-001.md"
        p.write_text("just a body with no frontmatter\n", encoding="utf-8")
        with pytest.raises(SegmentFileError, match="frontmatter"):
            read(p)


class TestBodyPreservedVerbatim:
    def test_body_preserved(self, tmp_path: Path) -> None:
        body = "- Claim one [0:00:15]\n- Claim two [0:01:00]\n\n_Extra line._\n"
        sf = SegmentFile(frontmatter=_fm(), body=body)
        p = tmp_path / "seg-001.md"
        write(sf, p)
        loaded = read(p)
        assert loaded.body == body
