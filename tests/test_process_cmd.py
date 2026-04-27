"""Tests for the `process` CLI subcommand parser and dispatch logic."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml

from auto_lorebook.commands import process_cmd

if TYPE_CHECKING:
    from pathlib import Path


_SOURCE_ID = "yt-dQw4w9WgXcQ"
_YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(h))
    return h


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    w = tmp_path / "wiki"
    w.mkdir()
    (w / ".wiki-context.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (w / ".transcription-corrections.yaml").write_text(
        "schema_version: 1\n", encoding="utf-8"
    )
    for cat in ("characters", "locations", "factions", "events", "items", "concepts"):
        (w / cat).mkdir()
    return w


def _write_config(home: Path, wiki: Path) -> None:
    (home / "config.yaml").write_text(
        f"schema_version: 1\nwiki_repo_path: {wiki}\n"
        "openrouter:\n  api_key_env: FAKE_OR_KEY\n"
        "models:\n  primary: anthropic/claude-sonnet-4-5\n",
        encoding="utf-8",
    )


class TestProcessCmdParser:
    """Parser wiring: argparse registers the subcommand correctly."""

    def _parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        common = argparse.ArgumentParser(add_help=False)
        process_cmd.add_parser(subparsers, common)
        return parser

    def test_url_form_sets_url_or_path(self) -> None:
        parser = self._parser()
        args = parser.parse_args(["process", _YT_URL])
        assert args.url_or_path == _YT_URL
        assert args.source_id is None

    def test_source_id_form_sets_source_id(self) -> None:
        parser = self._parser()
        args = parser.parse_args(["process", "--source-id", _SOURCE_ID])
        assert args.source_id == _SOURCE_ID
        assert args.url_or_path is None

    def test_func_is_callable(self) -> None:
        parser = self._parser()
        args = parser.parse_args(["process", _YT_URL])
        assert callable(args.func)


class TestProcessCmdDispatch:
    """run() computes source_id and dispatches to the TUI."""

    def _run(self, home: Path, wiki: Path, **kwargs: object) -> tuple[int, object]:
        _write_config(home, wiki)
        args = argparse.Namespace(
            url_or_path=kwargs.get("url_or_path"),
            source_id=kwargs.get("source_id"),
        )
        app_mock = MagicMock()
        app_mock.run.return_value = None
        with patch(
            "auto_lorebook.commands.process.ProcessApp", return_value=app_mock
        ) as app_cls:
            rc = process_cmd.run(args)
            return rc, app_cls

    def test_url_form_derives_source_id(self, home: Path, wiki: Path) -> None:
        rc, app_cls = self._run(home, wiki, url_or_path=_YT_URL)
        assert rc == 0
        call_kwargs = app_cls.call_args  # ty: ignore[unresolved-attribute]
        assert call_kwargs is not None
        # source_id derived from URL and threaded through state
        state = call_kwargs[1].get("state")
        assert state is not None
        assert "dQw4w9WgXcQ" in state.source_id

    def test_source_id_resume_with_no_transcript_and_no_info_errors(
        self, home: Path, wiki: Path
    ) -> None:
        _write_config(home, wiki)
        args = argparse.Namespace(url_or_path=None, source_id=_SOURCE_ID)
        rc = process_cmd.run(args)
        assert rc == 1

    def test_source_id_resume_recovers_url_from_info_yaml(
        self, home: Path, wiki: Path
    ) -> None:
        _write_config(home, wiki)
        # Write info.yaml with source_url but no transcript
        src_dir = wiki / "sources" / _SOURCE_ID
        src_dir.mkdir(parents=True)
        info = {
            "schema_version": 1,
            "source_id": _SOURCE_ID,
            "source_type": "youtube",
            "source_url": _YT_URL,
            "title": "Test",
            "fetched_at": "2026-01-01T00:00:00Z",
            "transcript_filename": "transcript.en.srt",
            "context": {
                "perspective": None,
                "source_nature": None,
                "setting": None,
                "speakers": [],
                "notes": None,
            },
        }
        (src_dir / "info.yaml").write_text(yaml.safe_dump(info), encoding="utf-8")

        args = argparse.Namespace(url_or_path=None, source_id=_SOURCE_ID)
        app_mock = MagicMock()
        app_mock.run.return_value = None
        with patch("auto_lorebook.commands.process.ProcessApp", return_value=app_mock):
            rc = process_cmd.run(args)
        assert rc == 0
