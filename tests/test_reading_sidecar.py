"""Tests for reading_sidecar.py — reading.yaml I/O."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from auto_lorebook.gap_check import GapWarning
from auto_lorebook.reading_sidecar import ReadingSidecarError, Sidecar, read, write

if TYPE_CHECKING:
    from pathlib import Path


def _full_sidecar() -> Sidecar:
    return Sidecar(
        default_speaker="DM",
        name_corrections={"Fair-on": "Theron", "Aldera": "Aldara"},
        session_date="2026-01-15",
    )


def _two_warnings() -> list[GapWarning]:
    return [
        GapWarning(
            start=2050.0,
            end=2902.0,
            segment_ids=("seg-005", "seg-006", "seg-007"),
            segment_titles=("Pizza discussion", "Break", "Rules: initiative"),
        ),
        GapWarning(
            start=5400.0,
            end=6100.0,
            segment_ids=("seg-012",),
            segment_titles=("Silence",),
        ),
    ]


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
        assert first_line == "schema_version: 2"

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
            "gap_warnings",
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


class TestGapWarnings:
    def test_round_trip_two_warnings(self, tmp_path: Path) -> None:
        warnings = _two_warnings()
        sc = Sidecar(default_speaker="DM", gap_warnings=warnings)
        p = tmp_path / "reading.yaml"
        write(sc, p)
        loaded = read(p)
        assert len(loaded.gap_warnings) == 2
        w0 = loaded.gap_warnings[0]
        assert w0.start == pytest.approx(2050.0)
        assert w0.end == pytest.approx(2902.0)
        assert w0.segment_ids == ("seg-005", "seg-006", "seg-007")
        assert w0.segment_titles == ("Pizza discussion", "Break", "Rules: initiative")
        w1 = loaded.gap_warnings[1]
        assert w1.start == pytest.approx(5400.0)
        assert w1.end == pytest.approx(6100.0)
        assert w1.segment_ids == ("seg-012",)
        assert w1.segment_titles == ("Silence",)

    def test_schema_version_is_2(self, tmp_path: Path) -> None:
        sc = Sidecar(default_speaker="DM", gap_warnings=_two_warnings())
        p = tmp_path / "reading.yaml"
        write(sc, p)
        first_line = p.read_text(encoding="utf-8").splitlines()[0]
        assert first_line == "schema_version: 2"

    def test_key_order_ends_with_gap_warnings(self, tmp_path: Path) -> None:
        sc = Sidecar(default_speaker="DM", gap_warnings=_two_warnings())
        p = tmp_path / "reading.yaml"
        write(sc, p)
        parsed = yaml.safe_load(p.read_text(encoding="utf-8"))
        assert list(parsed.keys())[-1] == "gap_warnings"

    def test_v1_back_compat_reads_empty_gap_warnings(self, tmp_path: Path) -> None:
        # v1 YAML without gap_warnings key — should read as []
        p = tmp_path / "reading.yaml"
        p.write_text(
            "schema_version: 1\ndefault_speaker: DM\n"
            "session_date: null\nname_corrections: {}\n",
            encoding="utf-8",
        )
        loaded = read(p)
        assert loaded.gap_warnings == []
