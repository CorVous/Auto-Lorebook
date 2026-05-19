"""auto-lorebook seed-ingest subcommand.

Seeds a fresh disposable ``qa-*`` source with synthetic stage-input
fixtures, so a stage can be exercised in isolation without running the
prior stages or hitting the LLM. Pair with ``reject-ingest <sid>`` to
clean up.

Reading-pipeline state is seeded directly into DB (not as pending YAML).
Fixture YAMLs remain on disk as the seed source for human readability.
"""

from __future__ import annotations

import logging
import secrets
from importlib import resources
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
from auto_lorebook import wiki_state as wiki_state_mod
from auto_lorebook._io import atomic_write_text

if TYPE_CHECKING:
    import argparse
    from importlib.resources.abc import Traversable
    from pathlib import Path

_logger = logging.getLogger(__name__)

# placeholder substituted with the seeded source_id at copy time. Must
# not occur in any fixture file's natural content.
_SOURCE_ID_PLACEHOLDER = "__QA_SOURCE_ID__"

_FIXTURE_PACKAGE = "auto_lorebook._qa_fixtures"
_DEFAULT_FIXTURE = "tiny-aldara"
_SOURCE_ID_BYTES = 4  # 8 hex chars
_MINT_RETRY = 16

# stage → ordered list of fixture keys to seed cumulatively
_STAGES: dict[str, tuple[str, ...]] = {
    "structure": ("info", "transcript"),
    "summarize": ("info", "transcript", "structure"),
    "approve": ("info", "transcript", "structure", "bullets", "sidecar"),
    "plan": (
        "info",
        "transcript",
        "structure",
        "bullets",
        "sidecar",
        "approved_reading",
    ),
}

_NEXT_CMD: dict[str, str] = {
    "structure": "generate-reading {sid}",
    "summarize": "regenerate-reading {sid} --from=summarize",
    "approve": "approve-reading {sid} --yes",
    "plan": "replan {sid}",
}


class SeedIngestError(RuntimeError):
    """User-facing seed-ingest failure."""


def add_parser(
    subparsers: argparse._SubParsersAction,
    common_parser: argparse.ArgumentParser,
) -> argparse.ArgumentParser:
    """Register the seed-ingest subcommand."""
    parser = subparsers.add_parser(
        "seed-ingest",
        parents=[common_parser],
        help="Seed a disposable QA source from synthetic fixtures",
        description=(
            "Mints a fresh `qa-<hex>` source_id and lays down a canned "
            "ingest up through the stage selected by --at, so the next "
            "pipeline stage can be exercised in isolation. Use "
            "`reject-ingest <sid>` to clean up."
        ),
    )
    parser.add_argument(
        "--at",
        required=True,
        choices=sorted(_STAGES),
        help="Stage to seed inputs for; the printed next-step runs that stage.",
    )
    parser.add_argument(
        "--fixture",
        default=_DEFAULT_FIXTURE,
        help=f"Fixture name under {_FIXTURE_PACKAGE} (default: {_DEFAULT_FIXTURE}).",
    )
    parser.add_argument(
        "--source-id",
        dest="source_id",
        default=None,
        help="Override the minted qa-* id. Must not collide.",
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Execute the seed-ingest command."""
    try:
        cfg = cfg_mod.load_config()
    except cfg_mod.ConfigError as e:
        _logger.error("%s", e)
        return 1

    wiki_override: str | None = getattr(args, "wiki", None)

    try:
        sid = args.source_id or _mint_source_id(cfg, wiki_override=wiki_override)
        _seed(cfg, sid, args.at, args.fixture, wiki_override=wiki_override)
    except SeedIngestError as e:
        _logger.error("%s", e)
        return 1

    next_cmd = _NEXT_CMD[args.at].format(sid=sid)
    print(  # noqa: T201
        f"Seeded source {sid} at stage {args.at!r} from fixture {args.fixture!r}."
    )
    print(f"Next: auto-lorebook {next_cmd}")  # noqa: T201
    return 0


def _mint_source_id(
    cfg: cfg_mod.Config,
    wiki_override: str | None = None,
) -> str:
    for _ in range(_MINT_RETRY):
        candidate = f"qa-{secrets.token_hex(_SOURCE_ID_BYTES)}"
        if not _source_collides(cfg, candidate, wiki_override=wiki_override):
            return candidate
    msg = f"could not mint a unique source_id after {_MINT_RETRY} attempts"
    raise SeedIngestError(msg)


def _source_collides(
    cfg: cfg_mod.Config,
    sid: str,
    wiki_override: str | None = None,
) -> bool:
    wiki = cfg.resolve_active_wiki(wiki_override)
    wiki_src = wiki / "sources" / sid
    pending = wiki_state_mod.pending_source_dir(wiki, sid)
    return wiki_src.exists() or pending.exists()


def _seed(
    cfg: cfg_mod.Config,
    sid: str,
    at: str,
    fixture_name: str,
    wiki_override: str | None = None,
) -> None:
    import pathlib  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    import yaml  # noqa: PLC0415

    from auto_lorebook import db as db_mod  # noqa: PLC0415
    from auto_lorebook import reading_sidecar as sidecar_mod  # noqa: PLC0415
    from auto_lorebook import (  # noqa: PLC0415
        source_store,
        stage1b,
        structure_store,
    )
    from auto_lorebook import structure as structure_mod  # noqa: PLC0415
    from auto_lorebook.info_yaml import read_yaml  # noqa: PLC0415

    wiki_repo = cfg.resolve_active_wiki(wiki_override)
    if _source_collides(cfg, sid, wiki_override=wiki_override):
        msg = (
            f"source {sid!r} already exists under "
            f"{wiki_repo / 'sources' / sid} or "
            f"{wiki_state_mod.pending_source_dir(wiki_repo, sid)}"
        )
        raise SeedIngestError(msg)

    fixture_root = _resolve_fixture(fixture_name)
    wiki_src = wiki_repo / "sources" / sid

    keys = _STAGES[at]

    # always: copy transcript + write info.yaml
    _emit_file(fixture_root, "transcript.en.srt", wiki_src / "transcript.en.srt", sid)
    _emit_file(fixture_root, "info.yaml", wiki_src / "info.yaml", sid)

    # open DB connection for DB-backed state
    db_path = wiki_state_mod.wiki_db_path(wiki_repo)
    conn = db_mod.open(db_path)
    try:
        # read info back to get the Info object for record_in_db
        info = read_yaml(wiki_src / "info.yaml")

        # record sources + ingests row
        source_store.record_in_db(conn, info, sid, info.source_type)

        if "structure" in keys:
            src = fixture_root / "structure.yaml"
            text = src.read_text(encoding="utf-8").replace(_SOURCE_ID_PLACEHOLDER, sid)
            with tempfile.NamedTemporaryFile(
                suffix=".yaml", mode="w", encoding="utf-8", delete=False
            ) as tf:
                tf.write(text)
                tmp = pathlib.Path(tf.name)
            try:
                structure = structure_mod.read(tmp)
            finally:
                tmp.unlink(missing_ok=True)
            structure_store.write_structure(conn, sid, structure)

        if "bullets" in keys:
            src = fixture_root / "bullets.yaml"
            text = src.read_text(encoding="utf-8").replace(_SOURCE_ID_PLACEHOLDER, sid)
            with tempfile.NamedTemporaryFile(
                suffix=".yaml", mode="w", encoding="utf-8", delete=False
            ) as tf:
                tf.write(text)
                tmp = pathlib.Path(tf.name)
            try:
                rb = stage1b.read_bullets(tmp)
            finally:
                tmp.unlink(missing_ok=True)
            structure_store.write_bullets(conn, sid, rb)

        if "sidecar" in keys:
            src = fixture_root / "reading.yaml"
            text = src.read_text(encoding="utf-8").replace(_SOURCE_ID_PLACEHOLDER, sid)
            raw_sc = yaml.safe_load(text)
            sidecar_mod.write_state(
                conn,
                sid,
                default_speaker=str(raw_sc.get("default_speaker") or ""),
                name_corrections={
                    str(k): str(v)
                    for k, v in (raw_sc.get("name_corrections") or {}).items()
                },
                session_date=raw_sc.get("session_date"),
            )

        if "approved_reading" in keys:
            _emit_file(fixture_root, "reading.md", wiki_src / "reading.md", sid)

        conn.commit()
    finally:
        conn.close()


def _emit_file(
    fixture_root: Traversable,
    fixture_filename: str,
    dest: Path,
    sid: str,
) -> None:
    src = fixture_root / fixture_filename
    if not src.is_file():
        msg = f"fixture file missing: {fixture_filename} in {fixture_root}"
        raise SeedIngestError(msg)
    text = src.read_text(encoding="utf-8")
    text = text.replace(_SOURCE_ID_PLACEHOLDER, sid)
    atomic_write_text(dest, text)


def _resolve_fixture(fixture_name: str) -> Traversable:
    root = resources.files(_FIXTURE_PACKAGE) / fixture_name
    if not root.is_dir():
        msg = (
            f"fixture {fixture_name!r} not found under {_FIXTURE_PACKAGE}; "
            "available: " + ", ".join(sorted(_list_fixtures()))
        )
        raise SeedIngestError(msg)
    return root


def _list_fixtures() -> list[str]:
    root = resources.files(_FIXTURE_PACKAGE)
    return [child.name for child in root.iterdir() if child.is_dir()]
