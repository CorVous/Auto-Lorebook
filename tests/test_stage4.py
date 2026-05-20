"""Tests for stage4.py — LLM-prose entity summarizer."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from auto_lorebook import db
from auto_lorebook.entities import AliasRow, EntityRow
from auto_lorebook.facts import (
    FactRow,
    create_fact_with_target,
    create_fact_with_targets,
)
from auto_lorebook.page_step import run_page_step
from auto_lorebook.stage4 import (
    Stage4Error,
    build_prompt,
    parse_response,
    render_entity_page,
    run,
    summarize_entity,
)

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_entity_row(
    name: str = "Theron",
    category: str = "characters",
    slug: str = "theron",
) -> EntityRow:
    return EntityRow(
        category=category,
        slug=slug,
        canonical_name=name,
        superseded_by_category=None,
        superseded_by_slug=None,
        created_at="2026-01-01T00:00:00Z",
        created_by_ingest="ing-001",
        updated_at="2026-01-01T00:00:00Z",
    )


def _make_fact_row(
    fact_id: str = "f-001",
    text: str = "Theron founded the city.",
    status: str = "authoritative",
    status_reason: str | None = None,
    source_id: str = "src-001",
    locator: str = "0:04:32-0:04:41",
    speaker: str | None = "DM",
    session_date: str | None = "2026-01-15",
) -> FactRow:
    return FactRow(
        id=fact_id,
        text=text,
        raw_transcript_span="raw span",
        text_corrects_transcript=False,
        text_source=None,
        edited_by_human=False,
        edited_at=None,
        source_id=source_id,
        locator=locator,
        speaker=speaker,
        status=status,
        status_reason=status_reason,
        session_date=session_date,
        approved_at="2026-01-15T10:00:00Z",
        created_by_ingest="ing-001",
        claim_group_id="cg-001",
        corrections_applied=[],
        inputs_json=None,
    )


def _make_alias_row(name: str = "King Theron") -> AliasRow:
    return AliasRow(
        entity_category="characters",
        entity_slug="theron",
        name=name,
        name_normalized=name.lower(),
        added_by_ingest="ing-001",
        added_at="2026-01-01T00:00:00Z",
        source="stub-creation",
    )


def _seed_conn() -> sqlite3.Connection:
    """In-memory DB with one entity and one source."""
    conn = db.open(":memory:")
    conn.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at, context_json)"
        " VALUES ('src-001', 'youtube', '2026-01-01T00:00:00Z', '{}')"
    )
    conn.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('ing-001', 'src-001', '2026-01-01T00:00:00Z', 'done')"
    )
    conn.execute(
        "INSERT INTO entities(category, slug, canonical_name, created_at,"
        " created_by_ingest, updated_at)"
        " VALUES ('characters', 'theron', 'Theron',"
        " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# stage4.build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_includes_entity_name(self) -> None:
        entity = _make_entity_row()
        prompt = build_prompt(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row()],
            entity_index="Characters:\n  - Theron",
            wiki_setting="A fantasy world called Aether.",
        )
        assert "Theron" in prompt

    def test_includes_wiki_setting(self) -> None:
        entity = _make_entity_row()
        prompt = build_prompt(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row()],
            entity_index="Characters:\n  - Theron",
            wiki_setting="Aether is a realm of ancient magic.",
        )
        assert "Aether is a realm of ancient magic." in prompt

    def test_includes_fact_text(self) -> None:
        entity = _make_entity_row()
        fact = _make_fact_row(text="Theron founded Aldara.")
        prompt = build_prompt(
            entity=entity,
            aliases=[],
            facts=[fact],
            entity_index="",
            wiki_setting="",
        )
        assert "Theron founded Aldara." in prompt

    def test_includes_entity_index(self) -> None:
        entity = _make_entity_row()
        prompt = build_prompt(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row()],
            entity_index="Characters:\n  - Aldara\n  - Theron",
            wiki_setting="",
        )
        assert "Aldara" in prompt

    def test_includes_disproven_status(self) -> None:
        entity = _make_entity_row()
        fact = _make_fact_row(
            status="disproven",
            status_reason="Contradicted by later evidence.",
        )
        prompt = build_prompt(
            entity=entity,
            aliases=[],
            facts=[fact],
            entity_index="",
            wiki_setting="",
        )
        assert "disproven" in prompt.lower()


# ---------------------------------------------------------------------------
# stage4.parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_returns_prose_field(self) -> None:
        payload = {"prose": "Theron is a legendary king."}
        result = parse_response(payload)
        assert result.prose == "Theron is a legendary king."

    def test_empty_prose_treated_as_empty_string(self) -> None:
        payload = {"prose": ""}
        result = parse_response(payload)
        assert not result.prose

    def test_missing_prose_raises(self) -> None:
        with pytest.raises(Stage4Error, match="prose"):
            parse_response({})


# ---------------------------------------------------------------------------
# stage4.run (mocked client)
# ---------------------------------------------------------------------------


class TestRun:
    def test_calls_client_complete(self) -> None:
        client = MagicMock()
        client.complete.return_value = MagicMock(
            text='{"prose": "Theron is a legendary king."}'
        )
        entity = _make_entity_row()
        result = run(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row()],
            entity_index="Characters:\n  - Theron",
            wiki_setting="A fantasy world.",
            client=client,
            model="test/model",
        )
        assert client.complete.called
        assert result.prose == "Theron is a legendary king."

    def test_prompt_assembly_passed_to_client(self) -> None:
        client = MagicMock()
        client.complete.return_value = MagicMock(
            text='{"prose": "Theron ruled wisely."}'
        )
        entity = _make_entity_row()
        run(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row(text="Theron ruled wisely.")],
            entity_index="Characters:\n  - Theron",
            wiki_setting="A fantasy world.",
            client=client,
            model="test/model",
        )
        call_args = client.complete.call_args
        messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
        user_content = " ".join(m["content"] for m in messages if m["role"] == "user")
        assert "Theron ruled wisely." in user_content


# ---------------------------------------------------------------------------
# stage4.render_entity_page — renderer
# ---------------------------------------------------------------------------


class TestRenderEntityPage:
    def test_heading_is_canonical_name(self) -> None:
        entity = _make_entity_row()
        result = render_entity_page(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row()],
            prose="Theron is a legendary king.",
            conn=None,
        )
        assert result.startswith("# Theron")

    def test_prose_summary_section(self) -> None:
        entity = _make_entity_row()
        result = render_entity_page(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row()],
            prose="Theron is a legendary king.",
            conn=None,
        )
        assert "## Summary" in result
        assert "Theron is a legendary king." in result

    def test_facts_section_present(self) -> None:
        entity = _make_entity_row()
        facts = [_make_fact_row(status="authoritative")]
        result = render_entity_page(
            entity=entity,
            aliases=[],
            facts=facts,
            prose="Some prose.",
            conn=None,
        )
        assert "## Facts" in result

    def test_authoritative_subsection(self) -> None:
        entity = _make_entity_row()
        facts = [_make_fact_row(status="authoritative")]
        result = render_entity_page(
            entity=entity,
            aliases=[],
            facts=facts,
            prose="Some prose.",
            conn=None,
        )
        assert "### Authoritative" in result

    def test_hearsay_subsection(self) -> None:
        entity = _make_entity_row()
        facts = [_make_fact_row(status="hearsay")]
        result = render_entity_page(
            entity=entity,
            aliases=[],
            facts=facts,
            prose="Some prose.",
            conn=None,
        )
        assert "### Hearsay" in result

    def test_disproven_struck_through(self) -> None:
        entity = _make_entity_row()
        facts = [
            _make_fact_row(
                status="disproven",
                text="Theron was evil.",
                status_reason="Later corrected.",
            )
        ]
        result = render_entity_page(
            entity=entity,
            aliases=[],
            facts=facts,
            prose="Some prose.",
            conn=None,
        )
        assert "~~Theron was evil.~~" in result
        assert "Later corrected." in result

    def test_references_section(self) -> None:
        entity = _make_entity_row()
        facts = [_make_fact_row(source_id="src-001")]
        result = render_entity_page(
            entity=entity,
            aliases=[],
            facts=facts,
            prose="Some prose.",
            conn=None,
        )
        assert "## References" in result

    def test_footnote_includes_quote(self) -> None:
        entity = _make_entity_row()
        facts = [_make_fact_row(text="Theron founded Aldara.")]
        result = render_entity_page(
            entity=entity,
            aliases=[],
            facts=facts,
            prose="Some prose.",
            conn=None,
        )
        assert "Theron founded Aldara." in result

    def test_aliases_section_when_present(self) -> None:
        entity = _make_entity_row()
        aliases = [_make_alias_row("King Theron")]
        result = render_entity_page(
            entity=entity,
            aliases=aliases,
            facts=[_make_fact_row()],
            prose="Some prose.",
            conn=None,
        )
        assert "## Aliases" in result
        assert "King Theron" in result

    def test_zero_fact_stub_no_llm(self) -> None:
        """Zero-fact entity: mechanical stub only, no prose required."""
        entity = _make_entity_row()
        result = render_entity_page(
            entity=entity,
            aliases=[],
            facts=[],
            prose=None,
            conn=None,
        )
        assert "# Theron" in result
        assert "## Summary" not in result
        assert "## Facts" not in result


# ---------------------------------------------------------------------------
# stage4.summarize_entity — zero-fact path skips LLM
# ---------------------------------------------------------------------------


class TestSummarizeEntity:
    def test_zero_fact_entity_skips_llm(self, tmp_path: Path) -> None:
        conn = _seed_conn()
        client = MagicMock()
        path = summarize_entity(
            conn=conn,
            wiki_repo=tmp_path,
            category="characters",
            slug="theron",
            entity_index="",
            wiki_setting="",
            client=client,
            model="test/model",
        )
        assert not client.complete.called
        assert path.exists()

    def test_entity_with_facts_calls_llm(self, tmp_path: Path) -> None:
        conn = _seed_conn()
        create_fact_with_target(
            conn,
            fact_id="f-001",
            text="Theron founded the city.",
            raw_transcript_span="raw",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:04:32",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            entity_category="characters",
            entity_slug="theron",
            section="biography",
            by="tester",
        )
        conn.commit()

        client = MagicMock()
        client.complete.return_value = MagicMock(
            text='{"prose": "Theron was a great king."}'
        )

        path = summarize_entity(
            conn=conn,
            wiki_repo=tmp_path,
            category="characters",
            slug="theron",
            entity_index="",
            wiki_setting="",
            client=client,
            model="test/model",
        )
        assert client.complete.called
        assert path.exists()
        content = path.read_text()
        assert "Theron was a great king." in content


# ---------------------------------------------------------------------------
# page_step.run_page_step — batched orchestrator
# ---------------------------------------------------------------------------


def _seed_entity_with_fact() -> sqlite3.Connection:
    """In-memory DB with entity and one fact."""
    conn = _seed_conn()
    create_fact_with_target(
        conn,
        fact_id="f-001",
        text="Theron founded the city.",
        raw_transcript_span="raw",
        text_corrects_transcript=False,
        source_id="src-001",
        locator="0:04:32",
        status="authoritative",
        approved_at="2026-01-15T10:00:00Z",
        created_by_ingest="ing-001",
        entity_category="characters",
        entity_slug="theron",
        section="biography",
        by="tester",
    )
    conn.commit()
    return conn


class TestRunPageStep:
    def test_reports_progress(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        conn = _seed_entity_with_fact()
        client = MagicMock()
        client.complete.return_value = MagicMock(text='{"prose": "Theron was great."}')

        run_page_step(
            conn=conn,
            wiki_repo=tmp_path,
            touched_entities=[("characters", "theron")],
            entity_index="",
            wiki_setting="",
            client=client,
            model="test/model",
        )
        captured = capsys.readouterr()
        assert "Summarizing" in captured.out or "summariz" in captured.out.lower()

    def test_writes_md_for_touched_entities(self, tmp_path: Path) -> None:
        conn = _seed_entity_with_fact()
        client = MagicMock()
        client.complete.return_value = MagicMock(text='{"prose": "Theron was great."}')

        paths = run_page_step(
            conn=conn,
            wiki_repo=tmp_path,
            touched_entities=[("characters", "theron")],
            entity_index="",
            wiki_setting="",
            client=client,
            model="test/model",
        )
        md_path = tmp_path / "characters" / "theron.md"
        assert md_path in paths
        assert md_path.exists()

    def test_approving_fact_on_a_regenerates_linked_b(self, tmp_path: Path) -> None:
        """Integration: touched entity A shares fact with B → B page also written."""
        conn = _seed_conn()
        # seed second entity (aldara)
        conn.execute(
            "INSERT INTO entities(category, slug, canonical_name, created_at,"
            " created_by_ingest, updated_at)"
            " VALUES ('locations', 'aldara', 'Aldara',"
            " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
        )
        # shared fact targeting both theron and aldara
        create_fact_with_targets(
            conn,
            fact_id="f-shared",
            text="Theron founded Aldara.",
            raw_transcript_span="raw",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:04:32",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            targets=[
                ("characters", "theron", "biography"),
                ("locations", "aldara", "founding"),
            ],
            by="tester",
        )
        conn.commit()

        client = MagicMock()
        client.complete.return_value = MagicMock(text='{"prose": "Generated prose."}')

        # only theron is "touched" — aldara should be regenerated as linked
        paths = run_page_step(
            conn=conn,
            wiki_repo=tmp_path,
            touched_entities=[("characters", "theron")],
            entity_index="",
            wiki_setting="",
            client=client,
            model="test/model",
        )
        aldara_path = tmp_path / "locations" / "aldara.md"
        assert aldara_path in paths
        assert aldara_path.exists()

    def test_progress_reports_linked_count(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Progress message includes linked entity info when links exist."""
        conn = _seed_conn()
        conn.execute(
            "INSERT INTO entities(category, slug, canonical_name, created_at,"
            " created_by_ingest, updated_at)"
            " VALUES ('locations', 'aldara', 'Aldara',"
            " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
        )
        create_fact_with_targets(
            conn,
            fact_id="f-shared",
            text="Theron founded Aldara.",
            raw_transcript_span="raw",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:04:32",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            targets=[
                ("characters", "theron", "biography"),
                ("locations", "aldara", "founding"),
            ],
            by="tester",
        )
        conn.commit()

        client = MagicMock()
        client.complete.return_value = MagicMock(text='{"prose": "Generated prose."}')

        run_page_step(
            conn=conn,
            wiki_repo=tmp_path,
            touched_entities=[("characters", "theron")],
            entity_index="",
            wiki_setting="",
            client=client,
            model="test/model",
        )
        captured = capsys.readouterr()
        # progress should mention linked count (1 linked)
        assert "linked" in captured.out.lower() or "1" in captured.out


# ---------------------------------------------------------------------------
# build_prompt — linked_facts parameter
# ---------------------------------------------------------------------------


class TestBuildPromptLinkedFacts:
    def test_no_linked_block_when_none(self) -> None:
        entity = _make_entity_row()
        prompt = build_prompt(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row()],
            entity_index="",
            wiki_setting="",
            linked_facts=None,
        )
        assert "Linked entities" not in prompt
        assert "LINKED" not in prompt

    def test_no_linked_block_when_empty_list(self) -> None:
        entity = _make_entity_row()
        prompt = build_prompt(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row()],
            entity_index="",
            wiki_setting="",
            linked_facts=[],
        )
        assert "Linked entities" not in prompt

    def test_linked_facts_appear_in_prompt(self) -> None:
        entity = _make_entity_row()
        linked_entity = _make_entity_row("Aldara", "locations", "aldara")
        linked_fact = _make_fact_row(
            fact_id="f-n01",
            text="Aldara was founded in the Second Age.",
            status="authoritative",
        )
        prompt = build_prompt(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row()],
            entity_index="",
            wiki_setting="",
            linked_facts=[(linked_entity, [linked_fact])],
        )
        assert "Aldara was founded in the Second Age." in prompt

    def test_linked_block_label_present(self) -> None:
        entity = _make_entity_row()
        linked_entity = _make_entity_row("Aldara", "locations", "aldara")
        linked_fact = _make_fact_row(fact_id="f-n01", text="Some linked fact.")
        prompt = build_prompt(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row()],
            entity_index="",
            wiki_setting="",
            linked_facts=[(linked_entity, [linked_fact])],
        )
        assert "Linked" in prompt

    def test_run_forwards_linked_facts(self) -> None:
        client = MagicMock()
        client.complete.return_value = MagicMock(
            text='{"prose": "Theron founded Aldara."}'
        )
        entity = _make_entity_row()
        linked_entity = _make_entity_row("Aldara", "locations", "aldara")
        linked_fact = _make_fact_row(
            fact_id="f-n01",
            text="Aldara is an ancient city.",
            status="trustworthy",
        )
        result = run(
            entity=entity,
            aliases=[],
            facts=[_make_fact_row()],
            entity_index="",
            wiki_setting="",
            client=client,
            model="test/model",
            linked_facts=[(linked_entity, [linked_fact])],
        )
        call_args = client.complete.call_args
        messages = call_args[0][0] if call_args[0] else call_args[1]["messages"]
        user_content = " ".join(m["content"] for m in messages if m["role"] == "user")
        assert "Aldara is an ancient city." in user_content
        assert result.prose == "Theron founded Aldara."
