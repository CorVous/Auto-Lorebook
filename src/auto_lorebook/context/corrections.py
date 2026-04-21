"""Reader for .transcription-corrections.yaml."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypedDict, cast

import yaml

from auto_lorebook.schema import check_schema_version

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)


class Correction(TypedDict):
    """Single transcription correction entry."""

    from_: str  # key 'from' in YAML
    to: str
    first_seen_in: str | None
    also_seen_in: list[str]
    promoted_at: str | None
    notes: str | None


def read_corrections(path: Path) -> list[Correction]:
    """Read .transcription-corrections.yaml, returning [] on missing/empty file.

    :param path: path to corrections file
    :raises SchemaVersionError: version too new
    """
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not raw:
        return []
    data = cast("dict[str, object]", raw)
    if "schema_version" not in data:
        _logger.warning("no schema_version in %s; assuming 1", path)
    else:
        check_schema_version(data, str(path))
    entries_raw = data.get("corrections")
    if not isinstance(entries_raw, list):
        return []
    result: list[Correction] = []
    for item in entries_raw:
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, object]", item)
        also_raw = entry.get("also_seen_in")
        also = [str(x) for x in also_raw] if isinstance(also_raw, list) else []
        fseen = entry.get("first_seen_in")
        prom = entry.get("promoted_at")
        notes = entry.get("notes")
        result.append(
            Correction(
                from_=str(entry.get("from", "")),
                to=str(entry.get("to", "")),
                first_seen_in=str(fseen) if fseen is not None else None,
                also_seen_in=also,
                promoted_at=str(prom) if prom is not None else None,
                notes=str(notes) if notes is not None else None,
            )
        )
    return result
