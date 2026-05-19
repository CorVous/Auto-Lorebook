"""Shared helpers: context-finalize pipeline and wiki resolution."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import (
    corrections,
    info_yaml,
    interactive,
    wiki_context,
)
from auto_lorebook import db as db_mod
from auto_lorebook import entities as entities_mod
from auto_lorebook import preamble as preamble_mod
from auto_lorebook.config import ConfigError

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from auto_lorebook.info_yaml import Info

_logger = logging.getLogger(__name__)


def resolve_wiki(cfg: cfg_mod.Config, args: argparse.Namespace) -> Path:
    """Resolve active wiki path, applying `args.wiki` override if set.

    Accepts nicknames only — rejects path-shaped strings (containing `/`,
    starting with `~` or `.`). Does not mutate the registry.

    :raises ConfigError: invalid override or unknown nickname
    """
    override: str | None = getattr(args, "wiki", None)
    if override is not None and ("/" in override or override.startswith(("~", "."))):
        msg = (
            f"--wiki takes a nickname, not a path: {override!r}. "
            "Use `wiki list` to see registered nicknames."
        )
        raise ConfigError(msg)
    return cfg.resolve_active_wiki(override)


def finalize_context(
    info: Info,
    info_path: Path,
    cfg: cfg_mod.Config,
    args: argparse.Namespace,
) -> int:
    """Gather context, write info.yaml, and check preamble budget.

    Shared tail for `ingest` and `configure-context` commands.
    Returns the CLI exit code.
    """
    wiki_repo = resolve_wiki(cfg, args)
    wc = wiki_context.read(wiki_repo / ".wiki-context.yaml")
    cors = corrections.read(wiki_repo / ".transcription-corrections.yaml")
    last_ctx = cfg_mod.load_last_context(wiki_root=wiki_repo)

    flags = {
        "session_date": args.session_date,
        "perspective": args.perspective,
        "source_nature": args.source_nature,
        "setting": args.setting,
        "notes": args.notes,
    }
    try:
        info = interactive.gather_context(
            info,
            flags,
            wc,
            last_ctx,
            interactive=not args.no_interactive,
            save_path=info_path,
        )
    except KeyboardInterrupt:
        return 130

    info_yaml.write(info, info_path)
    print(f"Context saved to {info_path}")  # noqa: T201

    cfg_mod.save_last_context(
        cfg_mod.LastContext(
            perspective=info.context.perspective,
            source_nature=info.context.source_nature,
        ),
        wiki_root=wiki_repo,
    )

    conn = db_mod.open(wiki_repo / ".wiki-state" / "wiki.db")
    entity_snippet = entities_mod.render_for_preamble(conn, wiki_repo)
    try:
        p = preamble_mod.assemble(info, wc, cors, entity_snippet, reduced=False)
        p.check_budget(
            context_window=cfg.models.primary_context_window,
            budget_fraction=cfg.preamble.budget_fraction,
        )
    except preamble_mod.PreambleTooLargeError as e:
        _logger.error("%s", e)
        return 1

    char_count = len(p.text)
    print(  # noqa: T201
        f"Preamble: {char_count} chars (~{char_count // 4} tokens) — budget OK"
    )
    return 0
