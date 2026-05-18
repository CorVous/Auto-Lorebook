"""Tests for ingest_cleanup.py — Phase 4 reject-ingest engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook import config as cfg_mod
from auto_lorebook import entity_yaml, reading_pipeline
from auto_lorebook.entity_yaml import Alias, Entity
from auto_lorebook.ingest_cleanup import RejectResult, preview, reject_ingest
from auto_lorebook.wiki_registry import WikiEntry

if TYPE_CHECKING:
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


def _fact(
    fact_id: str,
    *,
    ingest: str = "yt-x",
    text: str = "A fact.",
) -> dict:
    return {
        "id": fact_id,
        "text": text,
        "raw_transcript_span": text,
        "text_corrects_transcript": False,
        "corrections_applied": [],
        "edited_by_human": False,
        "source_id": "yt-x",
        "locator": "0:00:01-0:00:02",
        "speaker": "DM",
        "status": "authoritative",
        "session_date": "2026-04-15",
        "approved_at": "2026-04-20T00:00:00Z",
        "created_by_ingest": ingest,
        "claim_group_id": "cg-001",
        "section": "founding",
    }


def _write_entity(
    wiki: Path,
    *,
    name: str,
    category: str,
    slug: str,
    created_by_ingest: str | None = "yt-x",
    facts: list[dict] | None = None,
    aliases: list[Alias] | None = None,
) -> Path:
    e = Entity(
        entity=name,
        category=category,
        slug=slug,
        created_at="2026-04-20T00:00:00Z",
        created_by_ingest=created_by_ingest,
        updated_at="2026-04-20T00:00:00Z",
        facts=facts or [],
        aliases=aliases or [],
    )
    path = wiki / category / f"{slug}.yaml"
    entity_yaml.write(e, path)
    return path


def _write_pending(wiki: Path, source_id: str) -> tuple[Path, Path, Path]:
    """Materialise plan.yaml, proposals/, and reading/ under <wiki>/.wiki-state/."""
    from auto_lorebook import wiki_state  # noqa: PLC0415

    plan_path = wiki_state.pending_plan_path(wiki, source_id)
    proposals_dir = wiki_state.pending_proposals_dir(wiki, source_id)
    reading_dir = wiki_state.pending_reading_dir(wiki, source_id)
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
        path = _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
            facts=[
                _fact("aldara-f001", ingest="yt-x"),
                _fact("aldara-f002", ingest="other-ingest"),
            ],
        )
        result = reject_ingest(cfg, "yt-x")
        assert result.facts_removed == 1
        assert result.entities_modified == 1
        assert result.stubs_deleted == 0
        e = entity_yaml.read(path)
        assert [f["id"] for f in e.facts] == ["aldara-f002"]


class TestAliasRemoval:
    def test_removes_matching_aliases(self, cfg: cfg_mod.Config) -> None:
        path = _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
            facts=[_fact("aldara-f001", ingest="other-ingest")],
            aliases=[
                Alias(
                    name="the Realm",
                    added_by_ingest="yt-x",
                    added_at="2026-04-20T00:00:00Z",
                    source="alias-confirmation",
                ),
                Alias(
                    name="Aldaran",
                    added_by_ingest="other-ingest",
                    added_at="2026-04-20T00:00:00Z",
                    source="alias-confirmation",
                ),
            ],
        )
        result = reject_ingest(cfg, "yt-x")
        assert result.aliases_removed == 1
        assert result.entities_modified == 1
        e = entity_yaml.read(path)
        assert [a.name for a in e.aliases] == ["Aldaran"]


class TestStubDeletion:
    def test_deletes_stub_when_facts_empty_and_created_by_ingest_matches(
        self, cfg: cfg_mod.Config
    ) -> None:
        path = _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="yt-x",
            facts=[_fact("aldara-f001", ingest="yt-x")],
        )
        result = reject_ingest(cfg, "yt-x")
        assert result.stubs_deleted == 1
        assert result.facts_removed == 1
        assert not path.exists()

    def test_keeps_stub_when_created_by_differs(self, cfg: cfg_mod.Config) -> None:
        # Pre-existing entity, all facts happen to be from yt-x.
        path = _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
            facts=[_fact("aldara-f001", ingest="yt-x")],
        )
        result = reject_ingest(cfg, "yt-x")
        assert result.stubs_deleted == 0
        assert result.facts_removed == 1
        assert path.exists()
        e = entity_yaml.read(path)
        assert e.facts == []

    def test_keeps_stub_when_other_ingest_facts_remain(
        self, cfg: cfg_mod.Config
    ) -> None:
        # Created by yt-x but a later ingest also added facts.
        path = _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="yt-x",
            facts=[
                _fact("aldara-f001", ingest="yt-x"),
                _fact("aldara-f002", ingest="other-ingest"),
            ],
        )
        result = reject_ingest(cfg, "yt-x")
        assert result.stubs_deleted == 0
        assert result.facts_removed == 1
        assert path.exists()
        e = entity_yaml.read(path)
        assert [f["id"] for f in e.facts] == ["aldara-f002"]


class TestMixedFacts:
    def test_only_target_ingest_facts_removed(self, cfg: cfg_mod.Config) -> None:
        path = _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
            facts=[
                _fact("aldara-f001", ingest="yt-x"),
                _fact("aldara-f002", ingest="other-ingest"),
                _fact("aldara-f003", ingest="yt-x"),
            ],
        )
        result = reject_ingest(cfg, "yt-x")
        assert result.facts_removed == 2
        e = entity_yaml.read(path)
        assert [f["id"] for f in e.facts] == ["aldara-f002"]


class TestAliasOnlyChange:
    def test_alias_only_change_bumps_modified_and_updated_at(
        self, cfg: cfg_mod.Config
    ) -> None:
        path = _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
            facts=[_fact("aldara-f001", ingest="other-ingest")],
            aliases=[
                Alias(
                    name="the Realm",
                    added_by_ingest="yt-x",
                    added_at="2026-04-20T00:00:00Z",
                    source="alias-confirmation",
                ),
            ],
        )
        before = entity_yaml.read(path).updated_at
        result = reject_ingest(cfg, "yt-x")
        assert result.aliases_removed == 1
        assert result.facts_removed == 0
        assert result.entities_modified == 1
        after = entity_yaml.read(path).updated_at
        assert after != before
        assert after is not None


class TestHandEdited:
    def test_fact_without_ingest_tag_kept(self, cfg: cfg_mod.Config) -> None:
        # Hand-edited fact: no `created_by_ingest` field.
        f = _fact("aldara-f001", ingest="yt-x")
        f.pop("created_by_ingest")
        path = _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
            facts=[f],
        )
        result = reject_ingest(cfg, "yt-x")
        assert result.facts_removed == 0
        e = entity_yaml.read(path)
        assert len(e.facts) == 1

    def test_alias_without_ingest_tag_kept(self, cfg: cfg_mod.Config) -> None:
        path = _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="other-ingest",
            facts=[_fact("aldara-f001", ingest="other-ingest")],
            aliases=[
                Alias(
                    name="the Realm",
                    added_by_ingest=None,
                    added_at="2026-04-20T00:00:00Z",
                    source="hand-edited",
                ),
            ],
        )
        result = reject_ingest(cfg, "yt-x")
        assert result.aliases_removed == 0
        e = entity_yaml.read(path)
        assert len(e.aliases) == 1


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
# Engine: idempotency, empty wiki, malformed YAML
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_call_returns_zeros(self, cfg: cfg_mod.Config) -> None:
        _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="yt-x",
            facts=[_fact("aldara-f001", ingest="yt-x")],
        )
        first = reject_ingest(cfg, "yt-x")
        assert first.stubs_deleted == 1
        second = reject_ingest(cfg, "yt-x")
        assert second == RejectResult()


class TestEmptyWiki:
    def test_returns_zeros(self, cfg: cfg_mod.Config) -> None:
        result = reject_ingest(cfg, "yt-x")
        assert result == RejectResult()


class TestMalformed:
    def test_skipped_with_warning(
        self, cfg: cfg_mod.Config, caplog: pytest.LogCaptureFixture
    ) -> None:
        _write_entity(
            cfg.resolve_active_wiki(None),
            name="Good",
            category="locations",
            slug="good",
            created_by_ingest="yt-x",
            facts=[_fact("good-f001", ingest="yt-x")],
        )
        # Malformed entity in the same dir: no schema_version
        (cfg.resolve_active_wiki(None) / "locations" / "bad.yaml").write_text(
            "entity: Bad\ncategory: locations\nslug: bad\n",
            encoding="utf-8",
        )
        with caplog.at_level("WARNING"):
            result = reject_ingest(cfg, "yt-x")
        # Good entity still cleaned up
        assert result.stubs_deleted == 1
        assert any("could not parse" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Preview matches actual run
# ---------------------------------------------------------------------------


class TestPreviewMatches:
    def test_preview_matches_reject_counts(self, cfg: cfg_mod.Config) -> None:
        # Several entities with mixed ingests
        _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="yt-x",
            facts=[
                _fact("aldara-f001", ingest="yt-x"),
                _fact("aldara-f002", ingest="yt-x"),
            ],
        )
        _write_entity(
            cfg.resolve_active_wiki(None),
            name="Theron",
            category="characters",
            slug="theron",
            created_by_ingest="other",
            facts=[
                _fact("theron-f001", ingest="other"),
                _fact("theron-f002", ingest="yt-x"),
            ],
            aliases=[
                Alias(
                    name="the King",
                    added_by_ingest="yt-x",
                    added_at="2026-04-20T00:00:00Z",
                    source="alias-confirmation",
                ),
            ],
        )
        previewed = preview(cfg, "yt-x")
        actual = reject_ingest(cfg, "yt-x")
        assert previewed == actual
        assert actual.stubs_deleted == 1
        assert actual.facts_removed == 3
        assert actual.aliases_removed == 1
        assert actual.entities_modified == 1


class TestProposalsDirAbsent:
    def test_runs_clean_when_no_pending(self, cfg: cfg_mod.Config) -> None:
        # Just an entity, no pending dir
        _write_entity(
            cfg.resolve_active_wiki(None),
            name="Aldara",
            category="locations",
            slug="aldara",
            created_by_ingest="yt-x",
            facts=[_fact("aldara-f001", ingest="yt-x")],
        )
        result = reject_ingest(cfg, "yt-x")
        assert result.stubs_deleted == 1
        # No exception; pending paths just don't exist.
        assert not reading_pipeline.pending_plan_path("yt-x").exists()
