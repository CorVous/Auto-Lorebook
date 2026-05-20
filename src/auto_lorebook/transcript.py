"""Load a source's transcript for LLM input.

SRT cues are flattened to `[h:mm:ss] text` lines. Plain-text sources are
returned verbatim. `.transcription-corrections.yaml` literal
substitutions are applied per-cue (or per-string for plain text) before
returning, so `cue.text` and `text_for_llm` agree exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from auto_lorebook.info_yaml import transcript_filename_for
from auto_lorebook.srt import Cue
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
    """Flattened transcript + duration for a source.

    `cues` is populated for SRT sources and `None` for plain text. When
    populated, every `cue.text` is a literal substring of `text_for_llm`.
    """

    text_for_llm: str
    total_duration: float
    cues: tuple[Cue, ...] | None = None


def apply_corrections(text: str, corrections: Corrections) -> str:
    """Apply every correction's `wrong` → `right` substitution literally."""
    out = text
    for c in corrections.corrections:
        out = out.replace(c.wrong, c.right)
    return out


def _correct_cue(cue: Cue, corrections: Corrections) -> Cue:
    return Cue(
        index=cue.index,
        start=cue.start,
        end=cue.end,
        text=apply_corrections(cue.text, corrections),
    )


def load(
    wiki_repo: Path,
    info: Info,
    corrections: Corrections,
) -> LoadedTranscript:
    """Read the stored transcript for `info` under `wiki_repo`."""
    fname = transcript_filename_for(info.source_type)
    path = wiki_repo / "sources" / info.source_id / fname
    if not path.exists():
        msg = f"transcript not found: {path}"
        raise TranscriptError(msg)

    raw = path.read_text(encoding="utf-8")
    if info.source_type in {"srt", "youtube"} or fname.endswith(".srt"):
        cues = tuple(_correct_cue(c, corrections) for c in parse_srt(raw))
        text = _render_cues(cues)
        duration = cues[-1].end if cues else 0.0
        if info.duration_seconds is not None:
            duration = float(info.duration_seconds)
        return LoadedTranscript(text_for_llm=text, total_duration=duration, cues=cues)

    corrected = apply_corrections(raw, corrections)
    duration = float(info.duration_seconds or 0)
    return LoadedTranscript(text_for_llm=corrected, total_duration=duration, cues=None)


def transcript_window(
    transcript: LoadedTranscript, start: float, end: float
) -> tuple[str, list[Cue]]:
    """Return rendered `[h:mm:ss] text` lines and cues whose start ∈ `[start, end)`.

    :raises TranscriptError: when transcript has no cues (plain text)
    """
    if transcript.cues is None:
        msg = "transcript has no cues; window requires SRT-derived transcript"
        raise TranscriptError(msg)
    kept = [c for c in transcript.cues if start <= c.start < end]
    return _render_cues(tuple(kept)), kept


def _render_cues(cues: tuple[Cue, ...]) -> str:
    if not cues:
        return ""
    return "\n".join(f"[{format_timestamp(c.start)}] {c.text}" for c in cues) + "\n"
