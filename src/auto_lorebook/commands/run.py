"""auto-lorebook run umbrella subcommand.

Walks a source through the pipeline from its current state to completion,
delegating each stage to the matching command's run() function.
"""

from __future__ import annotations

import argparse
import logging
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import source_id as sid_mod
from auto_lorebook.commands import approve_reading as approve_reading_cmd
from auto_lorebook.commands import extract as extract_cmd
from auto_lorebook.commands import generate_reading as generate_reading_cmd
from auto_lorebook.commands import ingest as ingest_cmd
from auto_lorebook.commands import plan as plan_cmd
from auto_lorebook.commands import review as review_cmd
from auto_lorebook.pipeline_state import Stage, first_missing_stage

if TYPE_CHECKING:
    from collections.abc import Callable

_logger = logging.getLogger(__name__)

# Ingest-only flags: warn when set on a source-id invocation.
_INGEST_ONLY_ATTRS = (
    "session_date",
    "perspective",
    "source_nature",
    "setting",
    "notes",
    "source_url",
    "no_interactive",
    "cookies_from_browser",
)

# Extra Namespace fields each stage's run() requires beyond source_id/wiki.
_STAGE_DEFAULTS: dict[Stage, dict[str, object]] = {
    Stage.GENERATE_READING: {},
    Stage.APPROVE_READING: {"yes": False},
    Stage.PLAN: {},
    Stage.EXTRACT: {},
    Stage.REVIEW: {"auto_approve": False},
}

# Maps Stage → command run() callable; exposed for patching in tests.
STAGE_RUNNERS: dict[Stage, Callable[[argparse.Namespace], int]] = {
    Stage.GENERATE_READING: generate_reading_cmd.run,
    Stage.APPROVE_READING: approve_reading_cmd.run,
    Stage.PLAN: plan_cmd.run,
    Stage.EXTRACT: extract_cmd.run,
    Stage.REVIEW: review_cmd.run,
}


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the run subcommand."""
    parser = subparsers.add_parser(
        "run",
        parents=[common_parser],
        help="Walk a source through the pipeline from current state to completion",
        description=(
            "Detect which pipeline stage is next for a given source and run "
            "through to completion. At human-gate stages (approve-reading, "
            "review) defers to the interactive command as-is."
        ),
    )
    parser.add_argument(
        "url_or_sid",
        help="Source ID (e.g. yt-abc12345678) or YouTube URL",
    )
    ingest_cmd.add_ingest_args(parser)
    parser.set_defaults(func=run)
    return parser


def _is_url(value: str) -> bool:
    """Check if value looks like a URL."""
    return "://" in value or value.startswith("http")


def _warn_ignored_ingest_flags(args: argparse.Namespace) -> None:
    """Warn if any ingest-only flags are set on a source-id invocation."""
    active = [attr for attr in _INGEST_ONLY_ATTRS if getattr(args, attr, None)]
    if active:
        flags = ", ".join(f"--{a.replace('_', '-')}" for a in active)
        _logger.warning("ingest-only flags ignored for source-id invocation: %s", flags)


def run(args: argparse.Namespace) -> int:
    """Execute the run command: loop through stages until done."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    wiki_override: str | None = getattr(args, "wiki", None)
    # url_or_sid = real positional; source_id = --source-id flag override.
    # When url_or_sid absent, treat source_id as positional (legacy test convention).
    url_or_sid: str | None = getattr(args, "url_or_sid", None)
    positional: str = url_or_sid if url_or_sid is not None else args.source_id
    # sid_override only valid when positional came from url_or_sid slot.
    sid_override: str | None = (
        getattr(args, "source_id", None) if url_or_sid is not None else None
    )

    if _is_url(positional):
        # Derive source_id from URL.
        video_id = sid_mod.extract_video_id(positional)
        if not video_id:
            _logger.error(
                "Only YouTube URLs are supported. Pass a source ID or a YouTube URL."
            )
            return 1
        source_id = sid_override or f"yt-{video_id}"
        # Check if source already ingested; if not, run ingest first.
        stage = first_missing_stage(cfg, source_id, wiki_override=wiki_override)
        if stage is Stage.INGEST:
            ingest_args = argparse.Namespace(
                url_or_path=positional,
                wiki=wiki_override,
                source_url=getattr(args, "source_url", None),
                source_id=sid_override,
                session_date=getattr(args, "session_date", None),
                perspective=getattr(args, "perspective", None),
                source_nature=getattr(args, "source_nature", None),
                setting=getattr(args, "setting", None),
                notes=getattr(args, "notes", None),
                no_interactive=getattr(args, "no_interactive", True),
                cookies_from_browser=getattr(args, "cookies_from_browser", None),
            )
            exit_code = ingest_cmd.run(ingest_args)
            if exit_code != 0:
                return exit_code
    else:
        source_id = positional
        _warn_ignored_ingest_flags(args)

    while True:
        stage = first_missing_stage(cfg, source_id, wiki_override=wiki_override)
        if stage is None:
            return 0
        if stage is Stage.INGEST:
            # Should not happen after successful ingest, but guard anyway.
            _logger.error("Source %s still missing after ingest.", source_id)
            return 1

        stage_runner = STAGE_RUNNERS[stage]
        defaults = _STAGE_DEFAULTS[stage]
        stage_args = argparse.Namespace(
            source_id=source_id,
            wiki=wiki_override,
            **defaults,
        )
        exit_code = stage_runner(stage_args)
        if exit_code != 0:
            return exit_code
