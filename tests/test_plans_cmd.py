"""Tests for the `plans` subcommand group."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

import pytest

from auto_lorebook import plan_yaml
from auto_lorebook.commands import plans_cmd
from auto_lorebook.plan_yaml import (
    ClaimTarget,
    EntityResolution,
    NewEntityProposal,
    Plan,
    PlannedClaim,
    Unresolved,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUTO_LOREBOOK_HOME", str(home))
    return home


def _write_config(home: Path, wiki: Path) -> None:
    (home / "config.yaml").write_text(
        "schema_version: 2\nactive_wiki: main\nwikis:\n"
        f"- nickname: main\n  path: {wiki}\n",
        encoding="utf-8",
    )


def _args(**kwargs: object) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _write_plan(wiki: Path, source_id: str) -> Path:
    from auto_lorebook import db as db_mod  # noqa: PLC0415
    from auto_lorebook import wiki_state  # noqa: PLC0415

    plan = Plan(
        source_id=source_id,
        planned_at="2026-04-20T14:58:33Z",
        entity_resolutions=[
            EntityResolution(
                mention="Aldara",
                resolution="existing",
                matched_entity="Aldara",
                rationale="Mentioned.",
            ),
        ],
        new_entities=[NewEntityProposal(name="Second Age", category="events")],
        planned_claims=[
            PlannedClaim(
                claim_group_id="cg-001",
                reading_section="[0:00:00-0:01:00] Founding",
                reading_bullet_index=0,
                locator="0:00:30",
                locator_hint="0:00:20-0:00:45",
                proposed_speaker="DM",
                proposed_status="authoritative",
                proposed_status_reason=None,
                targets=[
                    ClaimTarget(
                        entity="Aldara",
                        entity_state="existing",
                        proposed_section="founding",
                    ),
                    ClaimTarget(
                        entity="Second Age",
                        entity_state="new",
                        proposed_section="events-in-era",
                        proposed_category="events",
                    ),
                ],
            )
        ],
        unresolved=[
            Unresolved(
                reading_section="[0:01:00-0:02:00]",
                locator="0:01:10",
                issue="Uncertain name.",
            )
        ],
    )
    # write to DB
    conn = db_mod.open(wiki_state.wiki_db_path(wiki))
    try:
        # ensure source + ingest rows exist
        conn.execute(
            "INSERT OR IGNORE INTO sources(source_id, source_type, fetched_at,"
            " context_json) VALUES (?,?,?,?)",
            (source_id, "youtube", "2026-01-01T00:00:00Z", "{}"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ingests(ingest_id, source_id, started_at, state)"
            " VALUES (?,?,?,?)",
            (source_id, source_id, "2026-01-01T00:00:00Z", "planned"),
        )
        plan_yaml.write_plan_routes(conn, source_id, plan)
        conn.commit()
    finally:
        conn.close()
    # also write YAML for legacy callers
    path = wiki_state.pending_plan_path(wiki, source_id)
    plan_yaml.write(plan, path)
    return path


class TestPlansList:
    def test_empty(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_config(tmp_home, tmp_wiki)
        rc = plans_cmd.run(_args(plans_action="list"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "(no plans)" in out

    def test_lists_pending(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_config(tmp_home, tmp_wiki)
        _write_plan(tmp_wiki, "yt-aaa")
        _write_plan(tmp_wiki, "yt-bbb")
        rc = plans_cmd.run(_args(plans_action="list"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "yt-aaa" in out
        assert "yt-bbb" in out
        assert "PLANNED_AT" in out

    def test_skips_non_plan_dirs(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from auto_lorebook import wiki_state  # noqa: PLC0415

        _write_config(tmp_home, tmp_wiki)
        # An ingest dir without plan.yaml should be ignored
        wiki_state.pending_source_dir(tmp_wiki, "yt-no-plan").mkdir(parents=True)
        rc = plans_cmd.run(_args(plans_action="list"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "(no plans)" in out


class TestPlansShow:
    def test_renders_summary(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_config(tmp_home, tmp_wiki)
        _write_plan(tmp_wiki, "yt-aaa")
        rc = plans_cmd.run(_args(plans_action="show", source_id="yt-aaa"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "yt-aaa" in out
        assert "entity_resolutions" in out
        assert "Aldara" in out
        assert "Second Age" in out
        assert "founding" in out
        assert "Uncertain name" in out

    def test_unknown_id_returns_1(
        self,
        tmp_home: Path,
        tmp_wiki: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_config(tmp_home, tmp_wiki)
        rc = plans_cmd.run(_args(plans_action="show", source_id="yt-missing"))
        assert rc == 1
        out = capsys.readouterr().out
        assert "No plan" in out


class TestParserRegistration:
    def test_parser_registered(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        common = argparse.ArgumentParser(add_help=False)
        plans_cmd.add_parser(sub, common)
        # Should parse without error
        args = parser.parse_args(["plans", "list"])
        assert args.plans_action == "list"

    def test_show_requires_source_id(self) -> None:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        common = argparse.ArgumentParser(add_help=False)
        plans_cmd.add_parser(sub, common)
        with pytest.raises(SystemExit):
            parser.parse_args(["plans", "show"])
