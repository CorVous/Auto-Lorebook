"""Stage 2: Planner.

Routes claims from an approved reading to one or more entities and
emits a `Plan` ready to write to ``pending/<source_id>/plan.yaml``.

No filesystem side effects in this module — orchestration writes the
artifact via :func:`auto_lorebook.plan_yaml.write`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from auto_lorebook import plan_yaml
from auto_lorebook.llm_helpers import build_system_prompt, parse_json_object
from auto_lorebook.plan_yaml import Plan
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    from auto_lorebook.openrouter import OpenRouterClient
    from auto_lorebook.stage1b import ReadingBullets
    from auto_lorebook.structure import Structure

_logger = logging.getLogger(__name__)

_TASK_INSTRUCTIONS = """\
You are routing claim bullets from an approved wiki reading to entities
in a worldbuilding wiki. The entity index in the preamble lists every
existing entity with its category and aliases. Read the reading body and
its bullets and emit a single JSON object matching this schema exactly:

{
  "entity_resolutions": [
    {
      "mention": "<text as it appears>",
      "mention_locations": ["[h:mm:ss-h:mm:ss] short context"],
      "resolution": "existing" | "new" | "ambiguous",
      "matched_entity": null | "<canonical name from the index>",
      "proposed_entity_name": null | "<name>",
      "proposed_category": null | "characters" | "locations" | "factions"
                                | "events" | "items" | "concepts",
      "rationale": "<one sentence>",
      "suggested_aliases_to_add": [],
      "human_review_needed": false
    }
  ],
  "new_entities": [
    {"name": "<name>", "category": "<category>", "aliases_suggested": []}
  ],
  "planned_claims": [
    {
      "claim_group_id": "cg-001",
      "reading_section": "[h:mm:ss-h:mm:ss] <segment title>",
      "reading_bullet_index": 0,
      "locator": "h:mm:ss",
      "locator_hint": "h:mm:ss-h:mm:ss",
      "proposed_speaker": "<speaker name>",
      "proposed_status": "authoritative" | "hearsay" | "speculation",
      "proposed_status_reason": null | "<note>",
      "targets": [
        {
          "entity": "<canonical name>",
          "entity_state": "existing" | "new",
          "proposed_section": "<short section name>",
          "proposed_category": null | "<category if entity_state=new>",
          "rationale": "<why this claim concerns this entity>"
        }
      ]
    }
  ],
  "unresolved": [
    {
      "reading_section": "[h:mm:ss-h:mm:ss] <segment title>",
      "locator": "h:mm:ss",
      "issue": "<short note>"
    }
  ]
}

Hard rules:
- Route a claim to a target entity only when the claim CONCERNS that
  entity, not merely mentions it. "Theron met Aelindra at the Festival
  of Masks" routes to Theron and Aelindra, NOT to the Festival.
- Single-target routing is the common case; use multi-target only when
  the claim genuinely carries information about more than one entity.
- `claim_group_id` MUST be unique per claim across the plan
  (cg-001, cg-002, ...). Sibling targets sharing one bullet share one id.
- `matched_entity` (when resolution=existing) MUST be a canonical name
  shown in the entity index. If you can't find a clean match, prefer
  resolution=new or resolution=ambiguous.
- `entity_state=new` targets MUST also appear in `new_entities` with a
  matching name and category, and the target MUST set `proposed_category`.
- `mention_locations` should be drawn from the reading sections /
  bullet anchors so a human can audit the routing.
- Mirror reading-flagged uncertainty (uncertain names, attribution) into
  `unresolved` with the original locator.
- Every `entity_resolutions[i]` whose resolution is "ambiguous" MUST set
  human_review_needed=true.

Emit ONLY the JSON object. No prose, no code fences, no commentary.
"""


class Stage2Error(RuntimeError):
    """Stage 2 failed: bad JSON or schema violation."""


def run(
    *,
    reading_text: str,
    structure: Structure,
    bullets: ReadingBullets,
    preamble_text: str,
    source_id: str,
    client: OpenRouterClient,
    model: str,
) -> Plan:
    """Run Stage 2 against an approved reading and produce a Plan.

    :param reading_text: full ``reading.md`` body (frontmatter included)
    :param structure: Stage 1a output (segment ids, uncertainty flags)
    :param bullets: Stage 1b output (per-segment claims with anchors)
    :raises Stage2Error: bad LLM output or schema violation
    """
    user_msg = _build_user(reading_text, structure, bullets)
    messages = [
        {
            "role": "system",
            "content": build_system_prompt(preamble_text, _TASK_INSTRUCTIONS),
        },
        {"role": "user", "content": user_msg},
    ]
    resp = client.complete(
        messages,
        model=model,
        response_format={"type": "json_object"},
    )
    try:
        payload = parse_json_object(resp.text, "Stage 2")
    except ValueError as e:
        raise Stage2Error(str(e)) from e
    return _payload_to_plan(payload, source_id=source_id)


def _build_user(
    reading_text: str,
    structure: Structure,
    bullets: ReadingBullets,
) -> str:
    """Assemble the user message from reading + structure + bullets."""
    parts: list[str] = [
        "Approved reading:",
        "",
        reading_text.rstrip(),
        "",
        "Bullet index per segment (use these for reading_bullet_index):",
    ]
    for seg in structure.segments:
        seg_bullets = bullets.segments.get(seg.id, [])
        if not seg_bullets:
            parts.append(f"- {seg.id} {seg.title!r}: (no bullets)")
            continue
        parts.append(f"- {seg.id} {seg.title!r}:")
        parts.extend(f"    [{idx}] {b.text}" for idx, b in enumerate(seg_bullets))
    if structure.uncertainty_flags:
        parts.extend([
            "",
            "Reading-flagged uncertainty (mirror into `unresolved` as appropriate):",
        ])
        for flag in structure.uncertainty_flags:
            note = f"; {flag.note}" if flag.note else ""
            parts.append(
                f"- locator={flag.locator:.0f}s kind={flag.kind} "
                f"span={flag.span!r}{note}"
            )
    return "\n".join(parts)


def _payload_to_plan(payload: dict[str, Any], *, source_id: str) -> Plan:
    try:
        resolutions = [
            plan_yaml.parse_resolution(r)
            for r in (payload.get("entity_resolutions") or [])
        ]
        new_entities = [
            plan_yaml.parse_new_entity(n) for n in (payload.get("new_entities") or [])
        ]
        claims = [
            plan_yaml.parse_claim(c) for c in (payload.get("planned_claims") or [])
        ]
        unresolved = [
            plan_yaml.parse_unresolved(u) for u in (payload.get("unresolved") or [])
        ]
    except plan_yaml.PlanError as e:
        msg = f"Stage 2 schema violation: {e}"
        raise Stage2Error(msg) from e
    return Plan(
        source_id=source_id,
        planned_at=format_iso_now(),
        entity_resolutions=resolutions,
        new_entities=new_entities,
        planned_claims=claims,
        unresolved=unresolved,
    )
