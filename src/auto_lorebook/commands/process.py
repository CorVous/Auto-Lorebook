"""auto-lorebook process subcommand — interactive TUI pipeline."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import info_yaml as info_yaml_mod
from auto_lorebook import source_id as sid_mod
from auto_lorebook.tui.app import ProcessApp
from auto_lorebook.tui.resume import detect_stage
from auto_lorebook.tui.state import PipelineState, Stage

if TYPE_CHECKING:
    import argparse

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the process subcommand."""
    parser = subparsers.add_parser(
        "process",
        parents=[common_parser],
        help="Walk a source end-to-end in an interactive TUI",
        description=(
            "Run the full ingest → reading → review pipeline for a single source "
            "inside a Textual TUI. Resumes automatically from wherever the previous "
            "run left off. Pass a YouTube URL or local file path to start fresh, "
            "or use --source-id to resume a partially-processed source."
        ),
    )
    parser.add_argument(
        "url_or_path",
        nargs="?",
        default=None,
        help="YouTube URL or local file path (.srt/.txt/.md)",
    )
    parser.add_argument(
        "--source-id",
        dest="source_id",
        default=None,
        help="Explicit source ID for resuming a partially-processed source",
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the process command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    wiki_repo = cfg.wiki_repo_path
    url_or_path: str | None = args.url_or_path
    source_id: str | None = args.source_id

    if source_id is None:
        # Derive source_id from the positional URL / path
        if url_or_path is None:
            _logger.error("Provide a URL/path or --source-id to identify the source.")
            return 1
        try:
            source_id = sid_mod.derive(url_or_path, None, None)
        except sid_mod.SourceIdError as e:
            _logger.error("%s", e)
            return 1
    else:
        # Resume: if transcript absent, try to recover URL from info.yaml
        stage = detect_stage(source_id, wiki_repo)
        if stage == Stage.INGEST and url_or_path is None:
            info_path = wiki_repo / "sources" / source_id / "info.yaml"
            if info_path.exists():
                try:
                    info = info_yaml_mod.read(info_path)
                    url_or_path = info.source_url
                except info_yaml_mod.InfoError:
                    pass
            if url_or_path is None:
                _logger.error(
                    "Source %r has no transcript and no recoverable URL; "
                    "pass the URL or local path on the command line.",
                    source_id,
                )
                return 1

    stage = detect_stage(source_id, wiki_repo)
    state = PipelineState(
        source_id=source_id,
        wiki_repo_path=wiki_repo,
        stage=stage,
        url_or_path=url_or_path,
    )
    app = ProcessApp(cfg=cfg, state=state)
    app.run()
    return 0
