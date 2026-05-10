"""Tests for wiki_registry.py — pure data layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_lorebook.wiki_registry import (
    WikiEntry,
    WikiRegistry,
    WikiRegistryError,
)

# ---------------------------------------------------------------------------
# WikiEntry
# ---------------------------------------------------------------------------


def test_wiki_entry_holds_nickname_and_path() -> None:
    e = WikiEntry("home", Path("/a"))
    assert e.nickname == "home"
    assert e.path == Path("/a")


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------


def test_registry_roundtrip_to_dict_list() -> None:
    entries = [
        WikiEntry("alpha", Path("/a")),
        WikiEntry("beta", Path("/b")),
    ]
    reg = WikiRegistry(entries=entries, active="alpha")
    serialized = reg.to_list()
    assert serialized == [
        {"nickname": "alpha", "path": str(Path("/a"))},
        {"nickname": "beta", "path": str(Path("/b"))},
    ]
    reg2 = WikiRegistry.from_list(serialized, active="alpha")
    assert reg2.entries == entries
    assert reg2.active == "alpha"


def test_from_list_preserves_order() -> None:
    raw = [
        {"nickname": "z", "path": "/z"},
        {"nickname": "a", "path": "/a"},
    ]
    reg = WikiRegistry.from_list(raw, active="z")
    assert [e.nickname for e in reg.entries] == ["z", "a"]


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def test_add_appends_entry() -> None:
    reg = WikiRegistry(entries=[], active=None)
    reg.add(WikiEntry("home", Path("/wiki")))
    assert len(reg.entries) == 1
    assert reg.entries[0].nickname == "home"


def test_add_rejects_duplicate_nickname() -> None:
    reg = WikiRegistry(entries=[WikiEntry("home", Path("/a"))], active="home")
    with pytest.raises(WikiRegistryError, match="home"):
        reg.add(WikiEntry("home", Path("/b")))


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def test_remove_drops_entry() -> None:
    reg = WikiRegistry(
        entries=[WikiEntry("home", Path("/a")), WikiEntry("other", Path("/b"))],
        active="other",
    )
    reg.remove("home")
    assert len(reg.entries) == 1
    assert reg.entries[0].nickname == "other"


def test_remove_refuses_active_entry() -> None:
    reg = WikiRegistry(entries=[WikiEntry("home", Path("/a"))], active="home")
    with pytest.raises(WikiRegistryError, match="home"):
        reg.remove("home")


def test_remove_unknown_nickname_raises() -> None:
    reg = WikiRegistry(entries=[], active=None)
    with pytest.raises(WikiRegistryError, match="nope"):
        reg.remove("nope")


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


def test_rename_updates_entry_and_active_pointer() -> None:
    reg = WikiRegistry(
        entries=[WikiEntry("old", Path("/a")), WikiEntry("other", Path("/b"))],
        active="old",
    )
    reg.rename("old", "new")
    assert reg.entries[0].nickname == "new"
    assert reg.active == "new"


def test_rename_updates_active_when_not_matched() -> None:
    reg = WikiRegistry(
        entries=[WikiEntry("alpha", Path("/a")), WikiEntry("beta", Path("/b"))],
        active="beta",
    )
    reg.rename("alpha", "gamma")
    assert reg.entries[0].nickname == "gamma"
    assert reg.active == "beta"  # unchanged


def test_rename_rejects_duplicate_target() -> None:
    reg = WikiRegistry(
        entries=[WikiEntry("a", Path("/a")), WikiEntry("b", Path("/b"))],
        active="a",
    )
    with pytest.raises(WikiRegistryError, match="b"):
        reg.rename("a", "b")


def test_rename_unknown_source_raises() -> None:
    reg = WikiRegistry(entries=[], active=None)
    with pytest.raises(WikiRegistryError, match="nope"):
        reg.rename("nope", "new")


# ---------------------------------------------------------------------------
# set_active
# ---------------------------------------------------------------------------


def test_set_active_accepts_known_nickname() -> None:
    reg = WikiRegistry(
        entries=[WikiEntry("home", Path("/a")), WikiEntry("other", Path("/b"))],
        active="home",
    )
    reg.set_active("other")
    assert reg.active == "other"


def test_set_active_rejects_unknown_nickname() -> None:
    reg = WikiRegistry(entries=[WikiEntry("home", Path("/a"))], active="home")
    with pytest.raises(WikiRegistryError, match="unknown"):
        reg.set_active("nope")
