"""Tests for the `run` umbrella command."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Callable

import pytest

from auto_lorebook import config as cfg_mod
from auto_lorebook.cli import create_parser
from auto_lorebook.commands import run as run_cmd
from auto_lorebook.pipeline_state import Stage


def _args(
    source_id: str = "yt-abc12345678",
    wiki: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(source_id=source_id, wiki=wiki)


def _fake_runner(
    exit_code: int = 0,
) -> tuple[list[argparse.Namespace], Callable[[argparse.Namespace], int]]:
    """Return (call_log, runner) pair for a fake stage run() function."""
    call_log: list[argparse.Namespace] = []

    def runner(args: argparse.Namespace) -> int:
        call_log.append(args)
        return exit_code

    return call_log, runner


# ---------------------------------------------------------------------------
# No-op: nothing missing
# ---------------------------------------------------------------------------


def test_run_noop_when_nothing_missing() -> None:
    """Nothing to do when first_missing_stage returns None immediately."""
    with (
        patch(
            "auto_lorebook.commands.run.first_missing_stage", return_value=None
        ) as mock_fms,
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(_args())
    assert result == 0
    mock_fms.assert_called_once()


# ---------------------------------------------------------------------------
# Dispatch order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "extra_attr"),
    [
        (Stage.GENERATE_READING, None),
        (Stage.APPROVE_READING, "yes"),
        (Stage.PLAN, None),
        (Stage.EXTRACT, None),
        (Stage.REVIEW, "auto_approve"),
    ],
    ids=[
        "GENERATE_READING",
        "APPROVE_READING",
        "PLAN",
        "EXTRACT",
        "REVIEW",
    ],
)
def test_dispatches_to_stage(
    stage: Stage,
    extra_attr: str | None,
) -> None:
    """Each stage dispatches to its runner, then loops to None."""
    call_log, fake_run = _fake_runner(0)

    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[stage] = fake_run

    with (
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=[stage, None],
        ),
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch.object(
            run_cmd,
            "STAGE_RUNNERS",
            patched_runners,
        ),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(_args())

    assert result == 0
    assert len(call_log) == 1
    ns = call_log[0]
    assert ns.source_id == "yt-abc12345678"
    if extra_attr:
        assert hasattr(ns, extra_attr)


# ---------------------------------------------------------------------------
# Exit-code propagation
# ---------------------------------------------------------------------------


def test_propagates_nonzero_exit_code() -> None:
    """Non-zero exit from a stage is propagated immediately."""
    _, fake_run = _fake_runner(42)
    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[Stage.PLAN] = fake_run

    with (
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            return_value=Stage.PLAN,
        ),
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch.object(
            run_cmd,
            "STAGE_RUNNERS",
            patched_runners,
        ),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(_args())

    assert result == 42


def test_stops_looping_on_nonzero_exit() -> None:
    """Loop stops on first non-zero exit; first_missing_stage called once only."""
    fms_calls: list[int] = []

    def counting_fms(*_a: object, **_kw: object) -> Stage:
        fms_calls.append(1)
        return Stage.PLAN

    _, fake_run = _fake_runner(1)
    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[Stage.PLAN] = fake_run

    with (
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=counting_fms,
        ),
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch.object(
            run_cmd,
            "STAGE_RUNNERS",
            patched_runners,
        ),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(_args())

    assert result == 1
    assert len(fms_calls) == 1


# ---------------------------------------------------------------------------
# --wiki threading
# ---------------------------------------------------------------------------


def test_wiki_override_threaded_to_fms() -> None:
    """Wiki kwarg from args flows to first_missing_stage."""
    captured: list[str | None] = []

    def spy_fms(
        _cfg: object,
        _source_id: str,
        *,
        wiki_override: str | None,
    ) -> None:
        captured.append(wiki_override)

    with (
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=spy_fms,
        ),
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
    ):
        mock_cfg.return_value = MagicMock()
        run_cmd.run(_args(wiki="alt"))

    assert captured == ["alt"]


def test_wiki_override_threaded_to_stage_namespace() -> None:
    """Wiki attribute on dispatched Namespace matches args.wiki."""
    captured_ns: list[argparse.Namespace] = []

    def capture_run(args: argparse.Namespace) -> int:
        captured_ns.append(args)
        return 0

    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[Stage.PLAN] = capture_run

    with (
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=[Stage.PLAN, None],
        ),
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch.object(
            run_cmd,
            "STAGE_RUNNERS",
            patched_runners,
        ),
    ):
        mock_cfg.return_value = MagicMock()
        run_cmd.run(_args(wiki="alt"))

    assert captured_ns[0].wiki == "alt"


# ---------------------------------------------------------------------------
# Config error
# ---------------------------------------------------------------------------


def test_config_error_returns_1() -> None:
    """ConfigError during load_config returns exit code 1."""
    with patch(
        "auto_lorebook.commands.run.cfg_mod.load_config",
        side_effect=cfg_mod.ConfigError("no config"),
    ):
        result = run_cmd.run(_args())

    assert result == 1


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


def test_add_parser_registers_run_subcommand() -> None:
    """Run subcommand appears in parser after add_parser call."""
    parser = create_parser()
    args = parser.parse_args(["run", "yt-abc12345678"])
    assert args.source_id == "yt-abc12345678"
    assert hasattr(args, "func")


def test_add_parser_accepts_wiki_flag() -> None:
    """Run subcommand accepts --wiki via global flag."""
    parser = create_parser()
    args = parser.parse_args(["--wiki", "alt", "run", "yt-abc12345678"])
    assert args.wiki == "alt"
    assert args.source_id == "yt-abc12345678"
