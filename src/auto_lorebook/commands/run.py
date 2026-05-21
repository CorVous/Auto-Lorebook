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
from auto_lorebook.commands._shared import load_or_create_config
from auto_lorebook.interactive import _is_interactive
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

# Stages that act as human gates and their required non-interactive flags.
_GATE_FLAGS: dict[Stage, str] = {
    Stage.APPROVE_READING: "--yes",
    Stage.REVIEW: "--auto-approve",
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
    parser.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Forward to approve-reading gate (auto-approve reading segments).",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        default=False,
        help="Forward to review gate (auto-approve all proposals).",
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


_PIPELINE_ORDER = [
    Stage.INGEST,
    Stage.GENERATE_READING,
    Stage.APPROVE_READING,
    Stage.PLAN,
    Stage.EXTRACT,
    Stage.REVIEW,
]

# Display name (dashed, lowercase) for each stage.
_STAGE_DISPLAY: dict[Stage, str] = {
    Stage.INGEST: "ingest",
    Stage.GENERATE_READING: "generate-reading",
    Stage.APPROVE_READING: "approve-reading",
    Stage.PLAN: "plan",
    Stage.EXTRACT: "extract",
    Stage.REVIEW: "review",
}

# Artifact that confirms the stage is done (used in skip notices).
_STAGE_ARTIFACT: dict[Stage, str] = {
    Stage.INGEST: "info.yaml",
    Stage.GENERATE_READING: "reading sidecar",
    Stage.APPROVE_READING: "reading.md",
    Stage.PLAN: "plan.yaml",
    Stage.EXTRACT: "proposals/",
    Stage.REVIEW: "proposals empty",
}


def _print_stage_header(stage: Stage) -> None:
    idx = _PIPELINE_ORDER.index(stage) + 1
    total = len(_PIPELINE_ORDER)
    print(f"── [{idx}/{total}] {_STAGE_DISPLAY[stage]} ──")  # noqa: T201


def _print_skip_notice(stage: Stage) -> None:
    print(f"✓ skipped {_STAGE_DISPLAY[stage]} ({_STAGE_ARTIFACT[stage]} exists)")  # noqa: T201


def _reachable_gates(resume: Stage) -> list[Stage]:
    """Gates at-or-after resume point in pipeline order."""
    resume_idx = _PIPELINE_ORDER.index(resume)
    return [g for g in _GATE_FLAGS if _PIPELINE_ORDER.index(g) >= resume_idx]


def _preflight_check(args: argparse.Namespace, resume: Stage) -> list[str]:
    """Return list of missing required flags for non-interactive run; empty = ok."""
    if _is_interactive():
        return []
    missing = []
    for gate in _reachable_gates(resume):
        flag = _GATE_FLAGS[gate]
        # map flag string to attribute name: --yes → yes, --auto-approve → auto_approve
        attr = flag.lstrip("-").replace("-", "_")
        if not getattr(args, attr, False):
            missing.append(flag)
    return missing


def _build_stage_args(
    stage: Stage,
    source_id: str,
    wiki_override: str | None,
    args: argparse.Namespace,
) -> argparse.Namespace:
    """Build Namespace for a stage, forwarding gate flags as appropriate."""
    extra: dict[str, object] = {}
    if stage is Stage.APPROVE_READING:
        extra["yes"] = getattr(args, "yes", False)
    elif stage is Stage.REVIEW:
        extra["auto_approve"] = getattr(args, "auto_approve", False)
    return argparse.Namespace(source_id=source_id, wiki=wiki_override, **extra)


def run(args: argparse.Namespace) -> int:
    """Execute the run command: loop through stages until done."""
    try:
        cfg = load_or_create_config(
            no_interactive=getattr(args, "no_interactive", False)
        )
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1
    except KeyboardInterrupt:
        return 130

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
            _print_stage_header(Stage.INGEST)
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

    # Pre-flight: determine resume point and refuse early if non-TTY + gates reachable.
    resume = first_missing_stage(cfg, source_id, wiki_override=wiki_override)
    if resume is None:
        return 0
    if resume is Stage.INGEST:
        _logger.error("Source %s still missing after ingest.", source_id)
        return 1

    missing_flags = _preflight_check(args, resume)
    if missing_flags:
        flags_str = " and ".join(missing_flags)
        _logger.error(
            "non-interactive shell: %s required to pass gate(s) unattended",
            flags_str,
        )
        return 1

    # Emit skip notices for all stages before the resume point.
    resume_idx = _PIPELINE_ORDER.index(resume)
    for skipped_stage in _PIPELINE_ORDER[:resume_idx]:
        _print_skip_notice(skipped_stage)

    # Reuse resume from pre-flight as the first loop stage; avoids double call.
    stage: Stage | None = resume
    while stage is not None:
        if stage is Stage.INGEST:
            # Should not happen after successful ingest, but guard anyway.
            _logger.error("Source %s still missing after ingest.", source_id)
            return 1

        _print_stage_header(stage)
        stage_runner = STAGE_RUNNERS[stage]
        stage_args = _build_stage_args(stage, source_id, wiki_override, args)
        exit_code = stage_runner(stage_args)
        if exit_code != 0:
            return exit_code

        # Gate-decline check: if same stage still missing, user declined without error.
        post_stage = first_missing_stage(cfg, source_id, wiki_override=wiki_override)
        if post_stage is stage:
            print(f"Stopped at stage {stage.value.replace('_', '-')}")  # noqa: T201
            return 0
        stage = post_stage

    return 0
