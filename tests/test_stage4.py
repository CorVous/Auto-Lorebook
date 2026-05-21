"""Tests for stage4.py — LLM-prose entity summarizer."""

from __future__ import annotations

import logging
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
    _resolve_crossref_markers,
    _resolve_entity_links,
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


# ---------------------------------------------------------------------------
# TestPerFactAnchors — fact.id-derived stable anchors
# ---------------------------------------------------------------------------


class TestPerFactAnchors:
    def test_anchor_derived_from_fact_id(self) -> None:
        """Footnote label uses fact.id, not a counter."""
        entity = _make_entity_row()
        fact = _make_fact_row(fact_id="f-001")
        result = render_entity_page(
            entity=entity,
            aliases=[],
            facts=[fact],
            prose="Some prose.",
            conn=None,
        )
        assert "[^f-001]:" in result

    def test_anchor_stable_across_fact_set_change(self) -> None:
        """Adding a second fact does not change the anchor of the first."""
        entity = _make_entity_row()
        fact1 = _make_fact_row(fact_id="f-001", text="First fact.")
        fact2 = _make_fact_row(
            fact_id="f-002", text="Second fact.", source_id="src-001"
        )

        result_one = render_entity_page(
            entity=entity,
            aliases=[],
            facts=[fact1],
            prose="Some prose.",
            conn=None,
        )
        result_two = render_entity_page(
            entity=entity,
            aliases=[],
            facts=[fact1, fact2],
            prose="Some prose.",
            conn=None,
        )
        # f-001 anchor must be present in both; not changed by adding f-002
        assert "[^f-001]:" in result_one
        assert "[^f-001]:" in result_two


# ---------------------------------------------------------------------------
# TestEntityMarkerResolution — [[category/slug]] → markdown link
# ---------------------------------------------------------------------------


class TestEntityMarkerResolution:
    def _make_linked_entity(self, name: str, category: str, slug: str) -> EntityRow:
        return _make_entity_row(name, category, slug)

    def test_marker_resolves_to_link(self) -> None:
        """[[characters/aldara]] → [Aldara](../characters/aldara.md) (cross-cat)."""
        entity = self._make_linked_entity("Aldara", "characters", "aldara")
        lookup = {("characters", "aldara"): entity}
        prose = "See [[characters/aldara]] for details."
        result = _resolve_entity_links(prose, lookup, from_category="locations")
        assert "[Aldara](../characters/aldara.md)" in result
        assert "[[characters/aldara]]" not in result

    def test_same_category_bare_path(self) -> None:
        """Same category → relative path without parent component."""
        entity = self._make_linked_entity("Aldara", "locations", "aldara")
        lookup = {("locations", "aldara"): entity}
        prose = "See [[locations/aldara]] here."
        result = _resolve_entity_links(prose, lookup, from_category="locations")
        assert "[Aldara](aldara.md)" in result

    def test_unresolvable_marker_becomes_plain_text(self) -> None:
        """Unknown marker degrades to raw 'category/slug' text."""
        lookup: dict[tuple[str, str], EntityRow] = {}
        prose = "See [[mystery/unknown]] here."
        result = _resolve_entity_links(prose, lookup, from_category="characters")
        assert "[[mystery/unknown]]" not in result
        assert "mystery/unknown" in result

    def test_unresolvable_marker_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unresolvable marker emits a logged warning."""
        lookup: dict[tuple[str, str], EntityRow] = {}
        prose = "See [[mystery/unknown]] here."
        with caplog.at_level(logging.WARNING, logger="auto_lorebook.stage4"):
            _resolve_entity_links(prose, lookup, from_category="characters")
        assert any("mystery/unknown" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# TestCrossReferenceFootnotes — [[fact:<id>]] → footnote + crossref
# ---------------------------------------------------------------------------


class TestCrossReferenceFootnotes:
    def _make_linked(
        self, name: str = "Aldara", cat: str = "locations", slug: str = "aldara"
    ) -> EntityRow:
        return _make_entity_row(name, cat, slug)

    def test_crossref_marker_emits_footnote(self) -> None:
        """[[fact:f-n01]] replaced with [^f-n01] ref; footnote def added."""
        linked_ent = self._make_linked()
        linked_fact = _make_fact_row(
            fact_id="f-n01", text="Aldara was built in the Second Age."
        )
        linked_fact_index = {"f-n01": (linked_ent, linked_fact)}
        prose = "Theron lived near [[fact:f-n01]]."
        new_prose, footnote_defs = _resolve_crossref_markers(
            prose, linked_fact_index, from_category="characters"
        )
        assert "[^f-n01]" in new_prose
        assert "[[fact:f-n01]]" not in new_prose
        assert any("f-n01" in d for d in footnote_defs)

    def test_crossref_footnote_quotes_linked_fact(self) -> None:
        """Footnote def includes the linked fact's text as a quote."""
        linked_ent = self._make_linked()
        linked_fact = _make_fact_row(
            fact_id="f-n01", text="Aldara was built in the Second Age."
        )
        linked_fact_index = {"f-n01": (linked_ent, linked_fact)}
        prose = "Background: [[fact:f-n01]]."
        _, footnote_defs = _resolve_crossref_markers(
            prose, linked_fact_index, from_category="characters"
        )
        combined = "\n".join(footnote_defs)
        assert "Aldara was built in the Second Age." in combined

    def test_crossref_footnote_links_to_entity_page(self) -> None:
        """Footnote def contains link to linked entity's page anchored at fact."""
        linked_ent = self._make_linked(cat="locations", slug="aldara")
        linked_fact = _make_fact_row(fact_id="f-n01", text="Some claim.")
        linked_fact_index = {"f-n01": (linked_ent, linked_fact)}
        prose = "See [[fact:f-n01]]."
        _, footnote_defs = _resolve_crossref_markers(
            prose, linked_fact_index, from_category="characters"
        )
        combined = "\n".join(footnote_defs)
        # link to ../locations/aldara.md#fn:f-n01
        assert "locations/aldara.md" in combined
        assert "#fn:f-n01" in combined

    def test_unresolvable_crossref_degrades_to_plain_text(self) -> None:
        """[[fact:unknown]] → plain text (marker removed), no exception."""
        linked_fact_index: dict[str, tuple[EntityRow, FactRow]] = {}
        prose = "See [[fact:unknown]]."
        new_prose, footnote_defs = _resolve_crossref_markers(
            prose, linked_fact_index, from_category="characters"
        )
        assert "[[fact:unknown]]" not in new_prose
        assert not footnote_defs

    def test_unresolvable_crossref_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Unresolvable [[fact:unknown]] emits a logged warning."""
        linked_fact_index: dict[str, tuple[EntityRow, FactRow]] = {}
        prose = "See [[fact:unknown]]."
        with caplog.at_level(logging.WARNING, logger="auto_lorebook.stage4"):
            _resolve_crossref_markers(
                prose, linked_fact_index, from_category="characters"
            )
        assert any("unknown" in r.message for r in caplog.records)

    def test_duplicate_crossref_marker_single_footnote_def(self) -> None:
        """Same [[fact:f-n01]] twice → exactly one footnote def."""
        linked_ent = self._make_linked()
        linked_fact = _make_fact_row(fact_id="f-n01", text="Aldara was built.")
        linked_fact_index = {"f-n01": (linked_ent, linked_fact)}
        prose = "First [[fact:f-n01]] and again [[fact:f-n01]]."
        _, footnote_defs = _resolve_crossref_markers(
            prose, linked_fact_index, from_category="characters"
        )
        assert footnote_defs.count(footnote_defs[0]) == 1
        assert len([d for d in footnote_defs if "f-n01" in d]) == 1

    def test_crossref_only_for_cited_linked_facts(self) -> None:
        """Only [[fact:…]] markers that appear in prose generate footnotes."""
        linked_ent = self._make_linked()
        fact_cited = _make_fact_row(fact_id="f-n01", text="Cited fact.")
        fact_uncited = _make_fact_row(fact_id="f-n02", text="Uncited fact.")
        linked_fact_index = {
            "f-n01": (linked_ent, fact_cited),
            "f-n02": (linked_ent, fact_uncited),
        }
        prose = "Only [[fact:f-n01]] is mentioned."
        _, footnote_defs = _resolve_crossref_markers(
            prose, linked_fact_index, from_category="characters"
        )
        combined = "\n".join(footnote_defs)
        assert "f-n01" in combined
        assert "f-n02" not in combined

    def test_render_entity_page_with_crossref(self) -> None:
        """render_entity_page passes crossref footnotes through to output."""
        entity = _make_entity_row()
        own_fact = _make_fact_row(fact_id="f-001", text="Theron ruled.")
        linked_ent = _make_entity_row("Aldara", "locations", "aldara")
        linked_fact = _make_fact_row(fact_id="f-n01", text="Aldara was founded here.")
        entity_lookup = {("locations", "aldara"): linked_ent}
        linked_facts = [(linked_ent, [linked_fact])]
        prose = "Theron ruled. See [[fact:f-n01]]."
        result = render_entity_page(
            entity=entity,
            aliases=[],
            facts=[own_fact],
            prose=prose,
            conn=None,
            entity_lookup=entity_lookup,
            linked_facts=linked_facts,
        )
        assert "[^f-n01]" in result
        assert "Aldara was founded here." in result
