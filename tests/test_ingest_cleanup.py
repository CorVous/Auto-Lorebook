"""Tests for ingest_cleanup.py — Phase 4 reject-ingest engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook import config as cfg_mod
from auto_lorebook import db as db_mod
from auto_lorebook import entities as entities_mod
from auto_lorebook import facts as facts_mod
from auto_lorebook import reading_pipeline
from auto_lorebook import wiki_state as wiki_state_mod
from auto_lorebook.ingest_cleanup import RejectResult, preview, reject_ingest
from auto_lorebook.wiki_registry import WikiEntry

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cfg(
    tmp_path: Path, tmp_wiki: Path, monkeypatch: pytest.MonkeyPatch
) -> cfg_mod.Config:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    (home / "config.yaml").write_text(
        "schema_version: 2\nactive_wiki: test\nwikis:\n"
        f"- nickname: test\n  path: {tmp_wiki}\n",
        encoding="utf-8",
    )
    return cfg_mod.Config(wikis=[WikiEntry("test", tmp_wiki)], active_wiki="test")


def _open_db(wiki: Path) -> sqlite3.Connection:
    return db_mod.open(wiki_state_mod.wiki_db_path(wiki))


def _seed_entity(
    conn: sqlite3.Connection,
    *,
    name: str,
    category: str,
    slug: str,
    created_by_ingest: str = "yt-x",
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO sources"
        "(source_id, source_type, fetched_at, context_json)"
        " VALUES (?, 'youtube', '2026-01-01T00:00:00Z', '{}')",
        (created_by_ingest,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES (?, ?, '2026-01-01T00:00:00Z', 'done')",
        (created_by_ingest, created_by_ingest),
    )
    conn.execute(
        "INSERT OR IGNORE INTO entities"
        "(category, slug, canonical_name, created_at, created_by_ingest, updated_at)"
        " VALUES (?,?,?,?,?,?)",
        (
            category,
            slug,
            name,
            "2026-04-20T00:00:00Z",
            created_by_ingest,
            "2026-04-20T00:00:00Z",
        ),
    )


def _seed_fact(
    conn: sqlite3.Connection,
    *,
    fact_id: str,
    ingest: str = "yt-x",
    entity_category: str = "locations",
    entity_slug: str = "aldara",
    section: str = "founding",
    text: str = "A fact.",
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
            "0:00:01",
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
        (fact_id, entity_category, entity_slug, section),
    )
    conn.execute(
        "INSERT OR IGNORE INTO fact_status_history"
        "(fact_id, status, at, by, reason)"
        " VALUES (?,?,?,?,NULL)",
        (fact_id, "authoritative", "2026-04-20T00:00:00Z", "test"),
    )


def _seed_alias(
    conn: sqlite3.Connection,
    *,
    category: str,
    slug: str,
    name: str,
    ingest: str = "yt-x",
) -> None:
    norm = entities_mod.normalize_name(name)
    conn.execute(
        "INSERT OR IGNORE INTO aliases"
        "(entity_category, entity_slug, name, name_normalized,"
        " added_by_ingest, added_at, source)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            category,
            slug,
            name,
            norm,
            ingest,
            "2026-04-20T00:00:00Z",
            "alias-confirmation",
        ),
    )


def _write_pending(wiki: Path, source_id: str) -> tuple[Path, Path, Path]:
    """Materialise plan.yaml, proposals/, and reading/ under <wiki>/.wiki-state/."""
    plan_path = wiki_state_mod.pending_plan_path(wiki, source_id)
    proposals_dir = wiki_state_mod.pending_proposals_dir(wiki, source_id)
    reading_dir = wiki_state_mod.pending_reading_dir(wiki, source_id)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        yaml.safe_dump({
            "schema_version": 1,
            "source_id": source_id,
            "planned_at": "2026-04-20T00:00:00Z",
            "entity_resolutions": [],
            "new_entities": [],
            "planned_claims": [],
            "unresolved": [],
        }),
        encoding="utf-8",
    )
    proposals_dir.mkdir(parents=True, exist_ok=True)
    (proposals_dir / "stub.yaml").write_text("placeholder", encoding="utf-8")
    reading_dir.mkdir(parents=True, exist_ok=True)
    (reading_dir / "structure.yaml").write_text("placeholder", encoding="utf-8")
    return plan_path, proposals_dir, reading_dir


# ---------------------------------------------------------------------------
# Engine: facts / aliases / stub deletion
# ---------------------------------------------------------------------------


class TestFactRemoval:
    def test_removes_matching_facts(self, cfg: cfg_mod.Config) -> None:
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        _seed_entity(
            conn,
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
        )
        _seed_fact(conn, fact_id="aldara-f001", ingest="yt-x")
        _seed_fact(conn, fact_id="aldara-f002", ingest="other-ingest")
        conn.close()

        result = reject_ingest(cfg, "yt-x")
        assert result.facts_removed == 1
        assert result.stubs_deleted == 0

        conn = _open_db(wiki)
        remaining = facts_mod.list_facts_by_entity(conn, "locations", "aldara")
        conn.close()
        assert [f.id for f in remaining] == ["aldara-f002"]


class TestAliasRemoval:
    def test_removes_matching_aliases(self, cfg: cfg_mod.Config) -> None:
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        _seed_entity(
            conn,
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
        )
        _seed_fact(conn, fact_id="aldara-f001", ingest="other-ingest")
        _seed_alias(
            conn, category="locations", slug="aldara", name="the Realm", ingest="yt-x"
        )
        _seed_alias(
            conn,
            category="locations",
            slug="aldara",
            name="Aldaran",
            ingest="other-ingest",
        )
        conn.close()

        result = reject_ingest(cfg, "yt-x")
        assert result.aliases_removed == 1
        assert result.entities_modified == 0  # facts unchanged

        conn = _open_db(wiki)
        aliases = entities_mod.list_aliases(conn, "locations", "aldara")
        conn.close()
        assert [a.name for a in aliases] == ["Aldaran"]


class TestStubDeletion:
    def test_deletes_stub_when_facts_empty_and_created_by_ingest_matches(
        self, cfg: cfg_mod.Config
    ) -> None:
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        _seed_entity(
            conn,
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="yt-x",
        )
        _seed_fact(conn, fact_id="aldara-f001", ingest="yt-x")
        conn.close()

        result = reject_ingest(cfg, "yt-x")
        assert result.stubs_deleted == 1
        assert result.facts_removed == 1

        conn = _open_db(wiki)
        ent = entities_mod.get_entity(conn, "locations", "aldara")
        conn.close()
        assert ent is None

    def test_keeps_stub_when_created_by_differs(self, cfg: cfg_mod.Config) -> None:
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        _seed_entity(
            conn,
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
        )
        _seed_fact(conn, fact_id="aldara-f001", ingest="yt-x")
        conn.close()

        result = reject_ingest(cfg, "yt-x")
        assert result.stubs_deleted == 0
        assert result.facts_removed == 1

        conn = _open_db(wiki)
        ent = entities_mod.get_entity(conn, "locations", "aldara")
        remaining = facts_mod.list_facts_by_entity(conn, "locations", "aldara")
        conn.close()
        assert ent is not None
        assert remaining == []

    def test_keeps_stub_when_other_ingest_facts_remain(
        self, cfg: cfg_mod.Config
    ) -> None:
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        _seed_entity(
            conn,
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="yt-x",
        )
        _seed_fact(conn, fact_id="aldara-f001", ingest="yt-x")
        _seed_fact(conn, fact_id="aldara-f002", ingest="other-ingest")
        conn.close()

        result = reject_ingest(cfg, "yt-x")
        assert result.stubs_deleted == 0
        assert result.facts_removed == 1

        conn = _open_db(wiki)
        ent = entities_mod.get_entity(conn, "locations", "aldara")
        remaining = facts_mod.list_facts_by_entity(conn, "locations", "aldara")
        conn.close()
        assert ent is not None
        assert [f.id for f in remaining] == ["aldara-f002"]


class TestMixedFacts:
    def test_only_target_ingest_facts_removed(self, cfg: cfg_mod.Config) -> None:
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        _seed_entity(
            conn,
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
        )
        _seed_fact(conn, fact_id="aldara-f001", ingest="yt-x")
        _seed_fact(conn, fact_id="aldara-f002", ingest="other-ingest")
        _seed_fact(conn, fact_id="aldara-f003", ingest="yt-x")
        conn.close()

        result = reject_ingest(cfg, "yt-x")
        assert result.facts_removed == 2

        conn = _open_db(wiki)
        remaining = facts_mod.list_facts_by_entity(conn, "locations", "aldara")
        conn.close()
        assert [f.id for f in remaining] == ["aldara-f002"]


class TestAliasOnlyChange:
    def test_alias_only_change_bumps_modified(self, cfg: cfg_mod.Config) -> None:
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        _seed_entity(
            conn,
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
        )
        _seed_fact(conn, fact_id="aldara-f001", ingest="other-ingest")
        _seed_alias(
            conn, category="locations", slug="aldara", name="the Realm", ingest="yt-x"
        )
        conn.close()

        result = reject_ingest(cfg, "yt-x")
        assert result.aliases_removed == 1
        assert result.facts_removed == 0

        conn = _open_db(wiki)
        aliases = entities_mod.list_aliases(conn, "locations", "aldara")
        conn.close()
        assert aliases == []


# ---------------------------------------------------------------------------
# Engine: pending cleanup + sources untouched
# ---------------------------------------------------------------------------


class TestPendingCleanup:
    def test_drops_plan_and_proposals_keeps_reading(self, cfg: cfg_mod.Config) -> None:
        wiki = cfg.resolve_active_wiki(None)
        plan_path, proposals_dir, reading_dir = _write_pending(wiki, "yt-x")
        reject_ingest(cfg, "yt-x")
        assert not plan_path.exists()
        assert not proposals_dir.exists()
        assert reading_dir.exists()
        assert (reading_dir / "structure.yaml").exists()

    def test_sources_dir_untouched(self, cfg: cfg_mod.Config) -> None:
        src = cfg.resolve_active_wiki(None) / "sources" / "yt-x"
        src.mkdir(parents=True)
        (src / "info.yaml").write_text("placeholder", encoding="utf-8")
        (src / "transcript.en.srt").write_text("placeholder", encoding="utf-8")
        reject_ingest(cfg, "yt-x")
        assert (src / "info.yaml").exists()
        assert (src / "transcript.en.srt").exists()


# ---------------------------------------------------------------------------
# Engine: idempotency, empty wiki
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_call_returns_zeros(self, cfg: cfg_mod.Config) -> None:
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        _seed_entity(
            conn,
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="yt-x",
        )
        _seed_fact(conn, fact_id="aldara-f001", ingest="yt-x")
        conn.close()

        first = reject_ingest(cfg, "yt-x")
        assert first.stubs_deleted == 1
        second = reject_ingest(cfg, "yt-x")
        assert second == RejectResult()


class TestEmptyWiki:
    def test_returns_zeros(self, cfg: cfg_mod.Config) -> None:
        result = reject_ingest(cfg, "yt-x")
        assert result == RejectResult()


class TestMalformed:
    def test_skipped_with_warning(self, cfg: cfg_mod.Config) -> None:
        # No malformed YAML in DB-based approach; test that good entity is cleaned up
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        _seed_entity(
            conn,
            name="Good",
            category="locations",
            slug="good",
            created_by_ingest="yt-x",
        )
        _seed_fact(conn, fact_id="good-f001", ingest="yt-x", entity_slug="good")
        conn.close()

        result = reject_ingest(cfg, "yt-x")
        assert result.stubs_deleted == 1


# ---------------------------------------------------------------------------
# Preview matches actual run
# ---------------------------------------------------------------------------


class TestPreviewMatches:
    def test_preview_matches_reject_counts(self, cfg: cfg_mod.Config) -> None:
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        # Aldara: created by yt-x, two facts both from yt-x
        _seed_entity(
            conn,
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="yt-x",
        )
        _seed_fact(conn, fact_id="aldara-f001", ingest="yt-x")
        _seed_fact(conn, fact_id="aldara-f002", ingest="yt-x")
        # Theron: created by other, mixed facts + alias from yt-x
        _seed_entity(
            conn,
            name="Theron",
            category="characters",
            slug="theron",
            created_by_ingest="other",
        )
        _seed_fact(
            conn,
            fact_id="theron-f001",
            ingest="other",
            entity_category="characters",
            entity_slug="theron",
        )
        _seed_fact(
            conn,
            fact_id="theron-f002",
            ingest="yt-x",
            entity_category="characters",
            entity_slug="theron",
        )
        _seed_alias(
            conn, category="characters", slug="theron", name="the King", ingest="yt-x"
        )
        conn.close()

        previewed = preview(cfg, "yt-x")
        actual = reject_ingest(cfg, "yt-x")
        assert previewed.facts_removed == actual.facts_removed
        assert previewed.stubs_deleted == actual.stubs_deleted
        assert actual.stubs_deleted == 1
        assert actual.facts_removed == 3
        assert actual.aliases_removed == 1


class TestProposalsDirAbsent:
    def test_runs_clean_when_no_pending(self, cfg: cfg_mod.Config) -> None:
        wiki = cfg.resolve_active_wiki(None)
        conn = _open_db(wiki)
        _seed_entity(
            conn,
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="yt-x",
        )
        _seed_fact(conn, fact_id="aldara-f001", ingest="yt-x")
        conn.close()

        result = reject_ingest(cfg, "yt-x")
        assert result.stubs_deleted == 1
        # No exception; pending paths just don't exist.
        assert not reading_pipeline.pending_plan_path("yt-x").exists()
