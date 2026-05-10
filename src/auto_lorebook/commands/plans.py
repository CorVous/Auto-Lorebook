"""auto-lorebook plans subcommand group.

Inspection-only commands. Mirrors `entities` shape: nested subparser
with action dispatch on `args.plans_action`. Plans are intermediate
artifacts in `~/.auto-lorebook/pending/<source_id>/plan.yaml` — there
is no approval gate at this stage.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import plan_yaml
from auto_lorebook import wiki_state as wiki_state_mod

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the `plans` subcommand group."""
    parser = subparsers.add_parser(
        "plans",
        parents=[common_parser],
        help="List or show pending Stage 2 plans",
        description=(
            "Inspect Stage 2 plans. Plans are intermediate artifacts; the "
            "planner runs automatically after `approve-reading`. There is "
            "no approval gate."
        ),
    )
    sub = parser.add_subparsers(
        dest="plans_action",
        required=True,
        help="plans subcommand",
    )

    p_list = sub.add_parser(
        "list",
        parents=[common_parser],
        help="List pending plans across all ingests",
    )
    p_list.set_defaults(func=run)

    p_show = sub.add_parser(
        "show",
        parents=[common_parser],
        help="Show one plan by source/ingest id",
    )
    p_show.add_argument("source_id", help="source/ingest id")
    p_show.set_defaults(func=run)

    return parser


def run(args: argparse.Namespace) -> int:
    """Dispatch to the matching `_run_*` based on `plans_action`."""
    action = args.plans_action
    if action == "list":
        return _run_list()
    if action == "show":
        return _run_show(args.source_id)
    msg = f"unknown plans action: {action}"
    raise ValueError(msg)


def _pending_root() -> Path:
    wiki = cfg_mod.load_config().resolve_active_wiki(None)
    return wiki_state_mod.pending_dir(wiki)


def _run_list() -> int:
    root = _pending_root()
    if not root.is_dir():
        print("(no plans)")  # noqa: T201
        return 0
    rows: list[tuple[str, str, int, int]] = []
    for sub in sorted(root.iterdir()):
        plan_path = sub / "plan.yaml"
        if not plan_path.is_file():
            continue
        try:
            plan = plan_yaml.read(plan_path)
        except plan_yaml.PlanError:
            _logger.warning("plans list: could not parse %s; skipping", plan_path)
            continue
        rows.append((
            plan.source_id,
            plan.planned_at,
            len(plan.planned_claims),
            len(plan.new_entities),
        ))
    if not rows:
        print("(no plans)")  # noqa: T201
        return 0
    sid_w = max(len("SOURCE"), *(len(r[0]) for r in rows))
    when_w = max(len("PLANNED_AT"), *(len(r[1]) for r in rows))
    header = f"{'SOURCE':<{sid_w}}  {'PLANNED_AT':<{when_w}}  CLAIMS  NEW_ENTITIES"
    print(header)  # noqa: T201
    for sid, when, claims, new_e in rows:
        print(  # noqa: T201
            f"{sid:<{sid_w}}  {when:<{when_w}}  {claims:>6}  {new_e:>12}"
        )
    return 0


def _run_show(source_id: str) -> int:
    wiki = cfg_mod.load_config().resolve_active_wiki(None)
    plan_path = wiki_state_mod.pending_plan_path(wiki, source_id)
    if not plan_path.is_file():
        print(f"No plan for {source_id!r}")  # noqa: T201
        return 1
    try:
        plan = plan_yaml.read(plan_path)
    except plan_yaml.PlanError as e:
        print(f"error: {e}")  # noqa: T201
        return 1

    print(f"source_id:  {plan.source_id}")  # noqa: T201
    print(f"planned_at: {plan.planned_at}")  # noqa: T201

    print()  # noqa: T201
    print(f"entity_resolutions ({len(plan.entity_resolutions)}):")  # noqa: T201
    for r in plan.entity_resolutions:
        target = (
            r.matched_entity
            if r.resolution == "existing"
            else r.proposed_entity_name
            if r.resolution == "new"
            else "(ambiguous)"
        )
        flag = " [review-needed]" if r.human_review_needed else ""
        print(f"  - {r.mention!r} → {r.resolution}: {target}{flag}")  # noqa: T201
        if r.rationale:
            print(f"      rationale: {r.rationale}")  # noqa: T201

    print()  # noqa: T201
    print(f"new_entities ({len(plan.new_entities)}):")  # noqa: T201
    for n in plan.new_entities:
        aliases = (
            f" (aliases: {', '.join(n.aliases_suggested)})"
            if n.aliases_suggested
            else ""
        )
        print(f"  - {n.category}/{n.name}{aliases}")  # noqa: T201

    print()  # noqa: T201
    print(f"planned_claims ({len(plan.planned_claims)}):")  # noqa: T201
    for c in plan.planned_claims:
        print(  # noqa: T201
            f"  - {c.claim_group_id} {c.reading_section} "
            f"[{c.locator}] {c.proposed_status}"
        )
        for t in c.targets:
            tag = f" (new: {t.proposed_category})" if t.entity_state == "new" else ""
            print(f"      → {t.entity}/{t.proposed_section}{tag}")  # noqa: T201

    print()  # noqa: T201
    print(f"unresolved ({len(plan.unresolved)}):")  # noqa: T201
    for u in plan.unresolved:
        print(f"  - {u.reading_section} [{u.locator}] {u.issue}")  # noqa: T201

    return 0
