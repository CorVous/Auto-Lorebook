"""Tests for context.gather."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import yaml

from auto_lorebook.context.gather import (
    ContextInputs,
    GatherDefaults,
    gather_context,
    load_last_context,
    save_last_context,
)

if TYPE_CHECKING:
    from pathlib import Path


# ── load_last_context ─────────────────────────────────────────────────────────


def test_load_last_context_missing(tmp_path: Path) -> None:
    """Absent last-context.yaml returns empty GatherDefaults."""
    result = load_last_context(tmp_path)
    assert result.session_date is None
    assert result.perspective is None
    assert result.setting is None
    assert result.notes is None
    assert result.speakers == []


def test_load_last_context_empty_file(tmp_path: Path) -> None:
    """Empty last-context.yaml returns empty GatherDefaults."""
    (tmp_path / "last-context.yaml").write_text("", encoding="utf-8")
    result = load_last_context(tmp_path)
    assert result.session_date is None


def test_load_last_context_populated(tmp_path: Path) -> None:
    """Populated last-context.yaml loads correctly."""
    (tmp_path / "last-context.yaml").write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "session_date": "2025-03-01",
            "perspective": "GM",
            "source_nature": "actual play",
            "setting": "Eberron",
            "notes": "test notes",
            "speakers": ["Alice", "Bob"],
        }),
        encoding="utf-8",
    )
    result = load_last_context(tmp_path)
    assert result.session_date == "2025-03-01"
    assert result.perspective == "GM"
    assert result.source_nature == "actual play"
    assert result.setting == "Eberron"
    assert result.notes == "test notes"
    assert result.speakers == ["Alice", "Bob"]


# ── save_last_context ─────────────────────────────────────────────────────────


def test_save_last_context_creates_file(tmp_path: Path) -> None:
    """save_last_context writes last-context.yaml."""
    inputs = ContextInputs(session_date="2025-01-01", perspective="GM")
    save_last_context(tmp_path, inputs)
    assert (tmp_path / "last-context.yaml").exists()


def test_save_last_context_round_trip(tmp_path: Path) -> None:
    """Saved then loaded context matches original."""
    inputs = ContextInputs(
        session_date="2025-05-10",
        perspective="Player",
        source_nature="recap",
        setting="Ravnica",
        notes="some notes",
    )
    save_last_context(tmp_path, inputs)
    loaded = load_last_context(tmp_path)
    assert loaded.session_date == "2025-05-10"
    assert loaded.perspective == "Player"
    assert loaded.source_nature == "recap"
    assert loaded.setting == "Ravnica"
    assert loaded.notes == "some notes"


def test_save_creates_parent_dir(tmp_path: Path) -> None:
    """save_last_context creates config dir if absent."""
    nested = tmp_path / "deep" / "dir"
    save_last_context(nested, ContextInputs())
    assert (nested / "last-context.yaml").exists()


# ── gather_context: no-interactive ────────────────────────────────────────────


def test_no_interactive_returns_defaults() -> None:
    """no_interactive=True returns defaults without prompting."""
    defaults = GatherDefaults(
        session_date="2025-01-01",
        perspective="GM",
        source_nature="actual play",
        setting="Eberron",
        notes="preloaded",
    )
    result = gather_context(defaults, no_interactive=True)
    assert result.session_date == "2025-01-01"
    assert result.perspective == "GM"
    assert result.source_nature == "actual play"
    assert result.setting == "Eberron"
    assert result.notes == "preloaded"


def test_non_tty_stdin_skips_prompts() -> None:
    """Non-TTY stdin triggers non-interactive fallback."""
    stdin = io.StringIO("")  # isatty() returns False for StringIO
    stdout = io.StringIO()
    defaults = GatherDefaults(perspective="Narrator")
    result = gather_context(defaults, stdin=stdin, stdout=stdout)
    # no prompts written to stdout
    assert not stdout.getvalue()
    assert result.perspective == "Narrator"


# ── gather_context: interactive ───────────────────────────────────────────────


def _tty_stdin(text: str) -> io.StringIO:
    """StringIO that reports isatty() == True."""
    s = io.StringIO(text)
    s.isatty = lambda: True  # ty: ignore[invalid-assignment]
    return s


def test_interactive_accepts_user_input() -> None:
    """User-typed value overrides default."""
    user_input = "2025-06-01\nPlayer\nactual play\nForgotten Realms\ngreat session\n"
    stdin = _tty_stdin(user_input)
    stdout = io.StringIO()
    defaults = GatherDefaults()
    result = gather_context(defaults, stdin=stdin, stdout=stdout)
    assert result.session_date == "2025-06-01"
    assert result.perspective == "Player"
    assert result.source_nature == "actual play"
    assert result.setting == "Forgotten Realms"
    assert result.notes == "great session"


def test_interactive_empty_input_keeps_default() -> None:
    """Empty input line keeps the bracketed default."""
    stdin = _tty_stdin("\n\n\n\n\n")
    stdout = io.StringIO()
    defaults = GatherDefaults(
        session_date="2025-01-01",
        perspective="GM",
        source_nature="actual play",
        setting="Eberron",
        notes="existing",
    )
    result = gather_context(defaults, stdin=stdin, stdout=stdout)
    assert result.session_date == "2025-01-01"
    assert result.perspective == "GM"
    assert result.notes == "existing"


def test_interactive_shows_bracketed_defaults() -> None:
    """Bracketed defaults appear in prompt output."""
    stdin = _tty_stdin("\n\n\n\n\n")
    stdout = io.StringIO()
    defaults = GatherDefaults(session_date="2025-03-15")
    gather_context(defaults, stdin=stdin, stdout=stdout)
    assert "2025-03-15" in stdout.getvalue()


def test_interactive_ctrl_c_saves_partial() -> None:
    """KeyboardInterrupt during prompts returns partial context with notice."""
    # only first field answered before Ctrl-C
    stdin = _tty_stdin("2025-07-04\n")
    stdout = io.StringIO()

    original_readline = stdin.readline
    call_count = 0

    def raising_readline() -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise KeyboardInterrupt
        return original_readline()

    stdin.readline = raising_readline  # ty: ignore[invalid-assignment]
    defaults = GatherDefaults(setting="Existing Setting")
    result = gather_context(defaults, stdin=stdin, stdout=stdout)
    # partial context: first field set, interrupted after
    assert result.session_date == "2025-07-04"
    assert "Interrupted" in stdout.getvalue()
