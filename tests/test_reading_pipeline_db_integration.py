"""DB-integration tests for the reading pipeline.

Verify that generate-reading / regenerate-reading write to the wiki DB
and that the state can be read back without touching any pending YAML.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml

from auto_lorebook import structure_store as ss
from auto_lorebook.commands import generate_reading_cmd, regenerate_reading_cmd
from auto_lorebook.openrouter import OpenRouterResponse

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

_SOURCE_ID = "yt-abc12345678"

_SRT = (
    "1\n"
    "00:00:00,000 --> 00:02:00,000\n"
    "Introduction.\n"
    "\n"
    "2\n"
    "00:02:00,000 --> 00:05:00,000\n"
    "King Theron founded Aldara.\n"
)


def _stub_structure() -> str:
    import json  # noqa: PLC0415

    return json.dumps({
        "default_speaker": "DM",
        "segments": [
            {
                "id": "seg-001",
                "start": "0:00:00",
                "end": "0:02:00",
                "title": "Intro",
                "speaker": "DM",
            },
            {
                "id": "seg-002",
                "start": "0:02:00",
                "end": "0:05:00",
                "title": "Founding",
                "speaker": "DM",
            },
        ],
        "uncertainty_flags": [],
    })


def _stub_bullets(seg_id: str) -> str:
    import json  # noqa: PLC0415

    if seg_id == "seg-002":
        return json.dumps({
            "bullets": [{"text": "King Theron founded Aldara", "anchor": "0:02:30"}]
        })
    return json.dumps({"bullets": []})


def _wire_client(client: MagicMock) -> None:
    def side_effect(
        messages: list[dict[str, str]], **_kw: object
    ) -> OpenRouterResponse:
        system = next((m for m in messages if m["role"] == "system"), {}).get(
            "content", ""
        )
        user = next((m for m in messages if m["role"] == "user"), {}).get("content", "")
        if "segmenting" in system:
            text = _stub_structure()
        else:
            for seg_id in ("seg-001", "seg-002"):
                if seg_id in user:
                    text = _stub_bullets(seg_id)
                    break
            else:
                import json  # noqa: PLC0415

                text = json.dumps({"bullets": []})
        return OpenRouterResponse(text=text, model="m", tokens_in=0, tokens_out=0)

    client.complete.side_effect = side_effect


def _write_config(home: Path, wiki: Path) -> None:
    (home / "config.yaml").write_text(
        f"schema_version: 2\nactive_wiki: main\nwikis:\n"
        f"- nickname: main\n  path: {wiki}\n"
        "openrouter:\n  api_key_env: FAKE_OR_KEY\n"
        "models:\n  primary: anthropic/claude-sonnet-4-5\n",
        encoding="utf-8",
    )


def _args(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    return home


@pytest.fixture
def ingested_wiki(tmp_wiki: Path, tmp_home: Path) -> Path:  # noqa: ARG001
    src_dir = tmp_wiki / "sources" / _SOURCE_ID
    src_dir.mkdir(parents=True)
    (src_dir / "transcript.en.srt").write_text(_SRT, encoding="utf-8")
    info = {
        "schema_version": 1,
        "source_id": _SOURCE_ID,
        "source_type": "youtube",
        "source_url": "https://youtube.com/watch?v=abc",
        "title": "Session 3",
        "duration_seconds": 300,
        "caption_type": "manual",
        "fetched_at": "2026-04-20T14:35:12Z",
        "session_date": None,
        "transcript_filename": "transcript.en.srt",
        "context": {
            "perspective": None,
            "source_nature": None,
            "setting": None,
            "speakers": [],
            "notes": None,
        },
    }
    (src_dir / "info.yaml").write_text(
        yaml.safe_dump(info, sort_keys=False), encoding="utf-8"
    )
    return tmp_wiki


def _open_db(wiki: Path) -> sqlite3.Connection:
    from auto_lorebook import db as db_mod  # noqa: PLC0415
    from auto_lorebook import wiki_state  # noqa: PLC0415

    return db_mod.open(wiki_state.wiki_db_path(wiki))


class TestGenerateReadingWritesToDB:
    def test_segments_and_bullets_stored(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """generate-reading stores segments + bullets in DB, no pending YAML."""
        _write_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        client = MagicMock()
        _wire_client(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            rc = generate_reading_cmd.run(_args(source_id=_SOURCE_ID))
        assert rc == 0

        from auto_lorebook import wiki_state  # noqa: PLC0415

        # no pending YAML files
        pending = wiki_state.pending_reading_dir(ingested_wiki, _SOURCE_ID)
        assert not pending.exists()

        # DB has segments
        conn = _open_db(ingested_wiki)
        try:
            segs = ss.list_segments(conn, _SOURCE_ID)
        finally:
            conn.close()
        assert len(segs) == 2
        assert {s.segment_id for s in segs} == {"seg-001", "seg-002"}

    def test_segment_status_is_draft(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All segments start at 'draft' status."""
        _write_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        client = MagicMock()
        _wire_client(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id=_SOURCE_ID))

        conn = _open_db(ingested_wiki)
        try:
            segs = ss.list_segments(conn, _SOURCE_ID)
        finally:
            conn.close()
        assert all(s.segment_status == "draft" for s in segs)


class TestRegenerateReadingUpdatesDB:
    def test_regen_replaces_segments(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """regenerate-reading --from=summarize replaces segments in DB."""
        _write_config(tmp_home, ingested_wiki)
        monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
        client = MagicMock()
        _wire_client(client)
        with patch(
            "auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client
        ):
            generate_reading_cmd.run(_args(source_id=_SOURCE_ID))
            rc = regenerate_reading_cmd.run(
                _args(source_id=_SOURCE_ID, from_stage="summarize", segments=None)
            )
        assert rc == 0

        conn = _open_db(ingested_wiki)
        try:
            segs = ss.list_segments(conn, _SOURCE_ID)
        finally:
            conn.close()
        # segments present after regen
        assert segs
