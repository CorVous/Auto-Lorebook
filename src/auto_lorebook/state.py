"""State directory layout for ~/.auto-lorebook/."""

from __future__ import annotations

import string
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from auto_lorebook.config import DEFAULT_CONFIG_DIR

if TYPE_CHECKING:
    from pathlib import Path

_INGEST_DATE_FMT = "%Y-%m-%d"
_ALPHA = string.ascii_lowercase  # 26 slots per day (a-z)


def get_state_dir() -> Path:
    """Return the tool state directory path (not guaranteed to exist)."""
    return DEFAULT_CONFIG_DIR


def generate_ingest_id(state_dir: Path) -> str:
    """Return a unique ingest ID for today, avoiding collisions.

    Format: ``ingest-YYYY-MM-DD-<letter>``

    :param state_dir: tool state directory (~/.auto-lorebook)
    :raises RuntimeError: if all 26 per-day slots are occupied
    """
    today = datetime.now(tz=UTC).strftime(_INGEST_DATE_FMT)
    pending_dir = state_dir / "pending"
    for letter in _ALPHA:
        candidate = f"ingest-{today}-{letter}"
        if not (pending_dir / candidate).exists():
            return candidate
    msg = f"all 26 ingest IDs for {today} are taken"
    raise RuntimeError(msg)


def create_ingest_dir(state_dir: Path, ingest_id: str) -> Path:
    """Create pending/<ingest_id>/{reading,proposals}/ dirs.

    :param state_dir: tool state directory
    :param ingest_id: ingest identifier string
    :return: path to the ingest root directory
    """
    ingest_root = state_dir / "pending" / ingest_id
    (ingest_root / "reading").mkdir(parents=True, exist_ok=True)
    (ingest_root / "proposals").mkdir(parents=True, exist_ok=True)
    return ingest_root
