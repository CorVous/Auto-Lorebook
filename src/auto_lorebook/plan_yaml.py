"""Stage 2 plan: schema, parse helpers, DB I/O, file I/O (legacy).

DB API (conn-first):
    write_plan_routes(conn, ingest_id, plan) -- DELETE+INSERT plan_metadata+plan_routes
    read_plan_routes(conn, ingest_id) -> Plan | None
    list_plans(conn) -> list[tuple[str,str,int,int]]

File I/O (legacy, kept for tests that pre-date DB migration):
    read(path), write(plan, path)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from auto_lorebook._io import atomic_write_text
from auto_lorebook.entity_yaml import CATEGORIES
from auto_lorebook.schema import SchemaVersionError, read_schema_version

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

_logger = logging.getLogger(__name__)

_MAX_SCHEMA = 1

RESOLUTION_KINDS = frozenset({"existing", "new", "ambiguous"})
ENTITY_STATES = frozenset({"existing", "new"})


class PlanError(ValueError):
    """plan.yaml is missing or malformed on read."""


@dataclass(frozen=True)
class EntityResolution:
    """Routes one mention to existing entity, new proposal, or ambiguous."""

    mention: str
    mention_locations: list[str] = field(default_factory=list)
    resolution: str = "ambiguous"
    rationale: str = ""
    matched_entity: str | None = None
    proposed_entity_name: str | None = None
    proposed_category: str | None = None
    suggested_aliases_to_add: list[str] = field(default_factory=list)
    human_review_needed: bool = False


@dataclass(frozen=True)
class NewEntityProposal:
    """Proposed-but-not-yet-created entity surfaced by the planner."""

    name: str
    category: str
    aliases_suggested: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ClaimTarget:
    """One target of a planned claim (multi-target routing supported)."""

    entity: str
    entity_state: str  # existing | new
    proposed_section: str
    rationale: str = ""
    proposed_category: str | None = None  # required iff entity_state == new


@dataclass(frozen=True)
class PlannedClaim:
    """One reading bullet routed to one or more entities."""

    claim_group_id: str
    reading_section: str
    reading_bullet_index: int
    locator: str
    locator_hint: str
    proposed_speaker: str
    proposed_status: str
    proposed_status_reason: str | None = None
    targets: list[ClaimTarget] = field(default_factory=list)


@dataclass(frozen=True)
class Unresolved:
    """Reading-flagged uncertainty surfaced from structure to the plan."""

    reading_section: str
    locator: str
    issue: str


@dataclass
class Plan:
    """In-memory representation of pending/<id>/plan.yaml."""

    source_id: str
    planned_at: str
    entity_resolutions: list[EntityResolution] = field(default_factory=list)
    new_entities: list[NewEntityProposal] = field(default_factory=list)
    planned_claims: list[PlannedClaim] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)


# ------------- to_dict ----------------------------------------------------


def _resolution_to_dict(r: EntityResolution) -> dict[str, Any]:
    out: dict[str, Any] = {
        "mention": r.mention,
        "mention_locations": list(r.mention_locations),
        "resolution": r.resolution,
    }
    if r.matched_entity is not None:
        out["matched_entity"] = r.matched_entity
    if r.proposed_entity_name is not None:
        out["proposed_entity_name"] = r.proposed_entity_name
    if r.proposed_category is not None:
        out["proposed_category"] = r.proposed_category
    if r.rationale:
        out["rationale"] = r.rationale
    if r.suggested_aliases_to_add:
        out["suggested_aliases_to_add"] = list(r.suggested_aliases_to_add)
    if r.human_review_needed:
        out["human_review_needed"] = True
    return out


def _new_entity_to_dict(n: NewEntityProposal) -> dict[str, Any]:
    out: dict[str, Any] = {"name": n.name, "category": n.category}
    if n.aliases_suggested:
        out["aliases_suggested"] = list(n.aliases_suggested)
    return out


def _target_to_dict(t: ClaimTarget) -> dict[str, Any]:
    out: dict[str, Any] = {
        "entity": t.entity,
        "entity_state": t.entity_state,
        "proposed_section": t.proposed_section,
    }
    if t.proposed_category is not None:
        out["proposed_category"] = t.proposed_category
    if t.rationale:
        out["rationale"] = t.rationale
    return out


def _claim_to_dict(c: PlannedClaim) -> dict[str, Any]:
    out: dict[str, Any] = {
        "claim_group_id": c.claim_group_id,
        "reading_section": c.reading_section,
        "reading_bullet_index": c.reading_bullet_index,
        "locator": c.locator,
        "locator_hint": c.locator_hint,
        "proposed_speaker": c.proposed_speaker,
        "proposed_status": c.proposed_status,
    }
    if c.proposed_status_reason is not None:
        out["proposed_status_reason"] = c.proposed_status_reason
    out["targets"] = [_target_to_dict(t) for t in c.targets]
    return out


def _unresolved_to_dict(u: Unresolved) -> dict[str, Any]:
    return {
        "reading_section": u.reading_section,
        "locator": u.locator,
        "issue": u.issue,
    }


def _to_dict(p: Plan) -> dict[str, Any]:
    return {
        "schema_version": _MAX_SCHEMA,
        "source_id": p.source_id,
        "planned_at": p.planned_at,
        "entity_resolutions": [_resolution_to_dict(r) for r in p.entity_resolutions],
        "new_entities": [_new_entity_to_dict(n) for n in p.new_entities],
        "planned_claims": [_claim_to_dict(c) for c in p.planned_claims],
        "unresolved": [_unresolved_to_dict(u) for u in p.unresolved],
    }


# ------------- parse_* (used by stage2 too) ------------------------------


def _str_list(raw: object, field_label: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        msg = f"{field_label}: expected a list, got {type(raw).__name__}"
        raise PlanError(msg)
    return [str(x) for x in raw]


def parse_resolution(raw: dict[str, Any]) -> EntityResolution:
    """Build an EntityResolution from a JSON/YAML mapping."""
    if not isinstance(raw, dict):
        msg = f"entity_resolutions: expected mapping, got {type(raw).__name__}"
        raise PlanError(msg)
    resolution = str(raw.get("resolution") or "")
    if resolution not in RESOLUTION_KINDS:
        msg = (
            f"entity_resolutions: resolution must be one of "
            f"{sorted(RESOLUTION_KINDS)}, got {resolution!r}"
        )
        raise PlanError(msg)
    proposed_category = raw.get("proposed_category")
    if proposed_category is not None and proposed_category not in CATEGORIES:
        msg = (
            f"entity_resolutions: proposed_category must be one of "
            f"{list(CATEGORIES)}, got {proposed_category!r}"
        )
        raise PlanError(msg)
    return EntityResolution(
        mention=str(raw.get("mention") or ""),
        mention_locations=_str_list(raw.get("mention_locations"), "mention_locations"),
        resolution=resolution,
        rationale=str(raw.get("rationale") or ""),
        matched_entity=(raw.get("matched_entity") or None),
        proposed_entity_name=(raw.get("proposed_entity_name") or None),
        proposed_category=proposed_category,
        suggested_aliases_to_add=_str_list(
            raw.get("suggested_aliases_to_add"), "suggested_aliases_to_add"
        ),
        human_review_needed=bool(raw.get("human_review_needed")),
    )


def parse_new_entity(raw: dict[str, Any]) -> NewEntityProposal:
    """Build a NewEntityProposal from a JSON/YAML mapping."""
    if not isinstance(raw, dict):
        msg = f"new_entities: expected mapping, got {type(raw).__name__}"
        raise PlanError(msg)
    name = str(raw.get("name") or "").strip()
    if not name:
        msg = "new_entities: empty name"
        raise PlanError(msg)
    category = str(raw.get("category") or "")
    if category not in CATEGORIES:
        msg = (
            f"new_entities: category must be one of {list(CATEGORIES)}, "
            f"got {category!r}"
        )
        raise PlanError(msg)
    return NewEntityProposal(
        name=name,
        category=category,
        aliases_suggested=_str_list(raw.get("aliases_suggested"), "aliases_suggested"),
    )


def parse_target(raw: dict[str, Any]) -> ClaimTarget:
    """Build a ClaimTarget from a JSON/YAML mapping."""
    if not isinstance(raw, dict):
        msg = f"targets: expected mapping, got {type(raw).__name__}"
        raise PlanError(msg)
    entity_state = str(raw.get("entity_state") or "")
    if entity_state not in ENTITY_STATES:
        msg = (
            f"targets: entity_state must be one of {sorted(ENTITY_STATES)}, "
            f"got {entity_state!r}"
        )
        raise PlanError(msg)
    proposed_category = raw.get("proposed_category")
    if entity_state == "new":
        if not proposed_category:
            msg = "targets: entity_state=new requires proposed_category"
            raise PlanError(msg)
        if proposed_category not in CATEGORIES:
            msg = (
                f"targets: proposed_category must be one of {list(CATEGORIES)}, "
                f"got {proposed_category!r}"
            )
            raise PlanError(msg)
    elif proposed_category is not None and proposed_category not in CATEGORIES:
        msg = (
            f"targets: proposed_category must be one of {list(CATEGORIES)}, "
            f"got {proposed_category!r}"
        )
        raise PlanError(msg)
    entity = str(raw.get("entity") or "").strip()
    if not entity:
        msg = "targets: empty entity"
        raise PlanError(msg)
    proposed_section = str(raw.get("proposed_section") or "").strip()
    if not proposed_section:
        msg = "targets: empty proposed_section"
        raise PlanError(msg)
    return ClaimTarget(
        entity=entity,
        entity_state=entity_state,
        proposed_section=proposed_section,
        rationale=str(raw.get("rationale") or ""),
        proposed_category=proposed_category or None,
    )


def parse_claim(raw: dict[str, Any]) -> PlannedClaim:
    """Build a PlannedClaim from a JSON/YAML mapping."""
    if not isinstance(raw, dict):
        msg = f"planned_claims: expected mapping, got {type(raw).__name__}"
        raise PlanError(msg)
    targets_raw = raw.get("targets") or []
    if not isinstance(targets_raw, list) or not targets_raw:
        msg = "planned_claims: 'targets' must be a non-empty list"
        raise PlanError(msg)
    targets = [parse_target(t) for t in targets_raw]
    bullet_idx_raw = raw.get("reading_bullet_index")
    if not isinstance(bullet_idx_raw, int) or bullet_idx_raw < 0:
        msg = (
            f"planned_claims: reading_bullet_index must be non-negative int, "
            f"got {bullet_idx_raw!r}"
        )
        raise PlanError(msg)
    return PlannedClaim(
        claim_group_id=str(raw.get("claim_group_id") or ""),
        reading_section=str(raw.get("reading_section") or ""),
        reading_bullet_index=bullet_idx_raw,
        locator=str(raw.get("locator") or ""),
        locator_hint=str(raw.get("locator_hint") or ""),
        proposed_speaker=str(raw.get("proposed_speaker") or ""),
        proposed_status=str(raw.get("proposed_status") or ""),
        proposed_status_reason=(raw.get("proposed_status_reason") or None),
        targets=targets,
    )


def parse_unresolved(raw: dict[str, Any]) -> Unresolved:
    """Build an Unresolved from a JSON/YAML mapping."""
    if not isinstance(raw, dict):
        msg = f"unresolved: expected mapping, got {type(raw).__name__}"
        raise PlanError(msg)
    return Unresolved(
        reading_section=str(raw.get("reading_section") or ""),
        locator=str(raw.get("locator") or ""),
        issue=str(raw.get("issue") or ""),
    )


# ------------- read / write ----------------------------------------------


def read(path: Path) -> Plan:
    """Read and parse plan.yaml.

    :raises PlanError: missing / malformed / unsupported schema
    """
    if not path.exists():
        msg = f"{path}: file not found"
        raise PlanError(msg)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: expected a YAML mapping"
        raise PlanError(msg)
    try:
        read_schema_version(raw, str(path), max_supported=_MAX_SCHEMA)
    except SchemaVersionError as e:
        raise PlanError(str(e)) from e
    source_id_raw = raw.get("source_id")
    planned_at_raw = raw.get("planned_at")
    if not source_id_raw:
        msg = f"{path}: missing source_id"
        raise PlanError(msg)
    if not planned_at_raw:
        msg = f"{path}: missing planned_at"
        raise PlanError(msg)
    try:
        return Plan(
            source_id=str(source_id_raw),
            planned_at=str(planned_at_raw),
            entity_resolutions=[
                parse_resolution(r) for r in (raw.get("entity_resolutions") or [])
            ],
            new_entities=[parse_new_entity(n) for n in (raw.get("new_entities") or [])],
            planned_claims=[parse_claim(c) for c in (raw.get("planned_claims") or [])],
            unresolved=[parse_unresolved(u) for u in (raw.get("unresolved") or [])],
        )
    except PlanError:
        raise
    except (KeyError, ValueError, TypeError) as e:
        msg = f"{path}: malformed plan ({e})"
        raise PlanError(msg) from e


def write(plan: Plan, path: Path) -> None:
    """Atomically write plan.yaml."""
    text = yaml.safe_dump(
        _to_dict(plan),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    atomic_write_text(path, text)


# ------------- DB API -------------------------------------------------------


def write_plan_routes(
    conn: sqlite3.Connection,
    ingest_id: str,
    plan: Plan,
) -> None:
    """DELETE+INSERT plan_metadata + plan_routes for ingest_id. Caller owns tx."""
    conn.execute("DELETE FROM plan_routes WHERE ingest_id=?", (ingest_id,))
    conn.execute("DELETE FROM plan_metadata WHERE ingest_id=?", (ingest_id,))

    conn.execute(
        """
        INSERT INTO plan_metadata
            (ingest_id, planned_at, source_id,
             entity_resolutions_json, new_entities_json, unresolved_json)
        VALUES (?,?,?,?,?,?)
        """,
        (
            ingest_id,
            plan.planned_at,
            plan.source_id,
            json.dumps([_resolution_to_dict(r) for r in plan.entity_resolutions]),
            json.dumps([_new_entity_to_dict(n) for n in plan.new_entities]),
            json.dumps([_unresolved_to_dict(u) for u in plan.unresolved]),
        ),
    )

    for claim in plan.planned_claims:
        for target in claim.targets:
            conn.execute(
                """
                INSERT INTO plan_routes (
                    ingest_id, claim_group_id,
                    target_entity_category, target_entity_slug,
                    target_entity_name, entity_state, proposed_section,
                    proposed_speaker, proposed_status, proposed_status_reason,
                    locator, locator_hint, reading_section, reading_bullet_index,
                    rationale, matched_via
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
                """,
                (
                    ingest_id,
                    claim.claim_group_id,
                    target.proposed_category if target.entity_state == "new" else None,
                    None,  # slug resolved later
                    target.entity,
                    target.entity_state,
                    target.proposed_section,
                    claim.proposed_speaker or None,
                    claim.proposed_status,
                    claim.proposed_status_reason,
                    claim.locator,
                    claim.locator_hint,
                    claim.reading_section,
                    claim.reading_bullet_index,
                    target.rationale or None,
                ),
            )


def read_plan_routes(
    conn: sqlite3.Connection,
    ingest_id: str,
) -> Plan | None:
    """Return Plan from DB or None if no plan_metadata row."""
    row = conn.execute(
        "SELECT planned_at, source_id, entity_resolutions_json,"
        " new_entities_json, unresolved_json"
        " FROM plan_metadata WHERE ingest_id=?",
        (ingest_id,),
    ).fetchone()
    if row is None:
        return None

    planned_at, source_id, er_json, ne_json, u_json = row
    try:
        entity_resolutions = [
            parse_resolution(r) for r in (json.loads(er_json or "[]") or [])
        ]
        new_entities = [
            parse_new_entity(n) for n in (json.loads(ne_json or "[]") or [])
        ]
        unresolved = [parse_unresolved(u) for u in (json.loads(u_json or "[]") or [])]
    except (PlanError, json.JSONDecodeError) as exc:
        _logger.warning(
            "read_plan_routes: malformed metadata for %s: %s", ingest_id, exc
        )
        entity_resolutions = []
        new_entities = []
        unresolved = []

    routes = conn.execute(
        """
        SELECT claim_group_id, target_entity_name, entity_state, proposed_section,
               proposed_speaker, proposed_status, proposed_status_reason,
               locator, locator_hint, reading_section, reading_bullet_index,
               rationale, target_entity_category
        FROM plan_routes
        WHERE ingest_id=?
        ORDER BY id
        """,
        (ingest_id,),
    ).fetchall()

    # reconstruct planned_claims: group by claim_group_id preserving order
    claims_map: dict[str, dict[str, Any]] = {}
    claim_order: list[str] = []
    for r in routes:
        (
            cgid,
            entity_name,
            estate,
            esection,
            espeaker,
            estatus,
            estatus_reason,
            locator,
            locator_hint,
            rsection,
            rbullet,
            rationale,
            ecat,
        ) = r
        if cgid not in claims_map:
            claims_map[cgid] = {
                "claim_group_id": cgid,
                "reading_section": rsection,
                "reading_bullet_index": rbullet,
                "locator": locator,
                "locator_hint": locator_hint,
                "proposed_speaker": espeaker or "",
                "proposed_status": estatus,
                "proposed_status_reason": estatus_reason,
                "targets": [],
            }
            claim_order.append(cgid)
        claims_map[cgid]["targets"].append({
            "entity": entity_name,
            "entity_state": estate,
            "proposed_section": esection,
            "rationale": rationale or "",
            "proposed_category": ecat,
        })

    planned_claims: list[PlannedClaim] = []
    for cgid in claim_order:
        c = claims_map[cgid]
        targets = [parse_target(t) for t in c["targets"]]
        planned_claims.append(
            PlannedClaim(
                claim_group_id=c["claim_group_id"],
                reading_section=c["reading_section"],
                reading_bullet_index=c["reading_bullet_index"],
                locator=c["locator"],
                locator_hint=c["locator_hint"],
                proposed_speaker=c["proposed_speaker"],
                proposed_status=c["proposed_status"],
                proposed_status_reason=c["proposed_status_reason"],
                targets=targets,
            )
        )

    return Plan(
        source_id=source_id,
        planned_at=planned_at,
        entity_resolutions=entity_resolutions,
        new_entities=new_entities,
        planned_claims=planned_claims,
        unresolved=unresolved,
    )


def list_plans(
    conn: sqlite3.Connection,
) -> list[tuple[str, str, int, int]]:
    """Return [(source_id, planned_at, claim_count, new_entity_count), ...]."""
    rows = conn.execute(
        "SELECT ingest_id, planned_at, source_id, new_entities_json FROM plan_metadata"
        " ORDER BY planned_at"
    ).fetchall()
    result: list[tuple[str, str, int, int]] = []
    for ingest_id, planned_at, source_id, ne_json in rows:
        claim_count = conn.execute(
            "SELECT COUNT(DISTINCT claim_group_id) FROM plan_routes WHERE ingest_id=?",
            (ingest_id,),
        ).fetchone()[0]
        try:
            ne_count = len(json.loads(ne_json or "[]"))
        except json.JSONDecodeError:
            ne_count = 0
        result.append((source_id, planned_at, claim_count, ne_count))
    return result
