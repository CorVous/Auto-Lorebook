"""Tests for summary_regen.py — mechanical Markdown rendering from DB."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from auto_lorebook import db
from auto_lorebook.facts import create_fact_with_target
from auto_lorebook.summary_regen import (
    delete_entity_summary,
    regenerate_entity,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def conn() -> Generator[sqlite3.Connection]:
    """In-memory DB with full schema.

    Yields:
        open in-memory connection.

    """
    c = db.open(":memory:")
    c.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at, context_json)"
        " VALUES ('src-001', 'youtube', '2026-01-01T00:00:00Z', '{}')"
    )
    c.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('ing-001', 'src-001', '2026-01-01T00:00:00Z', 'done')"
    )
    c.execute(
        "INSERT INTO entities(category, slug, canonical_name, created_at,"
        " created_by_ingest, updated_at)"
        " VALUES ('characters', 'theron', 'Theron',"
        " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
    )
    c.commit()
    yield c
    c.close()


def _seed_fact(
    conn: sqlite3.Connection, fact_id: str, status: str, section: str = "biography"
) -> None:
    create_fact_with_target(
        conn,
        fact_id=fact_id,
        text=f"Fact text for {fact_id}.",
        raw_transcript_span="raw span",
        text_corrects_transcript=False,
        source_id="src-001",
        locator="0:04:32",
        status=status,
        approved_at="2026-01-15T10:00:00Z",
        created_by_ingest="ing-001",
        entity_category="characters",
        entity_slug="theron",
        section=section,
        by="tester",
    )


class TestRegenerateEntity:
    def test_creates_md_file(self, conn: sqlite3.Connection, tmp_path: Path) -> None:
        _seed_fact(conn, "f-001", "authoritative")
        conn.commit()
        path = regenerate_entity(conn, tmp_path, "characters", "theron")
        assert path.exists()
        assert path == tmp_path / "characters" / "theron.md"

    def test_heading_is_canonical_name(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_fact(conn, "f-001", "authoritative")
        conn.commit()
        path = regenerate_entity(conn, tmp_path, "characters", "theron")
        text = path.read_text()
        assert text.startswith("# Theron")

    def test_authoritative_and_hearsay_sections(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_fact(conn, "f-001", "authoritative")
        _seed_fact(conn, "f-002", "hearsay")
        conn.commit()
        path = regenerate_entity(conn, tmp_path, "characters", "theron")
        text = path.read_text()
        assert "### Authoritative" in text
        assert "### Hearsay" in text
        assert "### Trustworthy" not in text

    def test_disproven_uses_strikethrough(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_fact(conn, "f-001", "disproven")
        conn.commit()
        path = regenerate_entity(conn, tmp_path, "characters", "theron")
        text = path.read_text()
        assert "~~Fact text for f-001.~~" in text

    def test_aliases_section_included(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        conn.execute(
            "INSERT INTO aliases(entity_category, entity_slug, name, name_normalized,"
            " added_by_ingest, added_at, source)"
            " VALUES ('characters', 'theron', 'King Theron', 'king theron',"
            " 'ing-001', '2026-01-01T00:00:00Z', 'stub-creation')"
        )
        _seed_fact(conn, "f-001", "authoritative")
        conn.commit()
        path = regenerate_entity(conn, tmp_path, "characters", "theron")
        text = path.read_text()
        assert "## Aliases" in text
        assert "- King Theron" in text

    def test_aliases_section_omitted_when_empty(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_fact(conn, "f-001", "authoritative")
        conn.commit()
        path = regenerate_entity(conn, tmp_path, "characters", "theron")
        text = path.read_text()
        assert "## Aliases" not in text

    def test_no_facts_produces_just_heading(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        conn.commit()
        path = regenerate_entity(conn, tmp_path, "characters", "theron")
        text = path.read_text()
        assert "# Theron" in text
        assert "## Facts" not in text

    def test_citation_includes_section(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        _seed_fact(conn, "f-001", "authoritative", section="founding")
        conn.commit()
        path = regenerate_entity(conn, tmp_path, "characters", "theron")
        text = path.read_text()
        assert "(section: founding)" in text

    def test_missing_entity_raises(
        self, conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError, match="entity not found"):
            regenerate_entity(conn, tmp_path, "characters", "no-one")


class TestDeleteEntitySummary:
    def test_deletes_existing_file(self, tmp_path: Path) -> None:
        md = tmp_path / "characters" / "theron.md"
        md.parent.mkdir(parents=True)
        md.write_text("# Theron\n")
        delete_entity_summary(tmp_path, "characters", "theron")
        assert not md.exists()

    def test_silent_if_absent(self, tmp_path: Path) -> None:
        # must not raise
        delete_entity_summary(tmp_path, "characters", "no-one")
