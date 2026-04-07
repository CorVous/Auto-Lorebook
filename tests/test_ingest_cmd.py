"""Tests for the ingest CLI command."""

from __future__ import annotations

import subprocess  # noqa: S404
import sys
import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

import auto_lorebook

_PKG = auto_lorebook.__package__ or "auto_lorebook"

# ---------------------------------------------------------------------------
# Sample SRT content
# ---------------------------------------------------------------------------

SAMPLE_SRT = textwrap.dedent("""\
    1
    00:00:01,000 --> 00:00:04,000
    The Kingdom of Aldara was founded in the Second Age.

    2
    00:00:05,000 --> 00:00:08,000
    Its capital, the city of Elden, sits atop a volcanic plateau.

    3
    00:00:09,000 --> 00:00:12,000
    The Aldaran kings ruled for three hundred years before the Sundering.
""")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_ingest(
    *args: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``auto-lorebook ingest`` with the given arguments."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", _PKG, "ingest", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# Help / registration
# ---------------------------------------------------------------------------


class TestIngestHelp:
    """The ingest subcommand is registered and has help text."""

    def test_ingest_appears_in_global_help(self) -> None:
        """'ingest' appears in the top-level help output."""
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", _PKG, "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert "ingest" in result.stdout

    def test_ingest_help_flag(self) -> None:
        """``ingest --help`` exits 0 and describes the command."""
        result = run_ingest("--help")
        assert result.returncode == 0
        assert "ingest" in result.stdout.lower()

    def test_ingest_help_shows_source_url(self) -> None:
        """``ingest --help`` documents the --source-url option."""
        result = run_ingest("--help")
        assert "--source-url" in result.stdout


# ---------------------------------------------------------------------------
# Ingest from a file
# ---------------------------------------------------------------------------


class TestIngestFile:
    """Ingest an SRT file passed as a path argument."""

    def test_ingest_srt_file_exits_zero(self, tmp_path: Path) -> None:
        """Ingesting a valid SRT file returns exit code 0."""
        srt_file = tmp_path / "test.srt"
        srt_file.write_text(SAMPLE_SRT)
        result = run_ingest(str(srt_file))
        assert result.returncode == 0

    def test_ingest_srt_reports_block_count(self, tmp_path: Path) -> None:
        """Output includes the number of subtitle blocks parsed."""
        srt_file = tmp_path / "test.srt"
        srt_file.write_text(SAMPLE_SRT)
        result = run_ingest(str(srt_file))
        assert "3" in result.stdout  # 3 blocks

    def test_ingest_srt_reports_source_type_local(
        self, tmp_path: Path
    ) -> None:
        """Without --source-url, source type is reported as local."""
        srt_file = tmp_path / "notes.srt"
        srt_file.write_text(SAMPLE_SRT)
        result = run_ingest(str(srt_file))
        assert "local" in result.stdout.lower()

    def test_nonexistent_file_exits_nonzero(self) -> None:
        """A missing file path returns a non-zero exit code."""
        result = run_ingest("/no/such/file.srt")
        assert result.returncode != 0

    def test_ingest_txt_file(self, tmp_path: Path) -> None:
        """Plain .txt files are accepted and exit 0."""
        txt_file = tmp_path / "lore.txt"
        txt_file.write_text("Some plain lore text here.")
        result = run_ingest(str(txt_file))
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Ingest from stdin
# ---------------------------------------------------------------------------


class TestIngestStdin:
    """Ingest content from stdin using ``-`` as the path."""

    def test_stdin_exits_zero(self) -> None:
        """Reading from stdin returns exit code 0."""
        result = run_ingest("-", stdin=SAMPLE_SRT)
        assert result.returncode == 0

    def test_stdin_reports_block_count(self) -> None:
        """Stdin SRT content reports the correct block count."""
        result = run_ingest("-", stdin=SAMPLE_SRT)
        assert "3" in result.stdout


# ---------------------------------------------------------------------------
# --source-url flag
# ---------------------------------------------------------------------------


class TestIngestSourceUrl:
    """Tests for the --source-url flag."""

    def test_youtube_url_reported(self, tmp_path: Path) -> None:
        """A YouTube --source-url is echoed in the output."""
        srt_file = tmp_path / "vid.srt"
        srt_file.write_text(SAMPLE_SRT)
        yt_url = "https://youtube.com/watch?v=abc123"
        result = run_ingest(str(srt_file), "--source-url", yt_url)
        assert result.returncode == 0
        assert "youtube" in result.stdout.lower()

    def test_generic_url_reported(self, tmp_path: Path) -> None:
        """A generic --source-url causes source type 'url' to be reported."""
        srt_file = tmp_path / "lore.srt"
        srt_file.write_text(SAMPLE_SRT)
        result = run_ingest(str(srt_file), "--source-url", "https://example.com/lore")
        assert result.returncode == 0
        assert "url" in result.stdout.lower()

    def test_youtube_url_with_stdin(self) -> None:
        """--source-url works when reading from stdin."""
        result = run_ingest(
            "-", "--source-url", "https://youtube.com/watch?v=xyz", stdin=SAMPLE_SRT
        )
        assert result.returncode == 0
        assert "youtube" in result.stdout.lower()

    def test_timestamp_link_shown_for_youtube(self, tmp_path: Path) -> None:
        """For YouTube sources, output includes a &t= timestamp link."""
        srt_file = tmp_path / "vid.srt"
        srt_file.write_text(SAMPLE_SRT)
        result = run_ingest(str(srt_file), "--source-url", "https://youtube.com/watch?v=abc123")
        assert "&t=" in result.stdout
