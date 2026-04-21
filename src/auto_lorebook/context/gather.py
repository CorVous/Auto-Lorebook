"""Interactive context-gathering step run after ingest."""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import yaml

from auto_lorebook.schema import TOOL_SCHEMA_VERSION

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

_logger = logging.getLogger(__name__)

LAST_CONTEXT_FILE = "last-context.yaml"


@dataclass
class ContextInputs:
    """Context fields gathered from the user or defaults."""

    session_date: str | None = None
    perspective: str | None = None
    source_nature: str | None = None
    setting: str | None = None
    notes: str | None = None


@dataclass
class GatherDefaults:
    """Default values fed into the interactive prompts."""

    session_date: str | None = None
    perspective: str | None = None
    source_nature: str | None = None
    setting: str | None = None
    notes: str | None = None
    speakers: list[str] = field(default_factory=list)


def _prompt(
    label: str,
    default: str | None,
    stdin: TextIO,
    stdout: TextIO,
) -> str | None:
    """Emit one prompt line and read a response.

    Returns default on empty input; None when default is also None and input empty.
    """
    bracket = f" [{default}]" if default else ""
    stdout.write(f"{label}{bracket}: ")
    stdout.flush()
    line = stdin.readline()
    # EOF → treat as empty
    stripped = line.rstrip("\n")
    if not stripped:
        return default
    return stripped


def load_last_context(config_dir: Path) -> GatherDefaults:
    """Load last-context.yaml from config dir, returning empty defaults on miss.

    :param config_dir: ~/.auto-lorebook or equivalent
    """
    path = config_dir / LAST_CONTEXT_FILE
    if not path.exists():
        return GatherDefaults()
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not raw:
        return GatherDefaults()
    data = cast("dict[str, object]", raw)
    speakers_raw = data.get("speakers")
    speakers = [str(s) for s in speakers_raw] if isinstance(speakers_raw, list) else []
    return GatherDefaults(
        session_date=_str_or_none(data.get("session_date")),
        perspective=_str_or_none(data.get("perspective")),
        source_nature=_str_or_none(data.get("source_nature")),
        setting=_str_or_none(data.get("setting")),
        notes=_str_or_none(data.get("notes")),
        speakers=speakers,
    )


def save_last_context(config_dir: Path, inputs: ContextInputs) -> None:
    """Persist gathered context to last-context.yaml for use as future defaults.

    :param config_dir: ~/.auto-lorebook or equivalent
    :param inputs: context to persist
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / LAST_CONTEXT_FILE
    data: dict[str, object] = {
        "schema_version": TOOL_SCHEMA_VERSION,
        "session_date": inputs.session_date,
        "perspective": inputs.perspective,
        "source_nature": inputs.source_nature,
        "setting": inputs.setting,
        "notes": inputs.notes,
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)


def gather_context(
    defaults: GatherDefaults,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    no_interactive: bool = False,
) -> ContextInputs:
    """Run the interactive context-gathering sequence.

    Falls back to defaults when non-TTY or no_interactive=True.
    Ctrl-C saves partial context and exits cleanly.

    :param defaults: pre-filled defaults (CLI flags > wiki-context > last-context)
    :param stdin: override stdin (default sys.stdin)
    :param stdout: override stdout (default sys.stdout)
    :param no_interactive: skip prompts entirely
    :return: gathered ContextInputs
    """
    stdin_ = stdin if stdin is not None else sys.stdin
    stdout_ = stdout if stdout is not None else sys.stdout

    # fall back to defaults when non-interactive
    if no_interactive or not stdin_.isatty():
        return ContextInputs(
            session_date=defaults.session_date,
            perspective=defaults.perspective,
            source_nature=defaults.source_nature,
            setting=defaults.setting,
            notes=defaults.notes,
        )

    inputs = ContextInputs(
        session_date=defaults.session_date,
        perspective=defaults.perspective,
        source_nature=defaults.source_nature,
        setting=defaults.setting,
        notes=defaults.notes,
    )
    fields: list[tuple[str, str]] = [
        ("session_date", "Session date (YYYY-MM-DD)"),
        ("perspective", "Perspective"),
        ("source_nature", "Source nature"),
        ("setting", "Setting"),
        ("notes", "Notes"),
    ]
    try:
        for attr, label in fields:
            current = getattr(inputs, attr)
            value = _prompt(label, current, stdin_, stdout_)
            setattr(inputs, attr, value)
    except KeyboardInterrupt:
        stdout_.write("\nInterrupted — saving partial context.\n")
        stdout_.flush()
    return inputs


def _str_or_none(value: object) -> str | None:
    return str(value) if value is not None else None
