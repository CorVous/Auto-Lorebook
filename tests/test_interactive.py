"""Tests for interactive.py."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from auto_lorebook.config import LastContext
from auto_lorebook.info_yaml import Info, SourceContext
from auto_lorebook.interactive import gather_context
from auto_lorebook.wiki_context import SettingContext, WikiContext

_NO_FLAGS: dict[str, str | None] = {
    "perspective": None,
    "source_nature": None,
    "setting": None,
    "session_date": None,
    "notes": None,
}


def _info(
    perspective: str | None = None,
    source_nature: str | None = None,
    setting: str | None = None,
) -> Info:
    return Info(
        source_id="txt-abc1234567",
        source_type="text",
        fetched_at="2026-04-24T00:00:00Z",
        context=SourceContext(
            perspective=perspective,
            source_nature=source_nature,
            setting=setting,
        ),
    )


def _wc(name: str | None = "Aether Chronicles") -> WikiContext:
    return WikiContext(setting=SettingContext(name=name))


# ---------------------------------------------------------------------------
# Non-interactive path
# ---------------------------------------------------------------------------


def test_no_interactive_uses_flags() -> None:
    info = _info()
    result = gather_context(
        info,
        {**_NO_FLAGS, "perspective": "Cor", "source_nature": "actual-play"},
        _wc(),
        None,
        interactive=False,
    )
    assert result.context.perspective == "Cor"
    assert result.context.source_nature == "actual-play"


def test_no_interactive_falls_back_to_last_context() -> None:
    last = LastContext(perspective="Previous Cor", source_nature="notes")
    result = gather_context(
        _info(),
        _NO_FLAGS,
        _wc(),
        last,
        interactive=False,
    )
    assert result.context.perspective == "Previous Cor"
    assert result.context.source_nature == "notes"


def test_no_interactive_setting_falls_back_to_wiki_context() -> None:
    result = gather_context(
        _info(),
        _NO_FLAGS,
        _wc(name="My Setting"),
        None,
        interactive=False,
    )
    assert result.context.setting == "My Setting"


def test_no_interactive_flag_wins_over_last_context() -> None:
    last = LastContext(perspective="Old", source_nature="notes")
    flags: dict[str, str | None] = {
        **_NO_FLAGS,
        "perspective": "New",
        "source_nature": "dm-lore",
        "session_date": "2026-01-01",
    }
    result = gather_context(
        _info(),
        flags,
        _wc(),
        last,
        interactive=False,
    )
    assert result.context.perspective == "New"
    assert result.context.source_nature == "dm-lore"
    assert result.session_date == "2026-01-01"


def test_no_interactive_existing_info_preserved() -> None:
    """Existing context fields survive when flags are None."""
    info = _info(perspective="Existing", source_nature="notes")
    result = gather_context(
        info,
        _NO_FLAGS,
        _wc(name=None),
        None,
        interactive=False,
    )
    assert result.context.perspective == "Existing"
    assert result.context.source_nature == "notes"


# ---------------------------------------------------------------------------
# Interactive path via mocked input
# ---------------------------------------------------------------------------


def test_interactive_uses_input() -> None:
    inputs = ["2026-03-01", "Cor playing Kiki", "actual-play", "", ""]
    with (
        patch("builtins.input", side_effect=inputs),
        patch("auto_lorebook.interactive._is_interactive", return_value=True),
    ):
        result = gather_context(
            _info(),
            _NO_FLAGS,
            _wc(),
            None,
            interactive=True,
        )
    assert result.session_date == "2026-03-01"
    assert result.context.perspective == "Cor playing Kiki"
    assert result.context.source_nature == "actual-play"


def test_interactive_blank_skips_field() -> None:
    """Blank input keeps the existing/default value."""
    info = _info(perspective="Existing")
    inputs = ["", "", "", "", ""]
    with (
        patch("builtins.input", side_effect=inputs),
        patch("auto_lorebook.interactive._is_interactive", return_value=True),
    ):
        result = gather_context(
            info,
            _NO_FLAGS,
            _wc(),
            None,
            interactive=True,
        )
    assert result.context.perspective == "Existing"


def test_interactive_invalid_date_reprompts() -> None:
    """Invalid date input re-prompts; valid date follows."""
    inputs = ["not-a-date", "2026-03-01", "", "", "", ""]
    with (
        patch("builtins.input", side_effect=inputs),
        patch("auto_lorebook.interactive._is_interactive", return_value=True),
    ):
        result = gather_context(
            _info(),
            _NO_FLAGS,
            _wc(),
            None,
            interactive=True,
        )
    assert result.session_date == "2026-03-01"


def test_interactive_invalid_nature_reprompts() -> None:
    """Invalid source_nature re-prompts; valid value follows."""
    inputs = ["", "", "bad-nature", "actual-play", "", ""]
    with (
        patch("builtins.input", side_effect=inputs),
        patch("auto_lorebook.interactive._is_interactive", return_value=True),
    ):
        result = gather_context(
            _info(),
            _NO_FLAGS,
            _wc(),
            None,
            interactive=True,
        )
    assert result.context.source_nature == "actual-play"


def test_keyboard_interrupt_partial_save(tmp_path: Path) -> None:
    save_path = tmp_path / "info.yaml"
    flags: dict[str, str | None] = {**_NO_FLAGS, "perspective": "Cor"}
    with (
        patch("builtins.input", side_effect=KeyboardInterrupt),
        patch("auto_lorebook.interactive._is_interactive", return_value=True),
        pytest.raises(KeyboardInterrupt),
    ):
        gather_context(
            _info(),
            flags,
            _wc(),
            None,
            interactive=True,
            save_path=save_path,
        )
    # Partial save should have been written
    assert save_path.exists()
