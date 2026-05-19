"""Tests for plan_yaml.py — Stage 2 plan file I/O + DB API."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook import db
from auto_lorebook.plan_yaml import (
    ClaimTarget,
    EntityResolution,
    NewEntityProposal,
    Plan,
    PlanError,
    PlannedClaim,
    Unresolved,
    list_plans,
    read,
    read_plan_routes,
    write,
    write_plan_routes,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Generator
    from pathlib import Path


def _full_plan() -> Plan:
    return Plan(
        source_id="yt-abc123",
        planned_at="2026-04-20T14:58:33Z",
        entity_resolutions=[
            EntityResolution(
                mention="the Aldaran Realm",
                mention_locations=["[4:30-8:00] founding"],
                resolution="existing",
                matched_entity="Aldara",
                rationale="Listed in Aldara's aliases.",
            ),
            EntityResolution(
                mention="the War of the Dusk",
                mention_locations=["[8:00-12:00] war"],
                resolution="new",
                proposed_entity_name="War of the Dusk",
                proposed_category="events",
                rationale="No existing entity matches.",
            ),
            EntityResolution(
                mention="the elven sorceress",
                mention_locations=["[1:23:40-1:24:15] hearsay"],
                resolution="ambiguous",
                rationale="Unnamed referent.",
                human_review_needed=True,
            ),
        ],
        new_entities=[
            NewEntityProposal(name="War of the Dusk", category="events"),
        ],
        planned_claims=[
            PlannedClaim(
                claim_group_id="cg-001",
                reading_section="[4:30-8:00] Founding of Aldara",
                reading_bullet_index=0,
                locator="0:04:32",
                locator_hint="0:04:25-0:04:50",
                proposed_speaker="DM",
                proposed_status="authoritative",
                proposed_status_reason=None,
                targets=[
                    ClaimTarget(
                        entity="Aldara",
                        entity_state="existing",
                        proposed_section="founding",
                        rationale="Claim concerns Aldara's founding.",
                    ),
                    ClaimTarget(
                        entity="Theron",
                        entity_state="existing",
                        proposed_section="lineage",
                        rationale="Establishes grandfather as founder.",
                    ),
                    ClaimTarget(
                        entity="Second Age",
                        entity_state="new",
                        proposed_section="events-in-era",
                        proposed_category="events",
                        rationale="Dates founding to the Second Age.",
                    ),
                ],
            ),
        ],
        unresolved=[
            Unresolved(
                reading_section="[8:00-12:00] The War of the Dusk",
                locator="0:09:12",
                issue="Reading flagged uncertain place name.",
            ),
        ],
    )


class TestRoundTrip:
    def test_round_trip(self, tmp_path: Path) -> None:
        plan = _full_plan()
        path = tmp_path / "plan.yaml"
        write(plan, path)
        loaded = read(path)
        assert loaded == plan

    def test_schema_version_first_key(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        write(_full_plan(), path)
        # First non-blank key must be schema_version, value 1
        first_line = next(ln for ln in path.read_text().splitlines() if ln.strip())
        assert first_line.startswith("schema_version:")
        assert first_line.split(":", 1)[1].strip() == "1"

    def test_multi_target_claim_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        write(_full_plan(), path)
        loaded = read(path)
        assert len(loaded.planned_claims[0].targets) == 3
        states = {t.entity_state for t in loaded.planned_claims[0].targets}
        assert states == {"existing", "new"}


class TestSchemaValidation:
    def test_unsupported_schema_version_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        path.write_text(
            yaml.safe_dump({
                "schema_version": 2,
                "source_id": "yt-x",
                "planned_at": "2026-04-20T00:00:00Z",
            })
        )
        with pytest.raises(PlanError, match="schema_version"):
            read(path)

    def test_missing_schema_version_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        path.write_text(
            yaml.safe_dump({
                "source_id": "yt-x",
                "planned_at": "2026-04-20T00:00:00Z",
            })
        )
        with pytest.raises(PlanError, match="schema_version"):
            read(path)

    def test_missing_source_id_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        path.write_text(
            yaml.safe_dump({
                "schema_version": 1,
                "planned_at": "2026-04-20T00:00:00Z",
            })
        )
        with pytest.raises(PlanError, match="source_id"):
            read(path)

    def test_missing_planned_at_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        path.write_text(
            yaml.safe_dump({
                "schema_version": 1,
                "source_id": "yt-x",
            })
        )
        with pytest.raises(PlanError, match="planned_at"):
            read(path)

    def test_unknown_resolution_kind_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        path.write_text(
            yaml.safe_dump({
                "schema_version": 1,
                "source_id": "yt-x",
                "planned_at": "2026-04-20T00:00:00Z",
                "entity_resolutions": [
                    {"mention": "x", "resolution": "wat"},
                ],
            })
        )
        with pytest.raises(PlanError, match="resolution"):
            read(path)

    def test_unknown_target_state_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        path.write_text(
            yaml.safe_dump({
                "schema_version": 1,
                "source_id": "yt-x",
                "planned_at": "2026-04-20T00:00:00Z",
                "planned_claims": [
                    {
                        "claim_group_id": "cg-1",
                        "reading_section": "[0-1]",
                        "reading_bullet_index": 0,
                        "locator": "0:00:00",
                        "locator_hint": "0:00:00-0:00:01",
                        "proposed_speaker": "DM",
                        "proposed_status": "authoritative",
                        "targets": [
                            {
                                "entity": "X",
                                "entity_state": "wat",
                                "proposed_section": "foo",
                            }
                        ],
                    }
                ],
            })
        )
        with pytest.raises(PlanError, match="entity_state"):
            read(path)

    def test_new_entity_target_requires_category(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        path.write_text(
            yaml.safe_dump({
                "schema_version": 1,
                "source_id": "yt-x",
                "planned_at": "2026-04-20T00:00:00Z",
                "planned_claims": [
                    {
                        "claim_group_id": "cg-1",
                        "reading_section": "[0-1]",
                        "reading_bullet_index": 0,
                        "locator": "0:00:00",
                        "locator_hint": "0:00:00-0:00:01",
                        "proposed_speaker": "DM",
                        "proposed_status": "authoritative",
                        "targets": [
                            {
                                "entity": "Brand New",
                                "entity_state": "new",
                                "proposed_section": "overview",
                            }
                        ],
                    }
                ],
            })
        )
        with pytest.raises(PlanError, match="proposed_category"):
            read(path)

    def test_invalid_category_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        path.write_text(
            yaml.safe_dump({
                "schema_version": 1,
                "source_id": "yt-x",
                "planned_at": "2026-04-20T00:00:00Z",
                "new_entities": [
                    {"name": "Foo", "category": "not-a-category"},
                ],
            })
        )
        with pytest.raises(PlanError, match="category"):
            read(path)

    def test_empty_targets_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "plan.yaml"
        path.write_text(
            yaml.safe_dump({
                "schema_version": 1,
                "source_id": "yt-x",
                "planned_at": "2026-04-20T00:00:00Z",
                "planned_claims": [
                    {
                        "claim_group_id": "cg-1",
                        "reading_section": "[0-1]",
                        "reading_bullet_index": 0,
                        "locator": "0:00:00",
                        "locator_hint": "0:00:00-0:00:01",
                        "proposed_speaker": "DM",
                        "proposed_status": "authoritative",
                        "targets": [],
                    }
                ],
            })
        )
        with pytest.raises(PlanError, match="targets"):
            read(path)


class TestReadMissing:
    def test_read_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(PlanError, match="not found"):
            read(tmp_path / "missing.yaml")


# ---------------------------------------------------------------------------
# DB API tests
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn() -> Generator[sqlite3.Connection]:
    """In-memory DB with seed source + ingest rows.

    Yields:
        open in-memory connection.

    """
    conn = db.open(":memory:")
    conn.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at, context_json)"
        " VALUES ('yt-abc123', 'youtube', '2026-01-01T00:00:00Z', '{}')"
    )
    conn.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('yt-abc123', 'yt-abc123', '2026-01-01T00:00:00Z', 'planned')"
    )
    conn.commit()
    yield conn
    conn.close()


class TestWriteReadPlanRoutes:
    def test_read_returns_none_when_absent(self, db_conn: sqlite3.Connection) -> None:
        result = read_plan_routes(db_conn, "no-such")
        assert result is None

    def test_round_trip_minimal_plan(self, db_conn: sqlite3.Connection) -> None:
        plan = Plan(
            source_id="yt-abc123",
            planned_at="2026-04-20T14:58:33Z",
            planned_claims=[
                PlannedClaim(
                    claim_group_id="cg-001",
                    reading_section="[4:30-8:00] Founding",
                    reading_bullet_index=0,
                    locator="0:04:32",
                    locator_hint="0:04:00-0:05:00",
                    proposed_speaker="DM",
                    proposed_status="authoritative",
                    targets=[
                        ClaimTarget(
                            entity="Aldara",
                            entity_state="new",
                            proposed_section="founding",
                            proposed_category="locations",
                        )
                    ],
                )
            ],
        )
        write_plan_routes(db_conn, "yt-abc123", plan)
        db_conn.commit()
        loaded = read_plan_routes(db_conn, "yt-abc123")
        assert loaded is not None
        assert loaded.source_id == "yt-abc123"
        assert loaded.planned_at == "2026-04-20T14:58:33Z"
        assert len(loaded.planned_claims) == 1
        claim = loaded.planned_claims[0]
        assert claim.claim_group_id == "cg-001"
        assert len(claim.targets) == 1
        assert claim.targets[0].entity == "Aldara"

    def test_write_overwrites_previous_plan(self, db_conn: sqlite3.Connection) -> None:
        plan_v1 = Plan(
            source_id="yt-abc123",
            planned_at="2026-04-20T00:00:00Z",
            planned_claims=[
                PlannedClaim(
                    claim_group_id="cg-old",
                    reading_section="[0-1]",
                    reading_bullet_index=0,
                    locator="0:00:00",
                    locator_hint="0:00:00-0:01:00",
                    proposed_speaker="DM",
                    proposed_status="authoritative",
                    targets=[
                        ClaimTarget(
                            entity="X",
                            entity_state="new",
                            proposed_section="s",
                            proposed_category="characters",
                        )
                    ],
                )
            ],
        )
        write_plan_routes(db_conn, "yt-abc123", plan_v1)
        db_conn.commit()

        plan_v2 = Plan(
            source_id="yt-abc123",
            planned_at="2026-04-21T00:00:00Z",
        )
        write_plan_routes(db_conn, "yt-abc123", plan_v2)
        db_conn.commit()

        loaded = read_plan_routes(db_conn, "yt-abc123")
        assert loaded is not None
        assert loaded.planned_at == "2026-04-21T00:00:00Z"
        assert loaded.planned_claims == []

    def test_entity_resolutions_and_new_entities_round_trip(
        self, db_conn: sqlite3.Connection
    ) -> None:
        plan = Plan(
            source_id="yt-abc123",
            planned_at="2026-04-20T00:00:00Z",
            entity_resolutions=[
                EntityResolution(
                    mention="Aldara",
                    resolution="new",
                    proposed_entity_name="Aldara",
                    proposed_category="locations",
                )
            ],
            new_entities=[NewEntityProposal(name="Aldara", category="locations")],
        )
        write_plan_routes(db_conn, "yt-abc123", plan)
        db_conn.commit()
        loaded = read_plan_routes(db_conn, "yt-abc123")
        assert loaded is not None
        assert len(loaded.entity_resolutions) == 1
        assert loaded.entity_resolutions[0].mention == "Aldara"
        assert len(loaded.new_entities) == 1
        assert loaded.new_entities[0].name == "Aldara"

    def test_list_plans_returns_summary(self, db_conn: sqlite3.Connection) -> None:
        plan = Plan(
            source_id="yt-abc123",
            planned_at="2026-04-20T14:58:33Z",
            new_entities=[NewEntityProposal(name="X", category="characters")],
            planned_claims=[
                PlannedClaim(
                    claim_group_id="cg-001",
                    reading_section="s",
                    reading_bullet_index=0,
                    locator="0:00:00",
                    locator_hint="0:00:00-0:01:00",
                    proposed_speaker="DM",
                    proposed_status="authoritative",
                    targets=[
                        ClaimTarget(
                            entity="X",
                            entity_state="new",
                            proposed_section="s",
                            proposed_category="characters",
                        )
                    ],
                )
            ],
        )
        write_plan_routes(db_conn, "yt-abc123", plan)
        db_conn.commit()
        rows = list_plans(db_conn)
        assert len(rows) == 1
        sid, _planned_at, claim_count, ne_count = rows[0]
        assert sid == "yt-abc123"
        assert claim_count == 1
        assert ne_count == 1
