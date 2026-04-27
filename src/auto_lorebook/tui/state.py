"""Pipeline state dataclass and Stage enum for the TUI process orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class Stage(Enum):
    """Ordered pipeline stages; each maps to one TUI screen."""

    INGEST = auto()
    CONTEXT = auto()
    READING_GEN = auto()
    READING_GATE = auto()
    PLAN = auto()
    EXTRACT = auto()
    REVIEW_GATE = auto()
    DONE = auto()


@dataclass
class PipelineState:
    """Runtime state threaded through the process orchestrator."""

    source_id: str
    wiki_repo_path: Path
    stage: Stage
    # URL or local path supplied on the command line (may be None on --source-id resume)
    url_or_path: str | None = None
