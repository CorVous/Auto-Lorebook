"""auto-lorebook wiki subcommand group.

Registry management: `wiki <list|use|add|remove|rename>`.
All dispatch through `run()` on `args.wiki_action`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import wiki_bootstrap as wiki_bootstrap_mod
from auto_lorebook.config import save_config
from auto_lorebook.wiki_registry import WikiEntry, WikiRegistry, WikiRegistryError

if TYPE_CHECKING:
    import argparse

_logger = logging.getLogger(__name__)


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

    # known nickname → just switch
    known = {e.nickname for e in reg.entries}
    if target in known:
        reg.set_active(target)
        cfg.active_wiki = reg.active
        save_config(cfg, home=home)
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
