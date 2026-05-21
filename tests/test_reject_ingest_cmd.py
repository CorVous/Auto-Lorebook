"""End-to-end tests for the reject-ingest CLI command."""

from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
import yaml

from auto_lorebook import db as db_mod
from auto_lorebook import entities as entities_mod
from auto_lorebook import reading_pipeline
from auto_lorebook import wiki_state as wiki_state_mod
from auto_lorebook.commands import (
    approve_reading_cmd,
    extract_cmd,
    generate_reading_cmd,
    plan_cmd,
    reject_ingest_cmd,
    review_cmd,
)
from auto_lorebook.openrouter import OpenRouterResponse

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path


def _open_db(wiki: Path) -> sqlite3.Connection:
    return db_mod.open(wiki_state_mod.wiki_db_path(wiki))


def _db_entity(wiki: Path, category: str, slug: str) -> entities_mod.EntityRow | None:
    conn = _open_db(wiki)
    try:
        return entities_mod.get_entity(conn, category, slug)
    finally:
        conn.close()


_SRT = (
    "1\n"
    "00:00:00,000 --> 00:00:30,000\n"
    "Welcome to the session.\n"
    "\n"
    "2\n"
    "00:00:30,000 --> 00:02:00,000\n"
    "Today we talk about Aldara.\n"
    "\n"
    "3\n"
    "00:02:00,000 --> 00:05:00,000\n"
    "King Theron founded Aldara in the Second Age.\n"
)


def _stub_structure_payload() -> str:
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
                "title": "Founding of Aldara",
                "speaker": "DM",
            },
        ],
        "uncertainty_flags": [],
    })


def _seg_bullets_payload(segment_id: str) -> str:
    per_seg = {
        "seg-001": [{"text": "Intro bullet", "anchor": "0:00:15"}],
        "seg-002": [{"text": "King Theron founded Aldara", "anchor": "0:02:30"}],
    }
    return json.dumps({"bullets": per_seg.get(segment_id, [])})


def _stub_plan_payload() -> str:
    return json.dumps({
        "entity_resolutions": [
            {
                "mention": "Aldara",
                "mention_locations": ["[0:02:00-0:05:00] founding"],
                "resolution": "new",
                "proposed_entity_name": "Aldara",
                "proposed_category": "locations",
                "rationale": "Subject of this lore segment.",
            },
        ],
        "new_entities": [{"name": "Aldara", "category": "locations"}],
        "planned_claims": [
            {
                "claim_group_id": "cg-001",
                "reading_section": "[0:02:00-0:05:00] Founding of Aldara",
                "reading_bullet_index": 0,
                "locator": "0:02:30",
                "locator_hint": "0:02:00-0:02:30",
                "proposed_speaker": "DM",
                "proposed_status": "authoritative",
                "proposed_status_reason": None,
                "targets": [
                    {
                        "entity": "Aldara",
                        "entity_state": "new",
                        "proposed_section": "founding",
                        "proposed_category": "locations",
                        "rationale": "Founding fact.",
                    },
                ],
            }
        ],
        "unresolved": [],
    })


def _stub_extractor_payload() -> str:
    return json.dumps({
        "text": "King Theron founded Aldara in the Second Age.",
        "raw_transcript_span": "King Theron founded Aldara in the Second Age.",
        "text_corrects_transcript": False,
        "corrections_applied": [],
    })


def _wire_client(client_mock: MagicMock) -> None:
    def side_effect(
        messages: list[dict[str, str]], **_kw: object
    ) -> OpenRouterResponse:
        system = next((m for m in messages if m["role"] == "system"), {}).get(
            "content", ""
        )
        user = next((m for m in messages if m["role"] == "user"), {}).get("content", "")
        if "segmenting" in system:
            text = _stub_structure_payload()
        elif "routing claim bullets" in system:
            text = _stub_plan_payload()
        elif "locate the verbatim transcript span" in system:
            text = _stub_extractor_payload()
        else:
            for seg_id in ("seg-001", "seg-002"):
                if seg_id in user:
                    text = _seg_bullets_payload(seg_id)
                    break
            else:
                text = json.dumps({"bullets": []})
        return OpenRouterResponse(text=text, model="m", tokens_in=0, tokens_out=0)

    client_mock.complete.side_effect = side_effect


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    return home


@pytest.fixture
def ingested_wiki(tmp_wiki: Path, tmp_home: Path) -> Path:  # noqa: ARG001
    source_id = "yt-abc12345678"
    src_dir = tmp_wiki / "sources" / source_id
    src_dir.mkdir(parents=True)
    (src_dir / "transcript.en.srt").write_text(_SRT, encoding="utf-8")
    info = {
        "schema_version": 1,
        "source_id": source_id,
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


def _write_user_config(home: Path, wiki: Path) -> None:
    cfg_path = home / "config.yaml"
    cfg_path.write_text(
        f"""schema_version: 2
active_wiki: main
wikis:
- nickname: main
  path: {wiki}
openrouter:
  api_key_env: FAKE_OR_KEY
models:
  primary: anthropic/claude-sonnet-4-5
""",
        encoding="utf-8",
    )


def _args(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


SOURCE_ID = "yt-abc12345678"


def _approve_one(
    tmp_home: Path,
    wiki: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive the pipeline to a single approved fact under SOURCE_ID."""
    _write_user_config(tmp_home, wiki)
    monkeypatch.setenv("FAKE_OR_KEY", "sk-fake")
    client = MagicMock()
    _wire_client(client)
    review_client = MagicMock()
    review_client.complete.return_value = MagicMock(text='{"prose": "Stub prose."}')
    with (
        patch("auto_lorebook.reading_pipeline.OpenRouterClient", return_value=client),
        patch("auto_lorebook.review.OpenRouterClient", return_value=review_client),
    ):
        generate_reading_cmd.run(_args(source_id=SOURCE_ID))
        approve_reading_cmd.run(_args(source_id=SOURCE_ID, yes=True))
        plan_cmd.run(_args(source_id=SOURCE_ID))
        extract_cmd.run(_args(source_id=SOURCE_ID))
        review_cmd.run(_args(source_id=SOURCE_ID, auto_approve=True))


class TestRejectIngest:
    def test_yes_skips_prompt(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        aldara_md = ingested_wiki / "locations" / "aldara.md"
        assert aldara_md.exists()
        rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Rejected ingest" in out
        assert not aldara_md.exists()
        assert _db_entity(ingested_wiki, "locations", "aldara") is None

    def test_tty_guard_refuses_without_yes(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        with patch(
            "auto_lorebook.commands.reject_ingest._is_interactive",
            return_value=False,
        ):
            rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=False))
        assert rc == 1
        # Entity still in place — guard prevented the destructive op.
        assert (ingested_wiki / "locations" / "aldara.md").exists()
        assert _db_entity(ingested_wiki, "locations", "aldara") is not None

    def test_confirmation_y_runs(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        aldara_md = ingested_wiki / "locations" / "aldara.md"
        with (
            patch(
                "auto_lorebook.commands.reject_ingest._is_interactive",
                return_value=True,
            ),
            patch("builtins.input", side_effect=["y"]),
        ):
            rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=False))
        assert rc == 0
        assert not aldara_md.exists()

    def test_confirmation_n_keeps_state(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        aldara_md = ingested_wiki / "locations" / "aldara.md"
        with (
            patch(
                "auto_lorebook.commands.reject_ingest._is_interactive",
                return_value=True,
            ),
            patch("builtins.input", side_effect=[""]),  # blank == no
        ):
            rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=False))
        assert rc == 0
        assert aldara_md.exists()
        assert _db_entity(ingested_wiki, "locations", "aldara") is not None
        out = capsys.readouterr().out
        assert "Cancelled" in out

    def test_end_to_end_cleanup(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        # Pre-conditions: stub exists, pending plan + (drained) proposals dir
        aldara_md = ingested_wiki / "locations" / "aldara.md"
        plan_path = reading_pipeline.pending_plan_path(SOURCE_ID)
        proposals_dir = reading_pipeline.pending_proposals_dir(SOURCE_ID)
        sources_dir = ingested_wiki / "sources" / SOURCE_ID
        assert aldara_md.exists()
        assert _db_entity(ingested_wiki, "locations", "aldara") is not None
        assert plan_path.exists()
        # proposals dir exists but is empty (auto-approve drained it)
        assert proposals_dir.is_dir()
        assert sources_dir.is_dir()

        rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=True))
        assert rc == 0
        assert not aldara_md.exists()
        assert _db_entity(ingested_wiki, "locations", "aldara") is None
        assert not plan_path.exists()
        assert not proposals_dir.exists()
        # sources/ untouched
        assert (sources_dir / "info.yaml").exists()
        assert (sources_dir / "transcript.en.srt").exists()
        # reading-stage DB state survives (segments left intact for re-run)
        from auto_lorebook import db as db_mod  # noqa: PLC0415
        from auto_lorebook import structure_store as ss_mod  # noqa: PLC0415
        from auto_lorebook import wiki_state as ws_mod  # noqa: PLC0415

        conn = db_mod.open(ws_mod.wiki_db_path(ingested_wiki))
        try:
            segs = ss_mod.list_segments(conn, SOURCE_ID)
        finally:
            conn.close()
        assert segs

    def test_nothing_to_reject(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_user_config(tmp_home, ingested_wiki)
        # No ingest has run; nothing to reject.
        rc = reject_ingest_cmd.run(_args(source_id="ingest-that-never-was", yes=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Nothing to reject" in out

    def test_proper_entity_db_round_trip(
        self,
        tmp_home: Path,
        ingested_wiki: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After yes-rejection, no DB rows or .md files reference this ingest."""
        _approve_one(tmp_home, ingested_wiki, monkeypatch)
        rc = reject_ingest_cmd.run(_args(source_id=SOURCE_ID, yes=True))
        assert rc == 0
        conn = _open_db(ingested_wiki)
        try:
            # no entities created by this ingest
            entity_count = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE created_by_ingest=?",
                (SOURCE_ID,),
            ).fetchone()[0]
            assert entity_count == 0
            # no facts created by this ingest
            fact_count = conn.execute(
                "SELECT COUNT(*) FROM facts WHERE created_by_ingest=?",
                (SOURCE_ID,),
            ).fetchone()[0]
            assert fact_count == 0
        finally:
            conn.close()


class TestPageReconciliationIntegration:
    """Integration: reject-ingest page reconciliation."""

    def _seed_entity_direct(
        self,
        conn: sqlite3.Connection,
        *,
        name: str,
        category: str,
        slug: str,
        created_by: str,
    ) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO sources"
            "(source_id, source_type, fetched_at, context_json)"
            " VALUES (?, 'youtube', '2026-01-01T00:00:00Z', '{}')",
            (created_by,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ingests(ingest_id, source_id, started_at, state)"
            " VALUES (?, ?, '2026-01-01T00:00:00Z', 'done')",
            (created_by, created_by),
        )
        ts = "2026-04-20T00:00:00Z"
        conn.execute(
            "INSERT OR IGNORE INTO entities"
            "(category, slug, canonical_name,"
            " created_at, created_by_ingest, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (category, slug, name, ts, created_by, ts),
        )

    def _seed_shared_fact_direct(
        self,
        conn: sqlite3.Connection,
        *,
        fact_id: str,
        ingest: str,
        cat_a: str,
        slug_a: str,
        cat_b: str,
        slug_b: str,
        text: str = "Shared lore.",
    ) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO sources"
            "(source_id, source_type, fetched_at, context_json)"
            " VALUES (?, 'youtube', '2026-01-01T00:00:00Z', '{}')",
            (ingest,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ingests(ingest_id, source_id, started_at, state)"
            " VALUES (?, ?, '2026-01-01T00:00:00Z', 'done')",
            (ingest, ingest),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO facts (
                id, text, raw_transcript_span, text_corrects_transcript,
                text_source, edited_by_human, edited_at,
                source_id, locator, speaker,
                status, status_reason, session_date,
                approved_at, created_by_ingest, claim_group_id,
                corrections_applied_json, inputs_json
            ) VALUES (?,?,?,0,NULL,0,NULL,?,?,?,?,NULL,?,?,?,NULL,'[]',NULL)
            """,
            (
                fact_id,
                text,
                text,
                ingest,
                "0:01:00",
                "DM",
                "authoritative",
                "2026-04-15",
                "2026-04-20T00:00:00Z",
                ingest,
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO fact_targets"
            "(fact_id, entity_category, entity_slug, section)"
            " VALUES (?,?,?,?)",
            (fact_id, cat_a, slug_a, "s1"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO fact_targets"
            "(fact_id, entity_category, entity_slug, section)"
            " VALUES (?,?,?,?)",
            (fact_id, cat_b, slug_b, "s2"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO fact_status_history(fact_id, status, at, by, reason)"
            " VALUES (?,?,?,?,NULL)",
            (fact_id, "authoritative", "2026-04-20T00:00:00Z", "test"),
        )

    def _seed_own_fact_direct(
        self,
        conn: sqlite3.Connection,
        *,
        fact_id: str,
        ingest: str,
        category: str,
        slug: str,
    ) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO sources"
            "(source_id, source_type, fetched_at, context_json)"
            " VALUES (?, 'youtube', '2026-01-01T00:00:00Z', '{}')",
            (ingest,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ingests(ingest_id, source_id, started_at, state)"
            " VALUES (?, ?, '2026-01-01T00:00:00Z', 'done')",
            (ingest, ingest),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO facts (
                id, text, raw_transcript_span, text_corrects_transcript,
                text_source, edited_by_human, edited_at,
                source_id, locator, speaker,
                status, status_reason, session_date,
                approved_at, created_by_ingest, claim_group_id,
                corrections_applied_json, inputs_json
            ) VALUES (?,?,?,0,NULL,0,NULL,?,?,?,?,NULL,?,?,?,NULL,'[]',NULL)
            """,
            (
                fact_id,
                "Own fact.",
                "Own fact.",
                ingest,
                "0:02:00",
                "DM",
                "authoritative",
                "2026-04-15",
                "2026-04-20T00:00:00Z",
                ingest,
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO fact_targets"
            "(fact_id, entity_category, entity_slug, section)"
            " VALUES (?,?,?,?)",
            (fact_id, category, slug, "general"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO fact_status_history(fact_id, status, at, by, reason)"
            " VALUES (?,?,?,?,NULL)",
            (fact_id, "authoritative", "2026-04-20T00:00:00Z", "test"),
        )

    def test_deleted_entity_page_removed_and_neighbour_resummarized(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Deleted entity page removed; linked survivor re-summarized."""
        from auto_lorebook import db as db_mod  # noqa: PLC0415
        from auto_lorebook import wiki_bootstrap  # noqa: PLC0415
        from auto_lorebook import wiki_state as ws_mod  # noqa: PLC0415
        from auto_lorebook.config import Config  # noqa: PLC0415
        from auto_lorebook.ingest_cleanup import reject_ingest  # noqa: PLC0415
        from auto_lorebook.wiki_registry import WikiEntry  # noqa: PLC0415

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
        wiki = tmp_path / "wiki"
        wiki_bootstrap.bootstrap(wiki)

        # write config.yaml so reading_pipeline._active_wiki_root() resolves
        (home / "config.yaml").write_text(
            "schema_version: 2\nactive_wiki: main\nwikis:\n"
            f"- nickname: main\n  path: {wiki}\n",
            encoding="utf-8",
        )
        cfg = Config(wikis=[WikiEntry("main", wiki)], active_wiki="main")

        conn = db_mod.open(ws_mod.wiki_db_path(wiki))
        # aldara: created by yt-x → will be fully removed
        self._seed_entity_direct(
            conn, name="Aldara", category="locations", slug="aldara", created_by="yt-x"
        )
        # theron: created by other-ingest → linked survivor
        self._seed_entity_direct(
            conn,
            name="Theron",
            category="characters",
            slug="theron",
            created_by="other-ingest",
        )
        # shared fact from yt-x linking both; theron also has an own fact
        self._seed_shared_fact_direct(
            conn,
            fact_id="shared-f1",
            ingest="yt-x",
            cat_a="locations",
            slug_a="aldara",
            cat_b="characters",
            slug_b="theron",
        )
        self._seed_own_fact_direct(
            conn,
            fact_id="theron-own-f1",
            ingest="other-ingest",
            category="characters",
            slug="theron",
        )
        conn.commit()
        conn.close()

        # pre-create .md files
        aldara_md = wiki / "locations" / "aldara.md"
        aldara_md.write_text("old aldara", encoding="utf-8")
        theron_md = wiki / "characters" / "theron.md"
        theron_md.write_text("old theron", encoding="utf-8")

        reject_ingest(cfg, "yt-x")

        # aldara's page removed
        assert not aldara_md.exists(), "deleted entity page should be gone"
        # theron's page regenerated
        assert theron_md.exists(), "linked survivor's page should remain"
        assert theron_md.read_text(encoding="utf-8") != "old theron", (
            "linked survivor's page should be re-summarized"
        )


class TestRejectIngestIsolation:
    """Pending state for each wiki is isolated under its own .wiki-state/."""

    def test_reject_ingest_isolated_per_wiki(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reject in wiki_a leaves wiki_b's pending artifacts intact."""
        from auto_lorebook import wiki_bootstrap, wiki_state  # noqa: PLC0415
        from auto_lorebook.config import Config  # noqa: PLC0415
        from auto_lorebook.ingest_cleanup import reject_ingest  # noqa: PLC0415
        from auto_lorebook.wiki_registry import WikiEntry  # noqa: PLC0415

        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))

        wiki_a = tmp_path / "wiki_a"
        wiki_b = tmp_path / "wiki_b"
        wiki_bootstrap.bootstrap(wiki_a)
        wiki_bootstrap.bootstrap(wiki_b)

        source_id = "yt-shared-sid"

        # Write a plan.yaml under each wiki's .wiki-state/
        import yaml as _yaml  # noqa: PLC0415

        plan_stub = _yaml.safe_dump({
            "schema_version": 1,
            "source_id": source_id,
            "planned_at": "2026-04-20T00:00:00Z",
            "entity_resolutions": [],
            "new_entities": [],
            "planned_claims": [],
            "unresolved": [],
        })
        plan_a = wiki_state.pending_plan_path(wiki_a, source_id)
        plan_b = wiki_state.pending_plan_path(wiki_b, source_id)
        plan_a.parent.mkdir(parents=True, exist_ok=True)
        plan_b.parent.mkdir(parents=True, exist_ok=True)
        plan_a.write_text(plan_stub, encoding="utf-8")
        plan_b.write_text(plan_stub, encoding="utf-8")

        # Config pointing at wiki_a; write config.yaml so load_config() works
        (home / "config.yaml").write_text(
            "schema_version: 2\nactive_wiki: a\nwikis:\n"
            f"- nickname: a\n  path: {wiki_a}\n",
            encoding="utf-8",
        )
        cfg_a = Config(wikis=[WikiEntry("a", wiki_a)], active_wiki="a")

        reject_ingest(cfg_a, source_id)

        # wiki_a's plan removed; wiki_b's plan untouched
        assert not plan_a.exists(), "plan_a should have been removed"
        assert plan_b.exists(), "plan_b must not be touched"
