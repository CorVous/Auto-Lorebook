"""Staleness inputs block and detection for reading.md."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from auto_lorebook.llm import params_sha256

if TYPE_CHECKING:
    from collections.abc import Mapping

    from auto_lorebook.config import ModelParams

_INPUT_KEYS: tuple[str, ...] = (
    "transcript_sha256",
    "info_yaml_sha256",
    "wiki_context_sha256",
    "corrections_sha256",
    "entity_index_sha256",
    "preamble_sha256",
    "structure_sha256",
    "model",
    "model_params_sha256",
)


class StalenessError(Exception):
    """Stale artifact: recorded inputs no longer match current files."""

    def __init__(self, changed_input: str) -> None:
        self.changed_input = changed_input
        remedy = f"auto-lorebook regenerate-reading --from={changed_input}"
        super().__init__(
            f"Reading is stale: '{changed_input}' has changed. Run: {remedy}"
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_reading_inputs(
    *,
    transcript_bytes: bytes,
    info_bytes: bytes,
    wiki_bytes: bytes,
    corrections_bytes: bytes,
    entity_index: list[dict[str, object]],
    preamble: str,
    structure_bytes: bytes,
    model: str,
    params: ModelParams,
) -> dict[str, str]:
    """Build the staleness inputs block for reading.md frontmatter.

    :param transcript_bytes: raw .srt bytes
    :param info_bytes: info.yaml bytes
    :param wiki_bytes: .wiki-context.yaml bytes
    :param corrections_bytes: .transcription-corrections.yaml bytes
    :param entity_index: entity records list
    :param preamble: assembled preamble string
    :param structure_bytes: structure.yaml bytes
    :param model: LLM model string
    :param params: sampling parameters
    :return: dict of SHA-256 hashes and identity fields
    """
    entity_canon = json.dumps(entity_index, sort_keys=True, ensure_ascii=False).encode()
    return {
        "transcript_sha256": _sha256(transcript_bytes),
        "info_yaml_sha256": _sha256(info_bytes),
        "wiki_context_sha256": _sha256(wiki_bytes),
        "corrections_sha256": _sha256(corrections_bytes),
        "entity_index_sha256": _sha256(entity_canon),
        "preamble_sha256": _sha256(preamble.encode()),
        "structure_sha256": _sha256(structure_bytes),
        "model": model,
        "model_params_sha256": params_sha256(params),
    }


def check_staleness(
    recorded: Mapping[str, object],
    current: Mapping[str, object],
) -> None:
    """Compare recorded inputs to current; raise StalenessError naming the changed key.

    :param recorded: inputs block from stored artifact
    :param current: freshly computed inputs block
    :raises StalenessError: if any input key has changed
    """
    for key in _INPUT_KEYS:
        if recorded.get(key) != current.get(key):
            raise StalenessError(key)
