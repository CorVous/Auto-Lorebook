"""Interactive context prompts with flag overrides and partial-save on SIGINT."""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import replace
from typing import TYPE_CHECKING

from auto_lorebook.info_yaml import SourceContext
from auto_lorebook.info_yaml import write_yaml as write_info

if TYPE_CHECKING:
    from pathlib import Path

    from auto_lorebook.config import LastContext
    from auto_lorebook.info_yaml import Info
    from auto_lorebook.wiki_context import WikiContext

_logger = logging.getLogger(__name__)

_SOURCE_NATURES = (
    "actual-play",
    "dm-lore",
    "worldbuilding-video",
    "interview",
    "notes",
    "other",
)
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_HINT = "YYYY-MM-DD"


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _prompt(prompt_text: str, default: str | None = None) -> str | None:
    """Prompt user for input; blank → None (skip).

    :raises KeyboardInterrupt: on Ctrl-C
    """
    full_prompt = f"{prompt_text} [{default}]: " if default else f"{prompt_text}: "
    value = input(full_prompt).strip()
    return value or None


def _prompt_date(default: str | None) -> str | None:
    """Prompt for YYYY-MM-DD; re-prompt on invalid; blank → skip."""
    while True:
        raw = _prompt(f"Session date ({_DATE_HINT})", default)
        if raw is None:
            return None
        if _DATE_RE.fullmatch(raw):
            return raw
        print(f"  Expected {_DATE_HINT}, got '{raw}'. Press Enter to skip.")  # noqa: T201


def _prompt_nature(default: str | None) -> str | None:
    """Prompt for source_nature; re-prompt on invalid; blank → skip."""
    allowed = "/".join(_SOURCE_NATURES)
    while True:
        raw = _prompt(f"Source nature [{allowed}]", default)
        if raw is None:
            return None
        if raw in _SOURCE_NATURES:
            return raw
        print(f"  Expected one of: {allowed}. Press Enter to skip.")  # noqa: T201


def gather_context(
    existing_info: Info,
    flags: dict[str, str | None],
    wiki_context: WikiContext,
    last_context: LastContext | None,
    *,
    interactive: bool,
    save_path: Path | None = None,
) -> Info:
    """Gather context interactively or from flags; return updated Info.

    Prompts shown only in interactive mode and only for fields not
    already supplied by flags. Ctrl-C triggers a partial save to save_path.

    :param existing_info: current Info (may have pre-existing context)
    :param flags: keys: session_date, perspective, source_nature,
                  setting, notes (None = flag not supplied)
    :param wiki_context: wiki-level context for setting default
    :param last_context: last-context.yaml defaults; None if unavailable
    :param interactive: whether to show prompts
    :param save_path: partial save target on KeyboardInterrupt
    """
    info = existing_info
    ctx = info.context
    last = last_context

    session_date = flags.get("session_date") or info.session_date
    perspective = (
        flags.get("perspective")
        or ctx.perspective
        or (last.perspective if last else None)
    )
    source_nature = (
        flags.get("source_nature")
        or ctx.source_nature
        or (last.source_nature if last else None)
    )
    setting = flags.get("setting") or ctx.setting or wiki_context.setting.name
    notes = flags.get("notes") or ctx.notes

    if not interactive or not _is_interactive():
        if interactive:
            print(  # noqa: T201
                "Non-interactive environment detected; "
                "using flags and existing context only."
            )
        updated_ctx = SourceContext(
            perspective=perspective,
            source_nature=source_nature,
            setting=setting,
            speakers=ctx.speakers,
            notes=notes,
        )
        return replace(info, session_date=session_date, context=updated_ctx)

    captured: dict[str, str | None] = {
        "session_date": session_date,
        "perspective": perspective,
        "source_nature": source_nature,
        "setting": setting,
        "notes": notes,
    }

    try:
        print("\nLet's add some context. Press Enter to skip any field.\n")  # noqa: T201

        if not flags.get("session_date"):
            captured["session_date"] = _prompt_date(session_date) or session_date

        if not flags.get("perspective"):
            captured["perspective"] = _prompt("Perspective", perspective) or perspective

        if not flags.get("source_nature"):
            captured["source_nature"] = _prompt_nature(source_nature) or source_nature

        if not flags.get("setting"):
            captured["setting"] = _prompt("Setting", setting) or setting

        if not flags.get("notes"):
            captured["notes"] = (
                _prompt("Any notes? (one line, or Enter to skip)", notes) or notes
            )

    except KeyboardInterrupt:
        _logger.info("Interrupted; saving captured context")
        _partial_save(info, captured, save_path)
        raise

    updated_ctx = SourceContext(
        perspective=captured["perspective"],
        source_nature=captured["source_nature"],
        setting=captured["setting"],
        speakers=ctx.speakers,
        notes=captured["notes"],
    )
    return replace(info, session_date=captured["session_date"], context=updated_ctx)


def _partial_save(
    info: Info, captured: dict[str, str | None], save_path: Path | None
) -> None:
    ctx = info.context
    updated_ctx = SourceContext(
        perspective=captured.get("perspective") or ctx.perspective,
        source_nature=captured.get("source_nature") or ctx.source_nature,
        setting=captured.get("setting") or ctx.setting,
        speakers=ctx.speakers,
        notes=captured.get("notes") or ctx.notes,
    )
    updated = replace(
        info,
        session_date=captured.get("session_date") or info.session_date,
        context=updated_ctx,
    )
    if save_path:
        try:
            write_info(updated, save_path)
            print(f"\nPartial context saved to {save_path}")  # noqa: T201
        except Exception:  # noqa: BLE001
            _logger.warning("Could not save partial context to %s", save_path)
