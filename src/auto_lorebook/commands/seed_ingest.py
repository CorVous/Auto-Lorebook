"""auto-lorebook seed-ingest subcommand.

Seeds a fresh disposable ``qa-*`` source with synthetic stage-input
fixtures, so a stage can be exercised in isolation without running the
prior stages or hitting the LLM. Pair with ``reject-ingest <sid>`` to
clean up.
"""

from __future__ import annotations

import logging
import secrets
from importlib import resources
from typing import TYPE_CHECKING

from auto_lorebook import config as cfg_mod
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

# stage → (next CLI hint, ordered list of files to seed cumulatively)
_STAGES: dict[str, tuple[str, ...]] = {
    "structure": ("info", "transcript"),
    "summarize": ("info", "transcript", "structure"),
    "approve": ("info", "transcript", "structure", "bullets", "sidecar", "segments"),
    "plan": (
        "info",
        "transcript",
        "structure",
        "bullets",
        "sidecar",
        "segments",
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

    try:
        sid = args.source_id or _mint_source_id(cfg)
        _seed(cfg, sid, args.at, args.fixture)
    except SeedIngestError as e:
        _logger.error("%s", e)
        return 1

    next_cmd = _NEXT_CMD[args.at].format(sid=sid)
    print(  # noqa: T201
        f"Seeded source {sid} at stage {args.at!r} from fixture {args.fixture!r}."
    )
    print(f"Next: auto-lorebook {next_cmd}")  # noqa: T201
    return 0


def _mint_source_id(cfg: cfg_mod.Config) -> str:
    for _ in range(_MINT_RETRY):
        candidate = f"qa-{secrets.token_hex(_SOURCE_ID_BYTES)}"
        if not _source_collides(cfg, candidate):
            return candidate
    msg = f"could not mint a unique source_id after {_MINT_RETRY} attempts"
    raise SeedIngestError(msg)


def _source_collides(cfg: cfg_mod.Config, sid: str) -> bool:
    wiki_src = cfg.wiki_repo_path / "sources" / sid
    pending = cfg_mod.config_dir() / "pending" / sid
    return wiki_src.exists() or pending.exists()


def _seed(cfg: cfg_mod.Config, sid: str, at: str, fixture_name: str) -> None:
    if _source_collides(cfg, sid):
        msg = (
            f"source {sid!r} already exists under "
            f"{cfg.wiki_repo_path / 'sources' / sid} or "
            f"{cfg_mod.config_dir() / 'pending' / sid}"
        )
        raise SeedIngestError(msg)

    fixture_root = _resolve_fixture(fixture_name)
    wiki_src = cfg.wiki_repo_path / "sources" / sid
    pending_reading = cfg_mod.config_dir() / "pending" / sid / "reading"

    # wiki side first so a half-failed seed leaves only sources/<sid>
    # behind, which `reject-ingest` will tolerate.
    for key in _STAGES[at]:
        if key == "segments":
            _emit_segments_dir(fixture_root, pending_reading / "segments", sid)
        else:
            src_name, dest, status = _emit_target(key, wiki_src, pending_reading)
            _emit_file(fixture_root, src_name, dest, sid, status=status)


def _emit_target(
    key: str,
    wiki_src: Path,
    pending_reading: Path,
) -> tuple[str, Path, str | None]:
    if key == "info":
        return "info.yaml", wiki_src / "info.yaml", None
    if key == "transcript":
        return "transcript.en.srt", wiki_src / "transcript.en.srt", None
    if key == "structure":
        return "structure.yaml", pending_reading / "structure.yaml", None
    if key == "bullets":
        return "bullets.yaml", pending_reading / "bullets.yaml", None
    if key == "sidecar":
        return "reading.yaml", pending_reading / "reading.yaml", None
    if key == "approved_reading":
        return "reading.md", wiki_src / "reading.md", "approved"
    msg = f"internal: unknown seed key {key!r}"
    raise SeedIngestError(msg)


def _emit_file(
    fixture_root: Traversable,
    fixture_filename: str,
    dest: Path,
    sid: str,
    *,
    status: str | None,
) -> None:
    src = fixture_root / fixture_filename
    if not src.is_file():
        msg = f"fixture file missing: {fixture_filename} in {fixture_root}"
        raise SeedIngestError(msg)
    text = src.read_text(encoding="utf-8")
    text = text.replace(_SOURCE_ID_PLACEHOLDER, sid)
    if status is not None:
        # fixture's reading.md ships approved; rewrite for pre-approval seeding.
        text = text.replace(
            "reading_status: approved",
            f"reading_status: {status}",
        )
    atomic_write_text(dest, text)


def _emit_segments_dir(
    fixture_root: Traversable,
    dest_dir: Path,
    sid: str,
) -> None:
    """Copy all *.md files from fixture segments/ to dest_dir."""
    src_dir = fixture_root / "segments"
    if not src_dir.is_dir():
        msg = f"fixture segments/ directory missing in {fixture_root}"
        raise SeedIngestError(msg)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for child in src_dir.iterdir():
        if not child.name.endswith(".md"):
            continue
        text = child.read_text(encoding="utf-8")
        text = text.replace(_SOURCE_ID_PLACEHOLDER, sid)
        atomic_write_text(dest_dir / child.name, text)


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
