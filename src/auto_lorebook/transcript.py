"""Load a source's transcript for LLM input.

SRT cues are flattened to `[h:mm:ss] text` lines. Plain-text sources are
returned verbatim. `.transcription-corrections.yaml` literal
substitutions are applied before returning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook.srt import parse as parse_srt
from auto_lorebook.timestamps import format_timestamp

if TYPE_CHECKING:
    from pathlib import Path

    from auto_lorebook.corrections import Corrections
    from auto_lorebook.info_yaml import Info


class TranscriptError(ValueError):
    """Raised when a source's transcript file cannot be loaded."""


@dataclass(frozen=True)
class LoadedTranscript:
    """Flattened transcript + duration for a source."""

    text_for_llm: str
    total_duration: float


def apply_corrections(text: str, corrections: Corrections) -> str:
    """Apply every correction's `wrong` → `right` substitution literally."""
    out = text
    for c in corrections.corrections:
        out = out.replace(c.wrong, c.right)
    return out


def load(
    wiki_repo: Path,
    info: Info,
    corrections: Corrections,
) -> LoadedTranscript:
    """Read the stored transcript for `info` under `wiki_repo`."""
    fname = info.transcript_filename or _default_filename(info.source_type)
    path = wiki_repo / "sources" / info.source_id / fname
    if not path.exists():
        msg = f"transcript not found: {path}"
        raise TranscriptError(msg)

    raw = path.read_text(encoding="utf-8")
    if info.source_type in {"srt", "youtube"} or fname.endswith(".srt"):
        text, duration = _render_srt(raw)
    else:
        text = raw
        duration = float(info.duration_seconds or 0)

    if info.duration_seconds is not None:
        duration = float(info.duration_seconds)

    corrected = apply_corrections(text, corrections)
    return LoadedTranscript(text_for_llm=corrected, total_duration=duration)


def _default_filename(source_type: str) -> str:
    if source_type in {"srt", "youtube"}:
        return "transcript.en.srt"
    if source_type == "markdown":
        return "transcript.md"
    return "transcript.txt"


def _render_srt(raw: str) -> tuple[str, float]:
    cues = parse_srt(raw)
    if not cues:
        return "", 0.0
    lines = [f"[{format_timestamp(c.start)}] {c.text}" for c in cues]
    duration = cues[-1].end
    return "\n".join(lines) + "\n", duration
