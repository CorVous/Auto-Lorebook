"""auto-lorebook wiki subcommand group.

Registry management: `wiki <list|use|add|remove|rename|rebuild>`.
All dispatch through `run()` on `args.wiki_action`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import db, wiki_state
from auto_lorebook import wiki_bootstrap as wiki_bootstrap_mod
from auto_lorebook.config import save_config
from auto_lorebook.openrouter import OpenRouterClient
from auto_lorebook.wiki_registry import WikiEntry, WikiRegistry, WikiRegistryError

if TYPE_CHECKING:
    import argparse

_logger = logging.getLogger(__name__)

_ENTITY_CATEGORIES = (
    "characters",
    "locations",
    "factions",
    "events",
    "items",
    "concepts",
)


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the `wiki` subcommand group."""
    parser = subparsers.add_parser(
        "wiki",
        parents=[common_parser],
        help="Manage registered wikis",
        description="Register, switch, list, rename, and remove wikis.",
    )
    sub = parser.add_subparsers(
        dest="wiki_action",
        required=True,
        help="wiki subcommand",
    )

    # list
    p_list = sub.add_parser(
        "list",
        parents=[common_parser],
        help="List registered wikis; active entry marked with *",
    )
    p_list.set_defaults(func=run)

    # use
    p_use = sub.add_parser(
        "use",
        parents=[common_parser],
        help="Switch active wiki (by nickname or path; bootstraps new dirs)",
    )
    p_use.add_argument(
        "target",
        help="Known nickname or filesystem path to a wiki directory",
    )
    p_use.add_argument(
        "--name",
        default=None,
        help="Nickname for a path-based registration (defaults to basename)",
    )
    p_use.set_defaults(func=run)

    # add
    p_add = sub.add_parser(
        "add",
        parents=[common_parser],
        help="Register a wiki without switching the active pointer",
    )
    p_add.add_argument("nickname", help="Unique nickname for this wiki")
    p_add.add_argument("path", help="Filesystem path to the wiki directory")
    p_add.set_defaults(func=run)

    # remove
    p_remove = sub.add_parser(
        "remove",
        parents=[common_parser],
        help="Deregister a wiki (refuses if it is the active entry)",
    )
    p_remove.add_argument("nickname", help="Nickname to remove")
    p_remove.set_defaults(func=run)

    # rename
    p_rename = sub.add_parser(
        "rename",
        parents=[common_parser],
        help="Rename a wiki entry; updates active pointer if matched",
    )
    p_rename.add_argument("old", help="Current nickname")
    p_rename.add_argument("new", help="New nickname")
    p_rename.set_defaults(func=run)

    # rebuild
    p_rebuild = sub.add_parser(
        "rebuild",
        parents=[common_parser],
        help="Regenerate all entity pages; delete orphan .md files",
        description=(
            "Regenerate every entity page from scratch and reconcile the "
            "filesystem against the DB — deletes any .md file with no matching entity."
        ),
    )
    p_rebuild.add_argument(
        "--force",
        action="store_true",
        help="Regenerate every page even if inputs are unchanged",
    )
    p_rebuild.set_defaults(func=run)

    return parser


def run(args: argparse.Namespace) -> int:
    """Dispatch to matching `_run_*` based on `wiki_action`."""
    action = args.wiki_action
    home: Path | None = None  # respects AUTO_LOREBOOK_HOME via config_dir()
    if action == "list":
        return _run_list(home)
    if action == "use":
        return _run_use(args, home)
    if action == "add":
        return _run_add(args, home)
    if action == "remove":
        return _run_remove(args, home)
    if action == "rename":
        return _run_rename(args, home)
    if action == "rebuild":
        return _run_rebuild(args, home)
    msg = f"unknown wiki action: {action}"
    raise ValueError(msg)


def _build_registry(cfg: cfg_mod.Config) -> WikiRegistry:
    return WikiRegistry(
        entries=list(cfg.wikis),
        active=cfg.active_wiki,
    )


def _run_list(home: Path | None) -> int:
    cfg = cfg_mod.load_config(home=home)
    if not cfg.wikis:
        print("(no wikis registered)")  # noqa: T201
        return 0
    for entry in cfg.wikis:
        marker = "* " if entry.nickname == cfg.active_wiki else "  "
        print(f"{marker}{entry.nickname}  {entry.path}")  # noqa: T201
    return 0


def _run_add(args: argparse.Namespace, home: Path | None) -> int:
    wiki_path = Path(args.path)
    if not wiki_path.exists():
        print(f"error: path does not exist: {wiki_path}")  # noqa: T201
        return 1
    cfg = cfg_mod.load_config(home=home)
    reg = _build_registry(cfg)
    try:
        reg.add(WikiEntry(args.nickname, wiki_path))
    except WikiRegistryError as e:
        print(f"error: {e}")  # noqa: T201
        return 1
    cfg.wikis = reg.entries
    save_config(cfg, home=home)
    print(f"registered {args.nickname!r} → {wiki_path}")  # noqa: T201
    return 0


def _run_remove(args: argparse.Namespace, home: Path | None) -> int:
    cfg = cfg_mod.load_config(home=home)
    reg = _build_registry(cfg)
    try:
        reg.remove(args.nickname)
    except WikiRegistryError as e:
        msg = str(e)
        if "active" in msg:
            print(  # noqa: T201
                f"error: {msg}. Switch to another wiki first (`wiki use <nickname>`)."
            )
        else:
            print(f"error: {msg}")  # noqa: T201
        return 1
    cfg.wikis = reg.entries
    save_config(cfg, home=home)
    print(f"removed {args.nickname!r}")  # noqa: T201
    return 0


def _run_rename(args: argparse.Namespace, home: Path | None) -> int:
    cfg = cfg_mod.load_config(home=home)
    reg = _build_registry(cfg)
    try:
        reg.rename(args.old, args.new)
    except WikiRegistryError as e:
        print(f"error: {e}")  # noqa: T201
        return 1
    cfg.wikis = reg.entries
    cfg.active_wiki = reg.active
    save_config(cfg, home=home)
    print(f"renamed {args.old!r} → {args.new!r}")  # noqa: T201
    return 0


def _run_use(args: argparse.Namespace, home: Path | None) -> int:
    cfg = cfg_mod.load_config(home=home)
    reg = _build_registry(cfg)
    target = args.target

    # known nickname → just switch (and ensure DB is initialised)
    known = {e.nickname: e for e in reg.entries}
    if target in known:
        reg.set_active(target)
        cfg.active_wiki = reg.active
        save_config(cfg, home=home)
        reg_path = known[target].path
        db.open(wiki_state.wiki_db_path(reg_path)).close()
        print(f"active wiki: {target!r}")  # noqa: T201
        return 0

    # path-shaped arg
    candidate = Path(target)
    if not candidate.is_dir():
        print(  # noqa: T201
            f"error: {target!r} is not a known nickname and is not an existing "
            "directory. Run `wiki list` to see registered wikis."
        )
        return 1

    # resolve nickname
    nick = args.name or candidate.name
    if nick in known:
        print(  # noqa: T201
            f"error: nickname {nick!r} already registered. "
            "Pass --name to choose a different nickname."
        )
        return 1

    # bootstrap + register + switch
    try:
        wiki_bootstrap_mod.bootstrap(candidate)
        reg.add(WikiEntry(nick, candidate))
        reg.set_active(nick)
    except WikiRegistryError as e:
        print(f"error: {e}")  # noqa: T201
        return 1

    cfg.wikis = reg.entries
    cfg.active_wiki = reg.active
    save_config(cfg, home=home)
    print(f"active wiki: {nick!r} → {candidate}")  # noqa: T201
    return 0


def _run_rebuild(args: argparse.Namespace, home: Path | None) -> int:
    """Regenerate all entity pages; delete orphan .md files."""
    from auto_lorebook import entities as entities_mod  # noqa: PLC0415
    from auto_lorebook import page_step as page_step_mod  # noqa: PLC0415
    from auto_lorebook import wiki_context as wiki_context_mod  # noqa: PLC0415

    cfg = cfg_mod.load_config(home=home)
    try:
        wiki_repo = cfg.resolve_active_wiki(getattr(args, "wiki", None))
    except cfg_mod.ConfigError as e:
        print(f"error: {e}")  # noqa: T201
        return 1

    api_key = cfg.get_api_key()
    if not api_key:
        print(  # noqa: T201
            f"error: no API key found. "
            f"Export ${cfg.openrouter.api_key_env} or store in "
            "~/.auto-lorebook/credentials."
        )
        return 1

    conn = db.open(wiki_state.wiki_db_path(wiki_repo))
    try:
        rows = entities_mod.list_entities(conn, wiki_repo=wiki_repo)
        touched = [(e.category, e.slug) for e in rows]

        client = OpenRouterClient(
            api_key=api_key,
            default_model=cfg.models.summarizer or cfg.models.primary,
            app_title="auto-lorebook",
        )
        effective_model = cfg.models.summarizer or cfg.models.primary
        wiki_ctx = wiki_context_mod.read(conn, wiki_repo=wiki_repo)
        wiki_setting = wiki_ctx.setting.description or ""
        entity_index = entities_mod.render_for_preamble(conn)

        force = getattr(args, "force", False)
        written = page_step_mod.run_page_step(
            conn,
            wiki_repo,
            touched,
            entity_index=entity_index,
            wiki_setting=wiki_setting,
            client=client,
            model=effective_model,
            skip_unchanged=not force,
        )

        # orphan cleanup: delete .md files in category dirs with no matching entity
        valid_paths = {wiki_repo / e.category / f"{e.slug}.md" for e in rows}
        orphans_removed = 0
        for cat in _ENTITY_CATEGORIES:
            cat_dir = wiki_repo / cat
            if not cat_dir.is_dir():
                continue
            for md_file in cat_dir.glob("*.md"):
                if md_file not in valid_paths:
                    md_file.unlink()
                    orphans_removed += 1
                    _logger.info("removed orphan: %s", md_file)

        skipped = len(touched) - len(written)
        print(  # noqa: T201
            f"Rebuild complete: {len(written)} pages regenerated, "
            f"{skipped} skipped, "
            f"{orphans_removed} orphan(s) removed."
        )
        return 0
    finally:
        conn.close()
