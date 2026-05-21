"""Tests for regen_set.py — pure regeneration-set planner."""

from __future__ import annotations

from auto_lorebook.regen_set import RegenerationSet, plan_regeneration_set


def _no_links(_entity: tuple[str, str]) -> list[tuple[str, str]]:
    """Linked-entity resolver with no links."""
    return []


class TestRegenerationSet:
    def test_ordered_is_touched_then_linked(self) -> None:
        rs = RegenerationSet(
            touched=(("characters", "theron"),),
            linked=(("locations", "aldara"),),
        )
        assert rs.ordered == (("characters", "theron"), ("locations", "aldara"))

    def test_ordered_touched_only(self) -> None:
        rs = RegenerationSet(touched=(("characters", "theron"),), linked=())
        assert rs.ordered == (("characters", "theron"),)

    def test_ordered_linked_only(self) -> None:
        rs = RegenerationSet(touched=(), linked=(("locations", "aldara"),))
        assert rs.ordered == (("locations", "aldara"),)


class TestPlanRegenerationSet:
    def test_touched_only_no_links(self) -> None:
        rs = plan_regeneration_set([("characters", "theron")], _no_links)
        assert rs.touched == (("characters", "theron"),)
        assert rs.linked == ()

    def test_touched_first_in_ordered(self) -> None:
        def links(e: tuple[str, str]) -> list[tuple[str, str]]:
            if e == ("characters", "theron"):
                return [("locations", "aldara")]
            return []

        rs = plan_regeneration_set([("characters", "theron")], links)
        assert rs.ordered[0] == ("characters", "theron")
        assert rs.ordered[1] == ("locations", "aldara")

    def test_dedup_touched_preserves_order(self) -> None:
        rs = plan_regeneration_set(
            [("characters", "theron"), ("characters", "theron")],
            _no_links,
        )
        assert rs.touched == (("characters", "theron"),)
        assert len(rs.touched) == 1

    def test_linked_appended_deduped(self) -> None:
        # two touched entities both link to aldara → appears once in linked
        def links(_e: tuple[str, str]) -> list[tuple[str, str]]:
            return [("locations", "aldara")]

        rs = plan_regeneration_set(
            [("characters", "theron"), ("factions", "guild")],
            links,
        )
        aldara_count = rs.linked.count(("locations", "aldara"))
        assert aldara_count == 1

    def test_entity_both_touched_and_linked(self) -> None:
        # aldara is touched; theron links to aldara → aldara stays in touched only
        def links(e: tuple[str, str]) -> list[tuple[str, str]]:
            if e == ("characters", "theron"):
                return [("locations", "aldara")]
            return []

        rs = plan_regeneration_set(
            [("characters", "theron"), ("locations", "aldara")],
            links,
        )
        assert ("locations", "aldara") in rs.touched
        assert ("locations", "aldara") not in rs.linked

    def test_mutual_link_no_infinite_loop(self) -> None:
        # theron ↔ aldara mutual link; both are touched
        def links(e: tuple[str, str]) -> list[tuple[str, str]]:
            if e == ("characters", "theron"):
                return [("locations", "aldara")]
            if e == ("locations", "aldara"):
                return [("characters", "theron")]
            return []

        rs = plan_regeneration_set(
            [("characters", "theron"), ("locations", "aldara")],
            links,
        )
        # both in touched; linked must be empty (each other already in touched)
        assert rs.linked == ()

    def test_one_hop_non_transitive(self) -> None:
        # theron → aldara → guild; guild must NOT appear (not reachable in one hop)
        call_count: dict[tuple[str, str], int] = {}

        def links(e: tuple[str, str]) -> list[tuple[str, str]]:
            call_count[e] = call_count.get(e, 0) + 1
            if e == ("characters", "theron"):
                return [("locations", "aldara")]
            if e == ("locations", "aldara"):
                return [("factions", "guild")]
            return []

        rs = plan_regeneration_set([("characters", "theron")], links)
        # aldara is linked, but linked_of not called on it
        assert ("factions", "guild") not in rs.linked
        assert ("locations", "aldara") in rs.linked
        assert ("locations", "aldara") not in call_count

    def test_linked_sorted_by_category_slug(self) -> None:
        def links(_e: tuple[str, str]) -> list[tuple[str, str]]:
            return [("locations", "aldara"), ("factions", "guild")]

        rs = plan_regeneration_set([("characters", "theron")], links)
        assert rs.linked == (("factions", "guild"), ("locations", "aldara"))

    def test_empty_touched(self) -> None:
        rs = plan_regeneration_set([], _no_links)
        assert rs.touched == ()
        assert rs.linked == ()
        assert rs.ordered == ()
