"""Tests for proposal_yaml.py — Stage 3 proposal read/write."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from auto_lorebook.proposal_yaml import (
    Correction,
    Proposal,
    ProposalError,
    Sibling,
    read,
    write,
)

if TYPE_CHECKING:
    from pathlib import Path


def _full_proposal(**overrides: Any) -> Proposal:  # noqa: ANN401
    base = Proposal(
        proposal_type="new_fact",
        target_entity="Aldara",
        proposed_id="aldara-f004",
        claim_group_id="cg-001",
        claim_group_siblings=[
            Sibling(entity="Theron", proposed_id="theron-f011"),
            Sibling(entity="Second Age", proposed_id="second-age-f001"),
        ],
        text="Theron's grandfather founded Aldara in the Second Age.",
        raw_transcript_span=(
            "Fair-on's grandfather founded all-dara in the Second Age."
        ),
        text_corrects_transcript=True,
        corrections_applied=[
            Correction(
                from_="Fair-on",
                to="Theron",
                source="global-transcription-correction",
            ),
            Correction(
                from_="all-dara",
                to="Aldara",
                source="reading-name-correction",
            ),
        ],
        source_id="yt-abc123",
        locator="0:04:32-0:04:41",
        speaker="DM",
        reading_section="[4:30-8:00] Founding of Aldara",
        reading_bullet_index=0,
        status="authoritative",
        session_date="2026-01-15",
        section="founding",
        context_before="So let's talk about the founding of Aldara.",
        context_after="And that's why the Theron name matters so much now.",
    )
    return dataclasses.replace(base, **overrides)


class TestRoundTrip:
    def test_full_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        original = _full_proposal()
        write(original, path)
        loaded = read(path)
        assert loaded == original

    def test_schema_version_first_key(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        write(_full_proposal(), path)
        text = path.read_text(encoding="utf-8")
        assert text.startswith("schema_version: 1")

    def test_from_field_yaml_key_is_from(self, tmp_path: Path) -> None:
        """`Correction.from_` must serialise to YAML key `from`, not `from_`."""
        path = tmp_path / "p.yaml"
        write(_full_proposal(), path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        applied = raw["corrections_applied"]
        assert "from" in applied[0]
        assert "from_" not in applied[0]
        assert applied[0]["from"] == "Fair-on"

    def test_from_yaml_key_round_trips(self, tmp_path: Path) -> None:
        """Read a hand-written `from: X` and confirm it lands on Correction.from_."""
        path = tmp_path / "p.yaml"
        data = {
            "schema_version": 1,
            "proposal_type": "new_fact",
            "target_entity": "Aldara",
            "proposed_id": "aldara-f001",
            "claim_group_id": "cg-001",
            "claim_group_siblings": [],
            "text": "x",
            "raw_transcript_span": "x",
            "text_corrects_transcript": False,
            "corrections_applied": [
                {"from": "X", "to": "Y", "source": "reading-name-correction"},
            ],
            "source_id": "yt-x",
            "locator": "0:00:01-0:00:02",
            "speaker": "DM",
            "status": "authoritative",
            "session_date": "2026-01-15",
            "section": "founding",
            "reading_section": "[0:00-1:00] s",
            "reading_bullet_index": 0,
            "context_before": "",
            "context_after": "",
        }
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        loaded = read(path)
        assert loaded.corrections_applied[0].from_ == "X"
        assert loaded.corrections_applied[0].to == "Y"


class TestOptionalFields:
    def test_hint_widened_omitted_when_false(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        write(_full_proposal(hint_widened=False), path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "hint_widened" not in raw

    def test_hint_widened_written_when_true(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        write(_full_proposal(hint_widened=True), path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["hint_widened"] is True

    def test_extractor_flagged_omitted_when_false(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        write(_full_proposal(), path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "extractor_flagged" not in raw
        assert "flag_reason" not in raw

    def test_status_reason_omitted_when_none(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        write(_full_proposal(), path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "status_reason" not in raw


class TestValidation:
    def test_unknown_proposal_type_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        path.write_text(
            yaml.safe_dump({
                "schema_version": 1,
                "proposal_type": "weird",
                "target_entity": "x",
                "proposed_id": "x-f001",
                "claim_group_id": "cg-001",
                "claim_group_siblings": [],
                "text": "x",
                "raw_transcript_span": "x",
                "text_corrects_transcript": False,
                "corrections_applied": [],
                "source_id": "yt-x",
                "locator": "0:00:01-0:00:02",
                "speaker": "DM",
                "status": "authoritative",
                "session_date": "2026-01-15",
                "section": "x",
                "reading_section": "x",
                "reading_bullet_index": 0,
                "context_before": "",
                "context_after": "",
            }),
            encoding="utf-8",
        )
        with pytest.raises(ProposalError, match="proposal_type"):
            read(path)

    def test_unknown_correction_source_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        data = {
            "schema_version": 1,
            "proposal_type": "new_fact",
            "target_entity": "Aldara",
            "proposed_id": "aldara-f001",
            "claim_group_id": "cg-001",
            "claim_group_siblings": [],
            "text": "x",
            "raw_transcript_span": "x",
            "text_corrects_transcript": True,
            "corrections_applied": [
                {"from": "X", "to": "Y", "source": "made-up-source"},
            ],
            "source_id": "yt-x",
            "locator": "0:00:01-0:00:02",
            "speaker": "DM",
            "status": "authoritative",
            "session_date": "2026-01-15",
            "section": "x",
            "reading_section": "x",
            "reading_bullet_index": 0,
            "context_before": "",
            "context_after": "",
        }
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        with pytest.raises(ProposalError, match="source"):
            read(path)

    def test_missing_correction_from_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        data = {
            "schema_version": 1,
            "proposal_type": "new_fact",
            "target_entity": "Aldara",
            "proposed_id": "aldara-f001",
            "claim_group_id": "cg-001",
            "claim_group_siblings": [],
            "text": "x",
            "raw_transcript_span": "x",
            "text_corrects_transcript": True,
            "corrections_applied": [
                {"to": "Y", "source": "reading-name-correction"},
            ],
            "source_id": "yt-x",
            "locator": "0:00:01-0:00:02",
            "speaker": "DM",
            "status": "authoritative",
            "session_date": "2026-01-15",
            "section": "x",
            "reading_section": "x",
            "reading_bullet_index": 0,
            "context_before": "",
            "context_after": "",
        }
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        with pytest.raises(ProposalError, match="from"):
            read(path)

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        path.write_text(
            yaml.safe_dump({"schema_version": 1, "proposal_type": "new_fact"}),
            encoding="utf-8",
        )
        with pytest.raises(ProposalError):
            read(path)

    def test_bad_schema_version_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "p.yaml"
        path.write_text(
            yaml.safe_dump({"schema_version": 99, "proposal_type": "new_fact"}),
            encoding="utf-8",
        )
        with pytest.raises(ProposalError):
            read(path)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ProposalError, match="file not found"):
            read(tmp_path / "nope.yaml")
