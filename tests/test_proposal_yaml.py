"""Tests for proposal_yaml.py — Stage 3 proposal file I/O + DB API."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from auto_lorebook import db
from auto_lorebook.proposal_yaml import (
    Correction,
    Proposal,
    ProposalError,
    Sibling,
    count_proposals,
    delete_all_for_ingest,
    delete_proposal,
    list_proposals,
    proposals_exist,
    read,
    read_proposal,
    write,
    write_proposal,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Generator
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


# ---------------------------------------------------------------------------
# DB API tests
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> Generator[sqlite3.Connection]:
    """In-memory DB with seed source + ingest + plan_routes rows.

    Yields:
        open in-memory connection.

    """
    conn = db.open(":memory:")
    conn.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at, context_json)"
        " VALUES ('yt-x', 'youtube', '2026-01-01T00:00:00Z', '{}')"
    )
    conn.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('ing-001', 'yt-x', '2026-01-01T00:00:00Z', 'extracted')"
    )
    conn.execute(
        "INSERT INTO plan_routes(ingest_id, claim_group_id, target_entity_name,"
        " entity_state, proposed_section, proposed_status, locator, locator_hint,"
        " reading_section, reading_bullet_index)"
        " VALUES ('ing-001','cg-001','Aldara','new','founding',"
        " 'authoritative','0:04:32','0:04:00-0:05:00','[4:30-8:00]',0)"
    )
    conn.commit()
    yield conn
    conn.close()


def _route_id(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT id FROM plan_routes WHERE ingest_id='ing-001'"
    ).fetchone()[0]


def _make_db_proposal(**overrides: Any) -> Proposal:  # noqa: ANN401
    base: dict[str, Any] = {
        "proposal_type": "new_entity_with_facts",
        "target_entity": "Aldara",
        "proposed_id": "aldara-f001",
        "claim_group_id": "cg-001",
        "text": "Aldara was founded in the Second Age.",
        "raw_transcript_span": "Aldara was founded in the Second Age.",
        "text_corrects_transcript": False,
        "source_id": "yt-x",
        "locator": "0:04:32-0:04:41",
        "speaker": "DM",
        "reading_section": "[4:30-8:00] Founding",
        "reading_bullet_index": 0,
        "status": "authoritative",
        "session_date": "2026-01-15",
        "section": "founding",
        "context_before": "",
        "context_after": "",
    }
    base.update(overrides)
    return Proposal(**base)


class TestWriteReadProposal:
    def test_read_returns_none_when_absent(self, db_conn: sqlite3.Connection) -> None:
        assert read_proposal(db_conn, "no-such") is None

    def test_round_trip(self, db_conn: sqlite3.Connection) -> None:
        p = _make_db_proposal()
        write_proposal(db_conn, "ing-001", _route_id(db_conn), p)
        db_conn.commit()
        loaded = read_proposal(db_conn, "aldara-f001")
        assert loaded is not None
        assert loaded.proposed_id == "aldara-f001"
        assert loaded.text == "Aldara was founded in the Second Age."
        assert loaded.status == "authoritative"

    def test_list_proposals_returns_all(self, db_conn: sqlite3.Connection) -> None:
        p1 = _make_db_proposal(proposed_id="aldara-f001", target_entity="Aldara")
        write_proposal(db_conn, "ing-001", _route_id(db_conn), p1)
        db_conn.commit()
        result = list_proposals(db_conn, "ing-001")
        assert len(result) == 1
        assert result[0].proposed_id == "aldara-f001"

    def test_list_proposals_empty_when_none(self, db_conn: sqlite3.Connection) -> None:
        assert list_proposals(db_conn, "ing-001") == []

    def test_delete_proposal(self, db_conn: sqlite3.Connection) -> None:
        p = _make_db_proposal()
        write_proposal(db_conn, "ing-001", _route_id(db_conn), p)
        db_conn.commit()
        delete_proposal(db_conn, "aldara-f001")
        db_conn.commit()
        assert read_proposal(db_conn, "aldara-f001") is None

    def test_delete_proposal_silent_if_absent(
        self, db_conn: sqlite3.Connection
    ) -> None:
        delete_proposal(db_conn, "no-such-id")  # must not raise

    def test_count_proposals(self, db_conn: sqlite3.Connection) -> None:
        assert count_proposals(db_conn, "ing-001") == 0
        p = _make_db_proposal()
        write_proposal(db_conn, "ing-001", _route_id(db_conn), p)
        db_conn.commit()
        assert count_proposals(db_conn, "ing-001") == 1

    def test_proposals_exist(self, db_conn: sqlite3.Connection) -> None:
        assert not proposals_exist(db_conn, "ing-001")
        p = _make_db_proposal()
        write_proposal(db_conn, "ing-001", _route_id(db_conn), p)
        db_conn.commit()
        assert proposals_exist(db_conn, "ing-001")

    def test_delete_all_for_ingest(self, db_conn: sqlite3.Connection) -> None:
        p = _make_db_proposal()
        write_proposal(db_conn, "ing-001", _route_id(db_conn), p)
        db_conn.commit()
        delete_all_for_ingest(db_conn, "ing-001")
        db_conn.commit()
        assert count_proposals(db_conn, "ing-001") == 0

    def test_corrections_applied_round_trip(self, db_conn: sqlite3.Connection) -> None:
        p = _make_db_proposal(
            corrections_applied=[
                Correction(
                    from_="all-dara", to="Aldara", source="reading-name-correction"
                )
            ]
        )
        write_proposal(db_conn, "ing-001", _route_id(db_conn), p)
        db_conn.commit()
        loaded = read_proposal(db_conn, "aldara-f001")
        assert loaded is not None
        assert len(loaded.corrections_applied) == 1
        assert loaded.corrections_applied[0].from_ == "all-dara"

    def test_flag_reason_persisted(self, db_conn: sqlite3.Connection) -> None:
        p = _make_db_proposal(extractor_flagged=True, flag_reason="suspicious span")
        write_proposal(db_conn, "ing-001", _route_id(db_conn), p)
        db_conn.commit()
        loaded = read_proposal(db_conn, "aldara-f001")
        assert loaded is not None
        assert loaded.flag_reason == "suspicious span"
