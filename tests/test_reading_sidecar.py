"""Tests for reading_sidecar.py — reading.yaml I/O."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook.reading_sidecar import ReadingSidecarError, Sidecar, read, write

if TYPE_CHECKING:
    from pathlib import Path


def _full_sidecar() -> Sidecar:
    return Sidecar(
        default_speaker="DM",
        name_corrections={"Fair-on": "Theron", "Aldera": "Aldara"},
        session_date="2026-01-15",
    )


class TestRoundTripFull:
    def test_round_trip(self, tmp_path: Path) -> None:
        sc = _full_sidecar()
        p = tmp_path / "reading.yaml"
        write(sc, p)
        loaded = read(p)
        assert loaded.default_speaker == sc.default_speaker
        assert loaded.name_corrections == sc.name_corrections
        assert loaded.session_date == sc.session_date

    def test_schema_version_first(self, tmp_path: Path) -> None:
        p = tmp_path / "reading.yaml"
        write(_full_sidecar(), p)
        first_line = p.read_text(encoding="utf-8").splitlines()[0]
        assert first_line == "schema_version: 1"

    def test_key_order(self, tmp_path: Path) -> None:
        p = tmp_path / "reading.yaml"
        write(_full_sidecar(), p)
        parsed = yaml.safe_load(p.read_text(encoding="utf-8"))
        keys = list(parsed.keys())
        assert keys == [
            "schema_version",
            "default_speaker",
            "session_date",
            "name_corrections",
        ]

    def test_byte_identical_round_trip(self, tmp_path: Path) -> None:
        p = tmp_path / "reading.yaml"
        write(_full_sidecar(), p)
        first_bytes = p.read_bytes()
        loaded = read(p)
        write(loaded, p)
        assert p.read_bytes() == first_bytes


class TestEmptyCorrections:
    def test_empty_corrections(self, tmp_path: Path) -> None:
        sc = Sidecar(default_speaker="GM", name_corrections={}, session_date=None)
        p = tmp_path / "reading.yaml"
        write(sc, p)
        loaded = read(p)
        assert loaded.name_corrections == {}
        assert loaded.session_date is None


class TestNullSessionDate:
    def test_null_session_date(self, tmp_path: Path) -> None:
        sc = Sidecar(default_speaker="GM")
        p = tmp_path / "reading.yaml"
        write(sc, p)
        loaded = read(p)
        assert loaded.session_date is None


class TestMissingFileRaises:
    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ReadingSidecarError, match="not found"):
            read(tmp_path / "nope.yaml")


class TestMissingSchemaVersionRaises:
    def test_missing_schema_version(self, tmp_path: Path) -> None:
        p = tmp_path / "reading.yaml"
        p.write_text("default_speaker: DM\nname_corrections: {}\n", encoding="utf-8")
        with pytest.raises(ReadingSidecarError, match="schema_version"):
            read(p)


class TestFutureSchemaRaises:
    def test_future_schema(self, tmp_path: Path) -> None:
        p = tmp_path / "reading.yaml"
        p.write_text(
            "schema_version: 99\ndefault_speaker: DM\n"
            "session_date: null\nname_corrections: {}\n",
            encoding="utf-8",
        )
        with pytest.raises(ReadingSidecarError, match="schema_version"):
            read(p)
