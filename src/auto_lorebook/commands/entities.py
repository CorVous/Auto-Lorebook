"""auto-lorebook entities subcommand group.

First nested subparser in the project: `entities <list|show|new|rebuild-index>`.
Subcommands all dispatch through `run()`, branching on `args.entities_action`.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import db as db_mod
from auto_lorebook import entities as entities_mod
from auto_lorebook import entity_yaml
from auto_lorebook.entities import slugify
from auto_lorebook.timestamps import format_iso_now

if TYPE_CHECKING:
    import argparse

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
        help="Legacy no-op; entities live in the wiki SQLite DB",
    )
    p_rebuild.set_defaults(func=run)

    return parser


def run(args: argparse.Namespace) -> int:
    """Dispatch to the matching `_run_*` based on `entities_action`."""
    action = args.entities_action
    cfg = cfg_mod.load_config()
    wiki = cfg.resolve_active_wiki(getattr(args, "wiki", None))
    conn = db_mod.open(wiki / ".wiki-state" / "wiki.db")

    if action == "list":
        return _run_list(args, wiki, conn)
    if action == "show":
        return _run_show(args, wiki, conn)
    if action == "new":
        return _run_new(args, wiki, conn)
    if action == "rebuild-index":
        return _run_rebuild_index()
    msg = f"unknown entities action: {action}"
    raise ValueError(msg)


def _run_list(args: argparse.Namespace, wiki, conn) -> int:  # noqa: ANN001

    rows = entities_mod.list_entities(conn, args.category, wiki_repo=wiki)
    if args.created_by:
        rows = [r for r in rows if r.created_by_ingest == args.created_by]
    if not rows:
        print("(no entities)")  # noqa: T201
        return 0

    # column widths
    cat_w = max(len("CATEGORY"), *(len(r.category) for r in rows))
    name_w = max(len("NAME"), *(len(r.canonical_name) for r in rows))
    slug_w = max(len("SLUG"), *(len(r.slug) for r in rows))
    header = f"{'CATEGORY':<{cat_w}}  {'NAME':<{name_w}}  {'SLUG':<{slug_w}}  ALIASES"
    print(header)  # noqa: T201
    for r in rows:
        alias_count = len(entities_mod.list_aliases(conn, r.category, r.slug))
        print(  # noqa: T201
            f"{r.category:<{cat_w}}  {r.canonical_name:<{name_w}}  {r.slug:<{slug_w}}  "
            f"{alias_count}"
        )
    return 0


def _run_show(args: argparse.Namespace, wiki, conn) -> int:  # noqa: ANN001
    """3-tier resolution: slug → canonical_name → alias."""
    # backfill from YAML if DB empty
    entities_mod.list_entities(conn, wiki_repo=wiki)
    matches = entities_mod.search_entities(conn, args.query)
    if not matches:
        print(f"No entity matching {args.query!r}")  # noqa: T201
        return 1
    if len(matches) > 1:
        print(f"Multiple matches for {args.query!r}:")  # noqa: T201
        for m in matches:
            print(f"  - {m.category}/{m.slug}  ({m.canonical_name})")  # noqa: T201
        return 1

    m = matches[0]
    print(f"entity:    {m.canonical_name}")  # noqa: T201
    print(f"category:  {m.category}")  # noqa: T201
    print(f"slug:      {m.slug}")  # noqa: T201
    if m.superseded_by_category:
        print(f"superseded_by: {m.superseded_by_category}/{m.superseded_by_slug}")  # noqa: T201
    print(f"created_by_ingest: {m.created_by_ingest}")  # noqa: T201
    print(f"created_at: {m.created_at}")  # noqa: T201
    aliases = entities_mod.list_aliases(conn, m.category, m.slug)
    if aliases:
        print("aliases:")  # noqa: T201
        for a in aliases:
            tag = f" ({a.source})" if a.source else ""
            print(f"  - {a.name}{tag}")  # noqa: T201
    else:
        print("aliases: (none)")  # noqa: T201
    print("facts: (no facts yet — populated in Phase 4)")  # noqa: T201
    return 0


def _run_new(args: argparse.Namespace, wiki, conn) -> int:  # noqa: ANN001
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
    now = format_iso_now()
    # YAML write (dual-write: YAML first, then DB)
    e = entity_yaml.Entity(
        entity=args.name,
        category=args.category,
        slug=slug,
        created_at=now,
    )
    entity_yaml.write(e, target)
    # DB write (idempotent on re-run)
    with contextlib.suppress(entities_mod.EntityError):
        entities_mod.create_entity(
            conn,
            category=args.category,
            slug=slug,
            canonical_name=args.name,
            ingest_id="cli-new",
            when=now,
        )
    print(f"created {target}")  # noqa: T201
    return 0


def _run_rebuild_index() -> int:
    print("(DB-backed; index always current)")  # noqa: T201
    return 0
