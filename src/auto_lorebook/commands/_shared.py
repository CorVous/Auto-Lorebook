"""Shared context-finalize pipeline for ingest and configure-context."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import (
    corrections,
    entity_index,
    info_yaml,
    interactive,
    wiki_context,
)
from auto_lorebook import preamble as preamble_mod

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from auto_lorebook.info_yaml import Info

_logger = logging.getLogger(__name__)


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
    wiki_repo = cfg.wiki_repo_path
    wc = wiki_context.read(wiki_repo / ".wiki-context.yaml")
    cors = corrections.read(wiki_repo / ".transcription-corrections.yaml")
    last_ctx = cfg_mod.load_last_context()

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
        )
    )

    idx = entity_index.build(wiki_repo)
    try:
        p = preamble_mod.assemble(info, wc, cors, idx, reduced=False)
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
