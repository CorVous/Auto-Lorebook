"""Tests for the `run` umbrella command."""

from __future__ import annotations

import argparse
import logging
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
    *,
    yes: bool = False,
    auto_approve: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        source_id=source_id,
        wiki=wiki,
        yes=yes,
        auto_approve=auto_approve,
    )


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
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
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
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
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
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
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
    assert args.url_or_sid == "yt-abc12345678"
    assert hasattr(args, "func")


def test_add_parser_accepts_wiki_flag() -> None:
    """Run subcommand accepts --wiki via global flag."""
    parser = create_parser()
    args = parser.parse_args(["--wiki", "alt", "run", "yt-abc12345678"])
    assert args.wiki == "alt"
    assert args.url_or_sid == "yt-abc12345678"


# ---------------------------------------------------------------------------
# add_ingest_args parity
# ---------------------------------------------------------------------------


def test_ingest_flag_parity() -> None:
    """Run and ingest parsers accept the same ingest-specific flags."""
    ingest_flags = [
        "--session-date=2026-01-15",
        "--perspective=x",
        "--source-nature=actual-play",
        "--setting=Foo",
        "--notes=bar",
        "--source-url=https://example.com",
        "--source-id=yt-abc12345678",
        "--no-interactive",
        "--cookies-from-browser=chrome",
    ]
    full_parser = create_parser()
    # ingest with all flags
    ingest_args = full_parser.parse_args([
        "ingest",
        "https://youtube.com/watch?v=abc12345678",
        *ingest_flags,
    ])
    assert ingest_args.session_date == "2026-01-15"
    assert ingest_args.perspective == "x"
    assert ingest_args.source_nature == "actual-play"
    assert ingest_args.setting == "Foo"
    assert ingest_args.notes == "bar"
    assert ingest_args.source_url == "https://example.com"
    assert ingest_args.source_id == "yt-abc12345678"
    assert ingest_args.no_interactive is True
    assert ingest_args.cookies_from_browser == "chrome"

    # run with the same flags
    run_args = full_parser.parse_args(["run", "yt-abc12345678", *ingest_flags])
    assert run_args.session_date == "2026-01-15"
    assert run_args.perspective == "x"
    assert run_args.source_nature == "actual-play"
    assert run_args.setting == "Foo"
    assert run_args.notes == "bar"
    assert run_args.source_url == "https://example.com"
    assert run_args.source_id == "yt-abc12345678"
    assert run_args.no_interactive is True
    assert run_args.cookies_from_browser == "chrome"


# ---------------------------------------------------------------------------
# URL → new source: ingest called first, then chain continues
# ---------------------------------------------------------------------------


def test_url_new_source_calls_ingest_then_chain() -> None:
    """URL positional with no existing source calls ingest, then pipeline stages."""
    url = "https://youtube.com/watch?v=abc12345678"
    expected_sid = "yt-abc12345678"

    ingest_calls: list[argparse.Namespace] = []
    stage_calls: list[tuple[Stage, argparse.Namespace]] = []

    def fake_ingest(ns: argparse.Namespace) -> int:
        ingest_calls.append(ns)
        return 0

    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    for stage in list(patched_runners):

        def make_runner(s: Stage) -> Callable[[argparse.Namespace], int]:
            def runner(ns: argparse.Namespace) -> int:
                stage_calls.append((s, ns))
                return 0

            return runner

        patched_runners[stage] = make_runner(stage)

    # fms: INGEST (triggers ingest), GENERATE_READING (first loop stage), None (done)
    fms_side_effects = [Stage.INGEST, Stage.GENERATE_READING, None]

    args = argparse.Namespace(
        url_or_sid=url,
        source_id=None,
        wiki=None,
        session_date=None,
        perspective=None,
        source_nature=None,
        setting=None,
        notes=None,
        source_url=None,
        no_interactive=True,
        cookies_from_browser=None,
    )

    fms_patch = patch(
        "auto_lorebook.commands.run.first_missing_stage",
        side_effect=fms_side_effects,
    )
    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        fms_patch,
        patch("auto_lorebook.commands.run.ingest_cmd.run", fake_ingest),
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(args)

    assert result == 0
    # ingest was called with the URL and all ingest flags forwarded
    assert len(ingest_calls) == 1
    assert ingest_calls[0].url_or_path == url
    # pipeline stage called after ingest
    assert len(stage_calls) == 1
    assert stage_calls[0][0] == Stage.GENERATE_READING
    # source_id on stage namespace is the derived sid
    assert stage_calls[0][1].source_id == expected_sid


# ---------------------------------------------------------------------------
# URL → existing source: ingest skipped, chain continues from first missing
# ---------------------------------------------------------------------------


def test_url_existing_source_skips_ingest() -> None:
    """URL positional with existing source skips ingest, runs remaining stages."""
    url = "https://youtube.com/watch?v=abc12345678"

    ingest_calls: list[argparse.Namespace] = []

    def fake_ingest(ns: argparse.Namespace) -> int:
        ingest_calls.append(ns)
        return 0

    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    stage_calls: list[Stage] = []
    for stage in list(patched_runners):

        def make_runner(s: Stage) -> Callable[[argparse.Namespace], int]:
            def runner(_ns: argparse.Namespace) -> int:
                stage_calls.append(s)
                return 0

            return runner

        patched_runners[stage] = make_runner(stage)

    # URL path: fms called once to check ingest need (non-INGEST), then twice in loop.
    fms_side_effects = [Stage.GENERATE_READING, Stage.GENERATE_READING, None]

    args = argparse.Namespace(
        url_or_sid=url,
        source_id=None,
        wiki=None,
        session_date=None,
        perspective=None,
        source_nature=None,
        setting=None,
        notes=None,
        source_url=None,
        no_interactive=True,
        cookies_from_browser=None,
    )

    fms_patch = patch(
        "auto_lorebook.commands.run.first_missing_stage",
        side_effect=fms_side_effects,
    )
    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        fms_patch,
        patch("auto_lorebook.commands.run.ingest_cmd.run", fake_ingest),
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(args)

    assert result == 0
    assert ingest_calls == []  # ingest NOT called
    assert Stage.GENERATE_READING in stage_calls


# ---------------------------------------------------------------------------
# Source-id positional + ingest-only flag → warning, proceeds normally
# ---------------------------------------------------------------------------


def test_source_id_with_ingest_flags_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Source-id positional + ingest-only flags emits a warning log."""
    args = argparse.Namespace(
        source_id="yt-abc12345678",
        wiki=None,
        session_date="2026-01-15",  # ingest-only flag set
        perspective=None,
        source_nature=None,
        setting=None,
        notes=None,
        source_url=None,
        no_interactive=False,
        cookies_from_browser=None,
    )

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch("auto_lorebook.commands.run.first_missing_stage", return_value=None),
        caplog.at_level(logging.WARNING, logger="auto_lorebook.commands.run"),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(args)

    assert result == 0
    assert any("ignored" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Pre-flight TTY refusal
# ---------------------------------------------------------------------------


def test_preflight_refuses_when_non_tty_approve_reading_reachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-TTY shell + approve-reading gate reachable → non-zero exit, names --yes."""
    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            return_value=Stage.APPROVE_READING,
        ),
        patch(
            "auto_lorebook.commands.run._is_interactive",
            return_value=False,
        ),
        caplog.at_level(logging.ERROR, logger="auto_lorebook.commands.run"),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(_args())

    assert result != 0
    assert any("--yes" in r.message for r in caplog.records)


def test_preflight_refuses_when_non_tty_review_reachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-TTY shell + review gate reachable → non-zero exit, names --auto-approve."""
    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            return_value=Stage.REVIEW,
        ),
        patch(
            "auto_lorebook.commands.run._is_interactive",
            return_value=False,
        ),
        caplog.at_level(logging.ERROR, logger="auto_lorebook.commands.run"),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(_args())

    assert result != 0
    assert any("--auto-approve" in r.message for r in caplog.records)


def test_preflight_refuses_both_flags_when_both_reachable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-TTY + both gates reachable + neither flag → message names both."""
    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            return_value=Stage.GENERATE_READING,
        ),
        patch(
            "auto_lorebook.commands.run._is_interactive",
            return_value=False,
        ),
        caplog.at_level(logging.ERROR, logger="auto_lorebook.commands.run"),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(_args())

    assert result != 0
    combined = " ".join(r.message for r in caplog.records)
    assert "--yes" in combined
    assert "--auto-approve" in combined


def test_preflight_ok_when_approve_reading_not_reachable() -> None:
    """Reading already approved (PLAN is first missing) → --yes not demanded.

    REVIEW gate is still reachable, so --auto-approve is passed to satisfy it.
    Verifies that --yes (approve-reading gate) is NOT required.
    """
    _, fake_plan = _fake_runner(0)
    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[Stage.PLAN] = fake_plan

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=[Stage.PLAN, None],
        ),
        patch(
            "auto_lorebook.commands.run._is_interactive",
            return_value=False,
        ),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        # --auto-approve satisfies REVIEW gate; --yes should NOT be needed
        result = run_cmd.run(_args(auto_approve=True))

    assert result == 0


def test_preflight_ok_when_approve_reading_not_reachable_only_review_ahead() -> None:
    """Resume at REVIEW: --yes not demanded, only --auto-approve needed."""
    _, fake_review = _fake_runner(0)
    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[Stage.REVIEW] = fake_review

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=[Stage.REVIEW, None],
        ),
        patch(
            "auto_lorebook.commands.run._is_interactive",
            return_value=False,
        ),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        # Only REVIEW gate reachable; --yes (approve-reading) should NOT be needed
        result = run_cmd.run(_args(auto_approve=True))

    assert result == 0


def test_preflight_passes_when_flags_provided() -> None:
    """--yes + --auto-approve supplied → pre-flight allows through."""
    _, fake_run = _fake_runner(0)
    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[Stage.GENERATE_READING] = fake_run

    args = argparse.Namespace(
        source_id="yt-abc12345678",
        wiki=None,
        yes=True,
        auto_approve=True,
    )

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=[Stage.GENERATE_READING, None],
        ),
        patch(
            "auto_lorebook.commands.run._is_interactive",
            return_value=False,
        ),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(args)

    assert result == 0


# ---------------------------------------------------------------------------
# Flag forwarding
# ---------------------------------------------------------------------------


def test_yes_flag_forwarded_to_approve_reading() -> None:
    """--yes is forwarded to approve-reading stage namespace."""
    call_log, fake_run = _fake_runner(0)
    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[Stage.APPROVE_READING] = fake_run

    args = argparse.Namespace(
        source_id="yt-abc12345678",
        wiki=None,
        yes=True,
        auto_approve=False,
    )

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=[Stage.APPROVE_READING, None],
        ),
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        run_cmd.run(args)

    assert len(call_log) == 1
    assert call_log[0].yes is True


def test_auto_approve_flag_forwarded_to_review() -> None:
    """--auto-approve is forwarded to review stage namespace."""
    call_log, fake_run = _fake_runner(0)
    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[Stage.REVIEW] = fake_run

    args = argparse.Namespace(
        source_id="yt-abc12345678",
        wiki=None,
        yes=False,
        auto_approve=True,
    )

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=[Stage.REVIEW, None],
        ),
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        run_cmd.run(args)

    assert len(call_log) == 1
    assert call_log[0].auto_approve is True


def test_yes_not_forwarded_to_other_stages() -> None:
    """--yes flag does not appear with a truthy value in non-gate stages."""
    call_log, fake_run = _fake_runner(0)
    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[Stage.PLAN] = fake_run

    args = argparse.Namespace(
        source_id="yt-abc12345678",
        wiki=None,
        yes=True,
        auto_approve=True,
    )

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=[Stage.PLAN, None],
        ),
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        run_cmd.run(args)

    assert len(call_log) == 1
    # PLAN stage namespace should NOT have yes attribute from flag forwarding
    assert not getattr(call_log[0], "yes", False)


# ---------------------------------------------------------------------------
# Gate-decline handling
# ---------------------------------------------------------------------------


def test_gate_decline_at_approve_reading(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stage returns 0 but approve-reading still missing → stopped message, exit 0."""
    # Stage runner returns 0 but first_missing_stage still returns APPROVE_READING
    # (user quit without approving)
    _, fake_run = _fake_runner(0)
    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[Stage.APPROVE_READING] = fake_run

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            # first call: preflight + stage dispatch → APPROVE_READING
            # second call: post-stage re-check → still APPROVE_READING (gate declined)
            side_effect=[Stage.APPROVE_READING, Stage.APPROVE_READING],
        ),
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(_args())

    assert result == 0
    captured = capsys.readouterr()
    assert "approve-reading" in (captured.out + captured.err).lower()
    assert "stopped" in (captured.out + captured.err).lower()


def test_gate_decline_at_review(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Review stage returns 0 but still has proposals → stopped message, exit 0."""
    _, fake_run = _fake_runner(0)
    patched_runners = dict(run_cmd.STAGE_RUNNERS)
    patched_runners[Stage.REVIEW] = fake_run

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=[Stage.REVIEW, Stage.REVIEW],
        ),
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(_args())

    assert result == 0
    captured = capsys.readouterr()
    assert "review" in (captured.out + captured.err).lower()
    assert "stopped" in (captured.out + captured.err).lower()


# ---------------------------------------------------------------------------
# Parser: --yes and --auto-approve flags
# ---------------------------------------------------------------------------


def test_add_parser_accepts_yes_flag() -> None:
    """Run subcommand parser accepts --yes."""
    parser = create_parser()
    args = parser.parse_args(["run", "--yes", "yt-abc12345678"])
    assert args.yes is True


def test_add_parser_accepts_auto_approve_flag() -> None:
    """Run subcommand parser accepts --auto-approve."""
    parser = create_parser()
    args = parser.parse_args(["run", "--auto-approve", "yt-abc12345678"])
    assert args.auto_approve is True


# ---------------------------------------------------------------------------
# Stage headers and skip notices
# ---------------------------------------------------------------------------


def _all_stages_noop_runners() -> dict[Stage, Callable[[argparse.Namespace], int]]:
    """Return STAGE_RUNNERS dict where every stage is a no-op returning 0."""
    patched = dict(run_cmd.STAGE_RUNNERS)
    for stage in list(patched):
        patched[stage] = lambda _ns: 0
    return patched


def test_prints_six_headers_from_scratch(capsys: pytest.CaptureFixture[str]) -> None:
    """Fresh URL run prints all 6 stage headers in order."""
    # URL path: first fms → INGEST (triggers ingest), then pre-flight fms →
    # GENERATE_READING, then each remaining stage, then None.
    fms_effects: list[Stage | None] = [
        Stage.INGEST,  # URL pre-ingest check → triggers ingest_cmd.run
        Stage.GENERATE_READING,  # pre-flight after ingest
        Stage.APPROVE_READING,  # after GENERATE_READING
        Stage.PLAN,  # after APPROVE_READING
        Stage.EXTRACT,  # after PLAN
        Stage.REVIEW,  # after EXTRACT
        None,  # after REVIEW → done
    ]
    patched_runners = _all_stages_noop_runners()

    args = argparse.Namespace(
        url_or_sid="https://www.youtube.com/watch?v=abc12345678",
        source_id=None,
        wiki=None,
        yes=True,
        auto_approve=True,
    )

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=fms_effects,
        ),
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
        patch("auto_lorebook.commands.run.ingest_cmd.run", return_value=0),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(args)

    assert result == 0
    out = capsys.readouterr().out
    # All six [N/6] tags must appear in strictly increasing output position.
    tags = ["[1/6]", "[2/6]", "[3/6]", "[4/6]", "[5/6]", "[6/6]"]
    positions = [out.index(tag) for tag in tags]
    assert positions == sorted(positions), (
        f"headers out of order: {list(zip(tags, positions, strict=True))}"
    )
    # Stage names also present.
    stage_names = (
        "ingest",
        "generate-reading",
        "approve-reading",
        "plan",
        "extract",
        "review",
    )
    for name in stage_names:
        assert name in out, f"missing stage name '{name}' in stdout"


def test_resume_prints_skip_notices_and_remaining_headers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Resume from PLAN: 3 skip notices + headers for plan/extract/review."""
    fms_effects: list[Stage | None] = [
        Stage.PLAN,  # resume (pre-flight)
        Stage.EXTRACT,
        Stage.REVIEW,
        None,
    ]
    patched_runners = _all_stages_noop_runners()

    args = argparse.Namespace(
        url_or_sid="yt-abc12345678",
        source_id=None,
        wiki=None,
        yes=True,
        auto_approve=True,
    )

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=fms_effects,
        ),
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        result = run_cmd.run(args)

    assert result == 0
    out = capsys.readouterr().out
    # Three skip notices for ingest, generate-reading, approve-reading
    for skipped in ("ingest", "generate-reading", "approve-reading"):
        assert skipped in out, f"missing skip notice for '{skipped}'"
    # Three stage headers for plan, extract, review
    for idx, stage_name in [
        ("[4/6]", "plan"),
        ("[5/6]", "extract"),
        ("[6/6]", "review"),
    ]:
        assert idx in out, f"missing {idx} in stdout"
        assert stage_name in out, f"missing stage header for '{stage_name}'"


def test_skip_notice_mentions_artifact(capsys: pytest.CaptureFixture[str]) -> None:
    """Skip notices include both the stage name and its artifact name."""
    fms_effects: list[Stage | None] = [
        Stage.PLAN,  # resume — ingest/generate-reading/approve-reading already done
        None,
    ]
    patched_runners = _all_stages_noop_runners()

    args = argparse.Namespace(
        url_or_sid="yt-abc12345678",
        source_id=None,
        wiki=None,
        yes=True,
        auto_approve=True,
    )

    with (
        patch("auto_lorebook.commands.run.cfg_mod.load_config") as mock_cfg,
        patch(
            "auto_lorebook.commands.run.first_missing_stage",
            side_effect=fms_effects,
        ),
        patch("auto_lorebook.commands.run._is_interactive", return_value=True),
        patch.object(run_cmd, "STAGE_RUNNERS", patched_runners),
    ):
        mock_cfg.return_value = MagicMock()
        run_cmd.run(args)

    out = capsys.readouterr().out
    # Check ingest skip notice mentions its artifact
    assert "info.yaml" in out
    # Check approve-reading skip notice mentions its artifact
    assert "reading.md" in out
