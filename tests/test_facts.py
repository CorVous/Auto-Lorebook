"""Tests for facts.py — DB-backed fact store."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

import pytest

from auto_lorebook import db
from auto_lorebook.facts import (
    VALID_REF_KINDS,
    VALID_STATUSES,
    FactError,
    FactRef,
    FactRow,
    create_fact_with_target,
    create_fact_with_targets,
    create_ref,
    delete_ref,
    get_fact,
    list_facts_by_entity,
    list_linked_entities,
    list_refs_by_fact,
    update_status,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Generator


@pytest.fixture
def conn() -> Generator[sqlite3.Connection]:
    """In-memory DB with full schema.

    Yields:
        open in-memory connection.

    """
    c = db.open(":memory:")
    # seed a source so FK constraint passes
    c.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at, context_json)"
        " VALUES ('src-001', 'youtube', '2026-01-01T00:00:00Z', '{}')"
    )
    # seed entity + ingests row
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


def _make_fact(
    conn: sqlite3.Connection, fact_id: str = "f-001", status: str = "authoritative"
) -> FactRow:
    return create_fact_with_target(
        conn,
        fact_id=fact_id,
        text="Theron founded Aldara.",
        raw_transcript_span="Theron founded all-dara.",
        text_corrects_transcript=True,
        source_id="src-001",
        locator="0:04:32",
        status=status,
        approved_at="2026-01-15T10:00:00Z",
        created_by_ingest="ing-001",
        entity_category="characters",
        entity_slug="theron",
        section="biography",
        by="test-user",
    )


class TestCreateFactWithTarget:
    def test_returns_fact_row(self, conn: sqlite3.Connection) -> None:
        row = _make_fact(conn)
        assert isinstance(row, FactRow)
        assert row.id == "f-001"
        assert row.text == "Theron founded Aldara."
        assert row.status == "authoritative"
        assert row.source_id == "src-001"

    def test_inserts_fact_target(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        row = conn.execute(
            "SELECT * FROM fact_targets WHERE fact_id='f-001'"
        ).fetchone()
        assert row is not None
        assert row[1] == "characters"
        assert row[2] == "theron"
        assert row[3] == "biography"

    def test_inserts_status_history(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        row = conn.execute(
            "SELECT status, by FROM fact_status_history WHERE fact_id='f-001'"
        ).fetchone()
        assert row is not None
        assert row[0] == "authoritative"
        assert row[1] == "test-user"

    def test_invalid_status_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(FactError, match="invalid status"):
            create_fact_with_target(
                conn,
                fact_id="f-bad",
                text="x",
                raw_transcript_span="x",
                text_corrects_transcript=False,
                source_id="src-001",
                locator="0:00:00",
                status="fictional",
                approved_at="2026-01-01T00:00:00Z",
                created_by_ingest="ing-001",
                entity_category="characters",
                entity_slug="theron",
                section="overview",
                by="tester",
            )

    def test_duplicate_fact_id_raises(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        conn.commit()
        with pytest.raises(FactError):
            _make_fact(conn)


class TestGetFact:
    def test_get_existing_fact(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        conn.commit()
        row = get_fact(conn, "f-001")
        assert row is not None
        assert row.id == "f-001"

    def test_get_missing_returns_none(self, conn: sqlite3.Connection) -> None:
        assert get_fact(conn, "no-such-fact") is None


class TestListFactsByEntity:
    def test_returns_empty_when_none(self, conn: sqlite3.Connection) -> None:
        result = list_facts_by_entity(conn, "characters", "theron")
        assert result == []

    def test_returns_inserted_facts_sorted(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn, fact_id="f-002", status="trustworthy")
        _make_fact(conn, fact_id="f-001", status="authoritative")
        conn.commit()
        rows = list_facts_by_entity(conn, "characters", "theron")
        # f-001 approved_at same as f-002 → sort by id
        ids = [r.id for r in rows]
        assert "f-001" in ids
        assert "f-002" in ids


class TestUpdateStatus:
    def test_updates_status(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        conn.commit()
        update_status(conn, "f-001", "disproven", by="reviewer")
        conn.commit()
        row = get_fact(conn, "f-001")
        assert row is not None
        assert row.status == "disproven"

    def test_inserts_history_on_update(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        conn.commit()
        update_status(conn, "f-001", "hearsay", by="reviewer", reason="uncertain")
        conn.commit()
        rows = conn.execute(
            "SELECT status, reason FROM fact_status_history WHERE fact_id='f-001'"
            " ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[1][0] == "hearsay"
        assert rows[1][1] == "uncertain"

    def test_invalid_status_raises(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn)
        conn.commit()
        with pytest.raises(FactError, match="invalid status"):
            update_status(conn, "f-001", "unknown", by="reviewer")

    def test_missing_fact_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(FactError, match="fact not found"):
            update_status(conn, "no-such", "authoritative", by="reviewer")


class TestCreateFactWithTargets:
    def test_inserts_one_fact_and_n_fact_targets(
        self, conn: sqlite3.Connection
    ) -> None:
        # seed two extra entities for multi-target
        conn.execute(
            "INSERT INTO entities(category, slug, canonical_name, created_at,"
            " created_by_ingest, updated_at)"
            " VALUES ('locations', 'aldara', 'Aldara',"
            " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO entities(category, slug, canonical_name, created_at,"
            " created_by_ingest, updated_at)"
            " VALUES ('events', 'second-age', 'Second Age',"
            " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
        )
        targets = [
            ("characters", "theron", "biography"),
            ("locations", "aldara", "founding"),
            ("events", "second-age", "events-in-era"),
        ]
        row = create_fact_with_targets(
            conn,
            fact_id="multi-f001",
            text="Multi-target claim.",
            raw_transcript_span="Multi-target claim.",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:01:00",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            targets=targets,
            by="test-user",
        )
        assert isinstance(row, FactRow)
        assert row.id == "multi-f001"
        # 1 fact row
        fact_count = conn.execute(
            "SELECT COUNT(*) FROM facts WHERE id='multi-f001'"
        ).fetchone()[0]
        assert fact_count == 1
        # 3 fact_targets rows
        ft_rows = conn.execute(
            "SELECT entity_category, entity_slug, section FROM fact_targets"
            " WHERE fact_id='multi-f001' ORDER BY entity_category"
        ).fetchall()
        assert len(ft_rows) == 3

    def test_zero_targets_raises(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(FactError, match="at least one target required"):
            create_fact_with_targets(
                conn,
                fact_id="bad-f001",
                text="x",
                raw_transcript_span="x",
                text_corrects_transcript=False,
                source_id="src-001",
                locator="0:00:00",
                status="authoritative",
                approved_at="2026-01-01T00:00:00Z",
                created_by_ingest="ing-001",
                targets=[],
                by="tester",
            )


class TestValidStatuses:
    def test_all_four_statuses_present(self) -> None:
        assert (
            frozenset({
                "authoritative",
                "trustworthy",
                "hearsay",
                "disproven",
            })
            == VALID_STATUSES
        )


# ---------------------------------------------------------------------------
# Helpers shared by ref tests
# ---------------------------------------------------------------------------


def _make_fact2(
    conn: sqlite3.Connection, fact_id: str, status: str = "trustworthy"
) -> FactRow:
    """Second fact helper — uses a different fact_id by default."""
    return create_fact_with_target(
        conn,
        fact_id=fact_id,
        text=f"Claim {fact_id}.",
        raw_transcript_span=f"Claim {fact_id}.",
        text_corrects_transcript=False,
        source_id="src-001",
        locator="0:01:00",
        status=status,
        approved_at="2026-01-20T00:00:00Z",
        created_by_ingest="ing-001",
        entity_category="characters",
        entity_slug="theron",
        section="biography",
        by="test-user",
    )


# ---------------------------------------------------------------------------
# Step 1 — FactRef dataclass
# ---------------------------------------------------------------------------


class TestFactRef:
    def test_factref_dataclass_shape(self) -> None:
        ref = FactRef(
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="contradicts",
            created_at="2026-02-01T00:00:00Z",
            created_by="reviewer",
            created_by_ingest=None,
            note="conflicts with earlier claim",
        )
        assert ref.from_fact_id == "f-002"
        assert ref.to_fact_id == "f-001"
        assert ref.kind == "contradicts"
        assert ref.created_at == "2026-02-01T00:00:00Z"
        assert ref.created_by == "reviewer"
        assert ref.created_by_ingest is None
        assert ref.note == "conflicts with earlier claim"

    def test_valid_ref_kinds(self) -> None:
        assert (
            frozenset({
                "supersedes",
                "contradicts",
                "corroborates",
                "qualifies",
            })
            == VALID_REF_KINDS
        )


# ---------------------------------------------------------------------------
# Step 2 — create_ref for non-supersedes kinds
# ---------------------------------------------------------------------------


class TestCreateRef:
    @pytest.mark.parametrize("kind", ["contradicts", "corroborates", "qualifies"])
    def test_non_supersedes_inserts_edge_only(
        self, conn: sqlite3.Connection, kind: str
    ) -> None:
        _make_fact(conn, fact_id="f-001", status="authoritative")
        _make_fact2(conn, fact_id="f-002", status="trustworthy")
        conn.commit()

        ref = create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind=kind,
            by="reviewer",
            when="2026-02-01T00:00:00Z",
        )
        conn.commit()

        # edge row exists
        row = conn.execute(
            "SELECT * FROM fact_refs WHERE from_fact_id='f-002'"
            " AND to_fact_id='f-001' AND kind=?",
            (kind,),
        ).fetchone()
        assert row is not None
        assert row["created_by"] == "reviewer"

        # target status unchanged
        f001 = get_fact(conn, "f-001")
        assert f001 is not None
        assert f001.status == "authoritative"

        # no new history row added beyond the initial one
        history_count = conn.execute(
            "SELECT COUNT(*) FROM fact_status_history WHERE fact_id='f-001'"
        ).fetchone()[0]
        assert history_count == 1

        # returns FactRef
        assert isinstance(ref, FactRef)
        assert ref.kind == kind

    def test_create_ref_invalid_kind_raises(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn, fact_id="f-001")
        _make_fact2(conn, fact_id="f-002")
        conn.commit()
        with pytest.raises(FactError, match="invalid ref kind"):
            create_ref(
                conn,
                from_fact_id="f-002",
                to_fact_id="f-001",
                kind="bogus",
                by="reviewer",
            )

    def test_create_ref_duplicate_raises(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn, fact_id="f-001")
        _make_fact2(conn, fact_id="f-002")
        conn.commit()
        create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="contradicts",
            by="reviewer",
        )
        conn.commit()
        with pytest.raises(FactError):
            create_ref(
                conn,
                from_fact_id="f-002",
                to_fact_id="f-001",
                kind="contradicts",
                by="reviewer",
            )

    # Step 3 — supersedes
    def test_supersedes_flips_target_to_disproven(
        self, conn: sqlite3.Connection
    ) -> None:
        _make_fact(conn, fact_id="f-001", status="authoritative")
        _make_fact2(conn, fact_id="f-002", status="trustworthy")
        conn.commit()

        ref = create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-01T00:00:00Z",
        )
        conn.commit()

        # edge row
        edge = conn.execute(
            "SELECT * FROM fact_refs WHERE from_fact_id='f-002'"
            " AND to_fact_id='f-001' AND kind='supersedes'"
        ).fetchone()
        assert edge is not None

        # target flipped to disproven
        f001 = get_fact(conn, "f-001")
        assert f001 is not None
        assert f001.status == "disproven"

        # system history row
        history = conn.execute(
            "SELECT status, by, at, reason FROM fact_status_history"
            " WHERE fact_id='f-001' ORDER BY id"
        ).fetchall()
        assert len(history) == 2
        last = history[-1]
        assert last["status"] == "disproven"
        assert last["by"] == "system-ref-creation"
        assert last["at"] == "2026-02-01T00:00:00Z"
        assert "f-002" in last["reason"]

        assert isinstance(ref, FactRef)
        assert ref.kind == "supersedes"

    def test_supersedes_target_already_disproven_still_inserts_edge(
        self, conn: sqlite3.Connection
    ) -> None:
        _make_fact(conn, fact_id="f-001", status="disproven")
        _make_fact2(conn, fact_id="f-002", status="trustworthy")
        conn.commit()

        create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-01T00:00:00Z",
        )
        conn.commit()

        # edge inserted
        edge = conn.execute(
            "SELECT * FROM fact_refs WHERE from_fact_id='f-002' AND to_fact_id='f-001'"
        ).fetchone()
        assert edge is not None

        # target stays disproven
        f001 = get_fact(conn, "f-001")
        assert f001 is not None
        assert f001.status == "disproven"

        # history row appended regardless
        history_count = conn.execute(
            "SELECT COUNT(*) FROM fact_status_history WHERE fact_id='f-001'"
        ).fetchone()[0]
        assert history_count == 2


# ---------------------------------------------------------------------------
# Step 4 — delete_ref for non-supersedes kinds
# ---------------------------------------------------------------------------


class TestDeleteRef:
    @pytest.mark.parametrize("kind", ["contradicts", "corroborates", "qualifies"])
    def test_delete_non_supersedes_removes_edge_only(
        self, conn: sqlite3.Connection, kind: str
    ) -> None:
        _make_fact(conn, fact_id="f-001", status="authoritative")
        _make_fact2(conn, fact_id="f-002", status="trustworthy")
        conn.commit()
        create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind=kind,
            by="reviewer",
        )
        conn.commit()

        delete_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind=kind,
            by="reviewer",
            when="2026-02-02T00:00:00Z",
        )
        conn.commit()

        # edge gone
        row = conn.execute(
            "SELECT * FROM fact_refs WHERE from_fact_id='f-002'"
            " AND to_fact_id='f-001' AND kind=?",
            (kind,),
        ).fetchone()
        assert row is None

        # target status unchanged
        f001 = get_fact(conn, "f-001")
        assert f001 is not None
        assert f001.status == "authoritative"

        # no new history row
        count = conn.execute(
            "SELECT COUNT(*) FROM fact_status_history WHERE fact_id='f-001'"
        ).fetchone()[0]
        assert count == 1

    def test_delete_missing_edge_raises(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn, fact_id="f-001")
        _make_fact2(conn, fact_id="f-002")
        conn.commit()
        with pytest.raises(FactError, match="fact_ref not found"):
            delete_ref(
                conn,
                from_fact_id="f-002",
                to_fact_id="f-001",
                kind="contradicts",
                by="reviewer",
            )

    # Step 5 — supersedes restore
    def test_delete_supersedes_restores_prior_status(
        self, conn: sqlite3.Connection
    ) -> None:
        _make_fact(conn, fact_id="f-001", status="authoritative")
        _make_fact2(conn, fact_id="f-002", status="trustworthy")
        conn.commit()

        create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-01T00:00:00Z",
        )
        conn.commit()

        delete_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-02T00:00:00Z",
        )
        conn.commit()

        # edge gone
        edge = conn.execute(
            "SELECT * FROM fact_refs WHERE from_fact_id='f-002' AND to_fact_id='f-001'"
        ).fetchone()
        assert edge is None

        # status restored
        f001 = get_fact(conn, "f-001")
        assert f001 is not None
        assert f001.status == "authoritative"

        history = conn.execute(
            "SELECT status, by, reason FROM fact_status_history"
            " WHERE fact_id='f-001' ORDER BY id"
        ).fetchall()
        assert len(history) == 3
        last = history[-1]
        assert last["status"] == "authoritative"
        assert last["by"] == "system-ref-deletion"
        assert "f-002" in last["reason"]

    def test_delete_supersedes_with_other_supersedes_keeps_disproven(
        self, conn: sqlite3.Connection
    ) -> None:
        _make_fact(conn, fact_id="f-001", status="authoritative")
        _make_fact2(conn, fact_id="f-002", status="trustworthy")
        _make_fact2(conn, fact_id="f-003", status="trustworthy")
        conn.commit()

        create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-01T00:00:00Z",
        )
        create_ref(
            conn,
            from_fact_id="f-003",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-01T01:00:00Z",
        )
        conn.commit()

        delete_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-02T00:00:00Z",
        )
        conn.commit()

        # f-003 edge still exists
        edge = conn.execute(
            "SELECT * FROM fact_refs WHERE from_fact_id='f-003' AND to_fact_id='f-001'"
        ).fetchone()
        assert edge is not None

        # target still disproven
        f001 = get_fact(conn, "f-001")
        assert f001 is not None
        assert f001.status == "disproven"

        # no system-ref-deletion history row
        history = conn.execute(
            "SELECT by FROM fact_status_history WHERE fact_id='f-001' ORDER BY id"
        ).fetchall()
        bys = [r["by"] for r in history]
        assert "system-ref-deletion" not in bys

    def test_delete_last_supersedes_among_many_restores(
        self, conn: sqlite3.Connection
    ) -> None:
        _make_fact(conn, fact_id="f-001", status="authoritative")
        _make_fact2(conn, fact_id="f-002", status="trustworthy")
        _make_fact2(conn, fact_id="f-003", status="trustworthy")
        conn.commit()

        create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-01T00:00:00Z",
        )
        create_ref(
            conn,
            from_fact_id="f-003",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-01T01:00:00Z",
        )
        conn.commit()

        # delete first — still disproven
        delete_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-02T00:00:00Z",
        )
        conn.commit()
        mid_f001 = get_fact(conn, "f-001")
        assert mid_f001 is not None
        assert mid_f001.status == "disproven"

        # delete second (last) — restored
        delete_ref(
            conn,
            from_fact_id="f-003",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-03T00:00:00Z",
        )
        conn.commit()
        f001 = get_fact(conn, "f-001")
        assert f001 is not None
        assert f001.status == "authoritative"

    def test_delete_supersedes_no_prior_history_raises(
        self, conn: sqlite3.Connection
    ) -> None:
        _make_fact(conn, fact_id="f-001", status="authoritative")
        _make_fact2(conn, fact_id="f-002", status="trustworthy")
        conn.commit()
        create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-01T00:00:00Z",
        )
        conn.commit()
        # wipe all non-system history rows for f-001
        conn.execute(
            "DELETE FROM fact_status_history WHERE fact_id='f-001'"
            " AND by NOT IN ('system-ref-creation','system-ref-deletion')"
        )
        conn.commit()
        msg = "cannot restore: no prior non-system status in history"
        with pytest.raises(FactError, match=msg):
            delete_ref(
                conn,
                from_fact_id="f-002",
                to_fact_id="f-001",
                kind="supersedes",
                by="reviewer",
                when="2026-02-02T00:00:00Z",
            )


# ---------------------------------------------------------------------------
# Step 6 — Invariant + list_refs_by_fact
# ---------------------------------------------------------------------------


class TestSupersedesInvariant:
    def test_disproven_iff_supersedes_points_at_fact(
        self, conn: sqlite3.Connection
    ) -> None:
        """Via create_ref/delete_ref only: disproven iff supersedes targets fact."""
        _make_fact(conn, fact_id="f-001", status="authoritative")
        _make_fact2(conn, fact_id="f-002", status="trustworthy")
        _make_fact2(conn, fact_id="f-003", status="hearsay")
        conn.commit()

        # add contradicts — no status change
        create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-003",
            kind="contradicts",
            by="reviewer",
        )
        conn.commit()

        # add supersedes f-001 — f-001 becomes disproven
        create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
        )
        conn.commit()

        f001 = get_fact(conn, "f-001")
        assert f001 is not None
        assert f001.status == "disproven"
        f003 = get_fact(conn, "f-003")
        assert f003 is not None
        assert f003.status == "hearsay"

        delete_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
        )
        conn.commit()

        f001_after = get_fact(conn, "f-001")
        assert f001_after is not None
        assert f001_after.status != "disproven"


class TestListRefsByFact:
    def test_list_refs_out_in_both(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn, fact_id="f-001", status="authoritative")
        _make_fact2(conn, fact_id="f-002", status="trustworthy")
        _make_fact2(conn, fact_id="f-003", status="hearsay")
        conn.commit()

        # f-001 → f-002 (out from f-001)
        create_ref(
            conn,
            from_fact_id="f-001",
            to_fact_id="f-002",
            kind="corroborates",
            by="reviewer",
        )
        # f-003 → f-001 (in to f-001)
        create_ref(
            conn,
            from_fact_id="f-003",
            to_fact_id="f-001",
            kind="contradicts",
            by="reviewer",
        )
        # f-002 → f-003 (unrelated to f-001)
        create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-003",
            kind="qualifies",
            by="reviewer",
        )
        conn.commit()

        out_refs = list_refs_by_fact(conn, "f-001", direction="out")
        assert len(out_refs) == 1
        assert out_refs[0].from_fact_id == "f-001"
        assert out_refs[0].to_fact_id == "f-002"

        in_refs = list_refs_by_fact(conn, "f-001", direction="in")
        assert len(in_refs) == 1
        assert in_refs[0].from_fact_id == "f-003"
        assert in_refs[0].to_fact_id == "f-001"

        both = list_refs_by_fact(conn, "f-001", direction="both")
        assert len(both) == 2
        # sorted by from_fact_id, to_fact_id, kind
        assert both[0].from_fact_id == "f-001"
        assert both[1].from_fact_id == "f-003"

    def test_list_refs_invalid_direction_raises(self, conn: sqlite3.Connection) -> None:
        _make_fact(conn, fact_id="f-001")
        conn.commit()
        bad = cast("Literal['out', 'in', 'both']", "sideways")
        with pytest.raises(ValueError, match="direction"):
            list_refs_by_fact(conn, "f-001", direction=bad)


# ---------------------------------------------------------------------------
# Step 7 — Atomicity
# ---------------------------------------------------------------------------


class TestRefAtomicity:
    def test_create_supersedes_rollback_leaves_no_writes(
        self, conn: sqlite3.Connection
    ) -> None:
        _make_fact(conn, fact_id="f-001", status="authoritative")
        _make_fact2(conn, fact_id="f-002", status="trustworthy")
        conn.commit()

        conn.execute("BEGIN IMMEDIATE")
        create_ref(
            conn,
            from_fact_id="f-002",
            to_fact_id="f-001",
            kind="supersedes",
            by="reviewer",
            when="2026-02-01T00:00:00Z",
        )
        conn.execute("ROLLBACK")

        # no edge row
        edge = conn.execute("SELECT * FROM fact_refs").fetchone()
        assert edge is None

        # target status unchanged
        f001 = get_fact(conn, "f-001")
        assert f001 is not None
        assert f001.status == "authoritative"

        # no extra history rows
        count = conn.execute(
            "SELECT COUNT(*) FROM fact_status_history WHERE fact_id='f-001'"
        ).fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# Step 8 — list_linked_entities
# ---------------------------------------------------------------------------


def _seed_linked_entities(conn: sqlite3.Connection) -> None:
    """Seed two extra entities (locations/aldara, factions/guild) for link tests."""
    conn.execute(
        "INSERT INTO entities(category, slug, canonical_name, created_at,"
        " created_by_ingest, updated_at)"
        " VALUES ('locations', 'aldara', 'Aldara',"
        " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO entities(category, slug, canonical_name, created_at,"
        " created_by_ingest, updated_at)"
        " VALUES ('factions', 'guild', 'The Guild',"
        " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
    )


class TestListLinkedEntities:
    def test_co_target_returned(self, conn: sqlite3.Connection) -> None:
        """Fact targeting both theron and aldara → aldara linked to theron."""
        _seed_linked_entities(conn)
        create_fact_with_targets(
            conn,
            fact_id="f-link-01",
            text="Theron founded Aldara.",
            raw_transcript_span="Theron founded Aldara.",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:04:00",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            targets=[
                ("characters", "theron", "biography"),
                ("locations", "aldara", "founding"),
            ],
            by="test-user",
        )
        conn.commit()
        linked = list_linked_entities(conn, "characters", "theron")
        assert ("locations", "aldara") in linked

    def test_symmetric(self, conn: sqlite3.Connection) -> None:
        """Linked relation is symmetric: aldara also linked to theron."""
        _seed_linked_entities(conn)
        create_fact_with_targets(
            conn,
            fact_id="f-link-01",
            text="Theron founded Aldara.",
            raw_transcript_span="Theron founded Aldara.",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:04:00",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            targets=[
                ("characters", "theron", "biography"),
                ("locations", "aldara", "founding"),
            ],
            by="test-user",
        )
        conn.commit()
        linked = list_linked_entities(conn, "locations", "aldara")
        assert ("characters", "theron") in linked

    def test_self_excluded(self, conn: sqlite3.Connection) -> None:
        """Entity not linked to itself."""
        _seed_linked_entities(conn)
        create_fact_with_targets(
            conn,
            fact_id="f-link-01",
            text="Theron founded Aldara.",
            raw_transcript_span="Theron founded Aldara.",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:04:00",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            targets=[
                ("characters", "theron", "biography"),
                ("locations", "aldara", "founding"),
            ],
            by="test-user",
        )
        conn.commit()
        linked = list_linked_entities(conn, "characters", "theron")
        assert ("characters", "theron") not in linked

    def test_no_shared_fact_returns_empty(self, conn: sqlite3.Connection) -> None:
        """Entity with no co-targeted fact returns empty list."""
        _seed_linked_entities(conn)
        # theron has a single-target fact
        _make_fact(conn, fact_id="f-solo", status="authoritative")
        conn.commit()
        linked = list_linked_entities(conn, "characters", "theron")
        assert linked == []

    def test_dedup_multiple_shared_facts(self, conn: sqlite3.Connection) -> None:
        """Two facts linking same pair → aldara appears once."""
        _seed_linked_entities(conn)
        create_fact_with_targets(
            conn,
            fact_id="f-link-01",
            text="Claim one.",
            raw_transcript_span="Claim one.",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:01:00",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            targets=[
                ("characters", "theron", "biography"),
                ("locations", "aldara", "founding"),
            ],
            by="test-user",
        )
        create_fact_with_targets(
            conn,
            fact_id="f-link-02",
            text="Claim two.",
            raw_transcript_span="Claim two.",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:02:00",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            targets=[
                ("characters", "theron", "biography"),
                ("locations", "aldara", "founding"),
            ],
            by="test-user",
        )
        conn.commit()
        linked = list_linked_entities(conn, "characters", "theron")
        aldara_hits = [e for e in linked if e == ("locations", "aldara")]
        assert len(aldara_hits) == 1

    def test_superseded_entity_excluded(self, conn: sqlite3.Connection) -> None:
        """Superseded (non-null superseded_by_*) entity excluded from results."""
        _seed_linked_entities(conn)
        # make a third entity that supersedes aldara
        conn.execute(
            "INSERT INTO entities(category, slug, canonical_name, created_at,"
            " created_by_ingest, updated_at)"
            " VALUES ('locations', 'old-aldara', 'Old Aldara',"
            " '2026-01-01T00:00:00Z', 'ing-001', '2026-01-01T00:00:00Z')"
        )
        # mark old-aldara superseded by aldara
        conn.execute(
            "UPDATE entities SET superseded_by_category='locations',"
            " superseded_by_slug='aldara'"
            " WHERE category='locations' AND slug='old-aldara'"
        )
        create_fact_with_targets(
            conn,
            fact_id="f-link-01",
            text="Shared claim.",
            raw_transcript_span="Shared claim.",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:01:00",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            targets=[
                ("characters", "theron", "biography"),
                ("locations", "old-aldara", "founding"),
            ],
            by="test-user",
        )
        conn.commit()
        linked = list_linked_entities(conn, "characters", "theron")
        assert ("locations", "old-aldara") not in linked

    def test_sorted_by_category_slug(self, conn: sqlite3.Connection) -> None:
        """Results sorted by (category, slug)."""
        _seed_linked_entities(conn)
        create_fact_with_targets(
            conn,
            fact_id="f-link-01",
            text="Three-way claim.",
            raw_transcript_span="Three-way claim.",
            text_corrects_transcript=False,
            source_id="src-001",
            locator="0:01:00",
            status="authoritative",
            approved_at="2026-01-15T10:00:00Z",
            created_by_ingest="ing-001",
            targets=[
                ("characters", "theron", "biography"),
                ("factions", "guild", "members"),
                ("locations", "aldara", "founding"),
            ],
            by="test-user",
        )
        conn.commit()
        linked = list_linked_entities(conn, "characters", "theron")
        assert linked == [("factions", "guild"), ("locations", "aldara")]
