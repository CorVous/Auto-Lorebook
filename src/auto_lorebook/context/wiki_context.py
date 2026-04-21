"""Reader for .wiki-context.yaml (wiki-level context)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypedDict, cast

import yaml

from auto_lorebook.schema import check_schema_version

if TYPE_CHECKING:
    from pathlib import Path

_logger = logging.getLogger(__name__)


class WikiContext(TypedDict):
    """Parsed .wiki-context.yaml."""

    setting: str | None
    naming_conventions: list[str]
    interpretation_defaults: dict[str, str]
    recurring_speakers: list[str]


def _empty() -> WikiContext:
    return WikiContext(
        setting=None,
        naming_conventions=[],
        interpretation_defaults={},
        recurring_speakers=[],
    )


def read_wiki_context(path: Path) -> WikiContext:
    """Read .wiki-context.yaml, returning empty defaults on missing/empty file.

    :param path: path to .wiki-context.yaml
    :raises SchemaVersionError: version too new
    """
    if not path.exists():
        return _empty()
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not raw:
        return _empty()
    data = cast("dict[str, object]", raw)
    # hand-maintained file: missing schema_version defaults to 1
    if "schema_version" not in data:
        _logger.warning("no schema_version in %s; assuming 1", path)
    else:
        check_schema_version(data, str(path))
    setting_raw = data.get("setting")
    nc_raw = data.get("naming_conventions")
    id_raw = data.get("interpretation_defaults")
    rs_raw = data.get("recurring_speakers")
    naming_conventions = [str(x) for x in nc_raw] if isinstance(nc_raw, list) else []
    interpretation_defaults = (
        {str(k): str(v) for k, v in id_raw.items()} if isinstance(id_raw, dict) else {}
    )
    recurring_speakers = [str(x) for x in rs_raw] if isinstance(rs_raw, list) else []
    return WikiContext(
        setting=str(setting_raw) if setting_raw is not None else None,
        naming_conventions=naming_conventions,
        interpretation_defaults=interpretation_defaults,
        recurring_speakers=recurring_speakers,
    )
