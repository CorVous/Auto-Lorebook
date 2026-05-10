"""auto-lorebook entities subcommand group.

First nested subparser in the project: `entities <list|show|new|rebuild-index>`.
Subcommands all dispatch through `run()`, branching on `args.entities_action`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import entity_index, entity_yaml
from auto_lorebook.entity_yaml import slugify
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    import argparse

    from auto_lorebook.entity_yaml import Entity

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the `entities` subcommand group."""
    parser = subparsers.add_parser(
        "entities",
        parents=[common_parser],
        help="List, show, or hand-create entities in the wiki",
        description=(
            "Inspect and bootstrap entities. Hand-creation is a "
            "bootstrapping aid; the normal path is approval of facts "
            "in the review loop (Phase 4)."
        ),
    )
    sub = parser.add_subparsers(
        dest="entities_action",
        required=True,
        help="entities subcommand",
    )

    p_list = sub.add_parser(
        "list",
        parents=[common_parser],
        help="List entities in the wiki",
    )
    p_list.add_argument(
        "--category",
        choices=entity_yaml.CATEGORIES,
        help="Filter by category",
    )
    p_list.add_argument(
        "--created-by",
        dest="created_by",
        help="Filter by created_by_ingest",
    )
    p_list.set_defaults(func=run)

    p_show = sub.add_parser(
        "show",
        parents=[common_parser],
        help="Show one entity (resolved by slug, name, or alias)",
    )
    p_show.add_argument(
        "query",
        help="Entity slug, canonical name, or alias",
    )
    p_show.set_defaults(func=run)

    p_new = sub.add_parser(
        "new",
        parents=[common_parser],
        help="Hand-create a minimal entity stub",
    )
    p_new.add_argument(
        "--category",
        required=True,
        choices=entity_yaml.CATEGORIES,
    )
    p_new.add_argument("--name", required=True, help="Canonical entity name")
    p_new.add_argument(
        "--slug",
        default=None,
        help="Optional slug; defaults to a slugified --name",
    )
    p_new.set_defaults(func=run)

    p_rebuild = sub.add_parser(
        "rebuild-index",
        parents=[common_parser],
        help="Rebuild the in-memory entity index (no cache yet; placeholder)",
    )
    p_rebuild.set_defaults(func=run)

    return parser


def run(args: argparse.Namespace) -> int:
    """Dispatch to the matching `_run_*` based on `entities_action`."""
    action = args.entities_action
    cfg = cfg_mod.load_config()
    wiki = cfg.resolve_active_wiki(getattr(args, "wiki", None))

    if action == "list":
        return _run_list(args, wiki)
    if action == "show":
        return _run_show(args, wiki)
    if action == "new":
        return _run_new(args, wiki)
    if action == "rebuild-index":
        return _run_rebuild_index(wiki)
    msg = f"unknown entities action: {action}"
    raise ValueError(msg)


def _run_list(args: argparse.Namespace, wiki) -> int:  # noqa: ANN001
    entities = entity_yaml.scan(wiki)
    if args.category:
        entities = [e for e in entities if e.category == args.category]
    if args.created_by:
        entities = [e for e in entities if e.created_by_ingest == args.created_by]
    if not entities:
        print("(no entities)")  # noqa: T201
        return 0

    entities.sort(key=lambda e: (e.category, e.entity.casefold()))
    # column widths
    cat_w = max(len("CATEGORY"), *(len(e.category) for e in entities))
    name_w = max(len("NAME"), *(len(e.entity) for e in entities))
    slug_w = max(len("SLUG"), *(len(e.slug) for e in entities))
    header = f"{'CATEGORY':<{cat_w}}  {'NAME':<{name_w}}  {'SLUG':<{slug_w}}  ALIASES"
    print(header)  # noqa: T201
    for e in entities:
        print(  # noqa: T201
            f"{e.category:<{cat_w}}  {e.entity:<{name_w}}  {e.slug:<{slug_w}}  "
            f"{len(e.aliases)}"
        )
    return 0


def _resolve(entities: list[Entity], query: str) -> list[Entity]:
    """Return matches in resolution order: slug → name → alias."""
    q_norm = entity_yaml.normalize_alias_name(query)
    by_slug = [e for e in entities if e.slug == query]
    if by_slug:
        return by_slug
    by_name = [e for e in entities if e.entity.casefold() == query.casefold()]
    if by_name:
        return by_name
    return [
        e
        for e in entities
        if any(entity_yaml.normalize_alias_name(a.name) == q_norm for a in e.aliases)
    ]


def _run_show(args: argparse.Namespace, wiki) -> int:  # noqa: ANN001
    entities = entity_yaml.scan(wiki)
    matches = _resolve(entities, args.query)
    if not matches:
        print(f"No entity matching {args.query!r}")  # noqa: T201
        return 1
    if len(matches) > 1:
        print(f"Multiple matches for {args.query!r}:")  # noqa: T201
        for e in matches:
            print(f"  - {e.category}/{e.slug}  ({e.entity})")  # noqa: T201
        return 1

    e = matches[0]
    print(f"entity:    {e.entity}")  # noqa: T201
    print(f"category:  {e.category}")  # noqa: T201
    print(f"slug:      {e.slug}")  # noqa: T201
    if e.superseded_by:
        print(f"superseded_by: {e.superseded_by}")  # noqa: T201
    if e.created_by_ingest:
        print(f"created_by_ingest: {e.created_by_ingest}")  # noqa: T201
    if e.created_at:
        print(f"created_at: {e.created_at}")  # noqa: T201
    if e.aliases:
        print("aliases:")  # noqa: T201
        for a in e.aliases:
            tag = f" ({a.source})" if a.source else ""
            print(f"  - {a.name}{tag}")  # noqa: T201
    else:
        print("aliases: (none)")  # noqa: T201
    print("facts: (no facts yet — populated in Phase 4)")  # noqa: T201
    return 0


def _run_new(args: argparse.Namespace, wiki) -> int:  # noqa: ANN001
    slug = args.slug or slugify(args.name)
    if not slug:
        print(f"error: could not derive a slug from {args.name!r}; pass --slug")  # noqa: T201
        return 1
    cat_dir = wiki / args.category
    cat_dir.mkdir(parents=True, exist_ok=True)
    target = cat_dir / f"{slug}.yaml"
    if target.exists():
        print(  # noqa: T201
            f"error: {target} already exists; use `entities show {slug}` to inspect it"
        )
        return 1
    e = entity_yaml.Entity(
        entity=args.name,
        category=args.category,
        slug=slug,
        created_at=format_iso_now(),
    )
    entity_yaml.write(e, target)
    print(f"created {target}")  # noqa: T201
    return 0


def _run_rebuild_index(wiki) -> int:  # noqa: ANN001
    entity_index.build(wiki)
    print("(index rebuilt from filesystem; no cache in use)")  # noqa: T201
    return 0
