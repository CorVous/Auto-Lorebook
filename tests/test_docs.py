"""Automated checks that docs haven't drifted from code.

If a test here fails, documentation and implementation have diverged.
Fix the mismatch in the same PR — don't ignore the failure. See
``AGENTS.md`` for the manual workflow these checks enforce.

Directional by design: anything a doc names must exist in source, but
the reverse is not enforced (too many internal tuning knobs to
reasonably document).
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from auto_lorebook.cli import create_parser

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_ROOT = REPO_ROOT / "docs"
SRC_ROOT = REPO_ROOT / "src" / "auto_lorebook"
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"
CLI_DOC = DOCS_ROOT / "reference" / "cli.md"

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

#: Env vars documented for operator config where the read path isn't
#: wired yet. Remove entries as Phase 1 lands config loading.
ENV_VAR_ALLOWLIST: frozenset[str] = frozenset({
    # Planned: documented in docs/getting-started/installation.md for
    # the OpenRouter client. Read path lands with Phase 1's config
    # module.
    "OPENROUTER_API_KEY",
    # Test-only: read in tests/test_live_integration.py to override the
    # model used by live OpenRouter tests. Not a runtime config knob.
    "LIVE_TEST_MODEL",
})

#: Markdown files under ``docs/`` intentionally outside the published
#: ``mkdocs.yml`` nav (e.g. shared includes).
DOC_ORPHAN_ALLOWLIST: frozenset[str] = frozenset()

#: Registered CLI subcommands intentionally absent from the user-facing
#: CLI reference (e.g. debugging-only). Prefer documenting over
#: allowlisting.
CLI_SUBCOMMAND_ALLOWLIST: frozenset[str] = frozenset()

# ---------------------------------------------------------------------------
# mkdocs.yml loader (tolerates pymdownx !!python/name: tags)
# ---------------------------------------------------------------------------


class _MkdocsLoader(yaml.SafeLoader):
    """SafeLoader tolerant of mkdocs-material's ``!!python/name:`` tags."""


def _ignore_python_name(
    loader: yaml.Loader,  # noqa: ARG001
    suffix: str,  # noqa: ARG001
    node: yaml.Node,  # noqa: ARG001
) -> None:
    return None


_MkdocsLoader.add_multi_constructor(
    "tag:yaml.org,2002:python/name:",
    _ignore_python_name,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flatten_nav(nav: Iterable[object]) -> Iterator[str]:
    """Walk the mkdocs nav tree.

    Yields:
        Each relative markdown file path referenced by the tree.

    """
    for item in nav:
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            for value in item.values():
                if isinstance(value, str):
                    yield value
                elif isinstance(value, list):
                    yield from _flatten_nav(value)


def _read_mkdocs_nav() -> set[str]:
    cfg = yaml.load(
        MKDOCS_YML.read_text(encoding="utf-8"),
        Loader=_MkdocsLoader,  # noqa: S506
    )
    return set(_flatten_nav(cfg["nav"]))


def _docs_markdown_files() -> list[Path]:
    return sorted(DOCS_ROOT.rglob("*.md"))


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _is_os_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "os"


def _extract_env_var_from_call(call: ast.Call) -> str | None:
    """Recognise ``os.environ.get(...)`` and ``os.getenv(...)``."""
    if not call.args:
        return None
    first = call.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return None
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    if func.attr == "get" and _is_os_environ(func.value):
        return first.value
    if func.attr == "getenv" and _is_os_name(func.value):
        return first.value
    return None


def _extract_env_var_from_subscript(sub: ast.Subscript) -> str | None:
    """Recognise ``os.environ["..."]``."""
    if not _is_os_environ(sub.value):
        return None
    key = sub.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    return None


def _collect_env_vars_read_in_src() -> set[str]:
    names: set[str] = set()
    for py_file in SRC_ROOT.rglob("*.py"):
        if py_file.name == "_version.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _extract_env_var_from_call(node)
                if name is not None:
                    names.add(name)
            elif isinstance(node, ast.Subscript):
                name = _extract_env_var_from_subscript(node)
                if name is not None:
                    names.add(name)
    return names


# Inline backtick tokens in SHOUT_CASE with at least one underscore.
# Underscore requirement filters out bare constant-case words like
# IDLE / SPEAKING that happen to look env-var-shaped.
_INLINE_ENV_VAR_RE = re.compile(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`")
_BASH_ASSIGNMENT_RE = re.compile(r"^([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)=")
_BASH_BLOCK_LANGUAGES = frozenset({"bash", "sh", "shell", "env", "dotenv"})
_ENV_CONTEXT_RE = re.compile(
    r"\benv\b|\.env\b|environment variable|os\.environ|getenv|dotenv",
    re.IGNORECASE,
)


def _extract_bash_env_vars(text: str) -> set[str]:
    """Extract ``NAME=...`` assignments from fenced bash/shell/env blocks."""
    found: set[str] = set()
    in_env_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            lang = stripped[3:].strip().lower()
            if in_env_block:
                in_env_block = False
            elif lang in _BASH_BLOCK_LANGUAGES:
                in_env_block = True
            continue
        if in_env_block:
            match = _BASH_ASSIGNMENT_RE.match(line)
            if match is not None:
                found.add(match.group(1))
    return found


def _collect_env_vars_mentioned_in_docs() -> set[str]:
    """Scan docs for env var mentions with two complementary signals.

    Strong: ``NAME=value`` inside fenced bash/shell/env blocks — always
    counted.

    Weaker: inline backticked SHOUT_CASE tokens containing an
    underscore — counted only in docs that also mention env-context
    cues (``.env``, "environment variable", "os.environ", etc.). Avoids
    matching enum values or lint codes that look env-var-shaped.
    """
    found: set[str] = set()
    for md_file in _docs_markdown_files():
        text = md_file.read_text(encoding="utf-8")
        found.update(_extract_bash_env_vars(text))
        if _ENV_CONTEXT_RE.search(text):
            found.update(m.group(1) for m in _INLINE_ENV_VAR_RE.finditer(text))
    return found


def _registered_subcommands() -> set[str]:
    """Names of every argparse subcommand registered in ``cli.py``."""
    parser = create_parser()
    for action in parser._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            return set(action.choices)
    return set()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_orphaned_docs() -> None:
    """Every markdown file under ``docs/`` must appear in ``mkdocs.yml`` nav."""
    nav_files = _read_mkdocs_nav()
    on_disk = {p.relative_to(DOCS_ROOT).as_posix() for p in _docs_markdown_files()}
    orphans = on_disk - nav_files - DOC_ORPHAN_ALLOWLIST
    assert not orphans, (
        f"Found markdown files under docs/ that aren't referenced by "
        f"mkdocs.yml nav: {sorted(orphans)}. Either add them to nav, "
        f"delete them, or (if they're intentional shared includes) add "
        f"them to DOC_ORPHAN_ALLOWLIST."
    )


def test_env_vars_documented_exist_in_src() -> None:
    """Every env var mentioned in docs must actually be read by src/.

    One-directional: undocumented internal tuning knobs are fine, but
    documenting an env var that no code reads is unambiguous drift.
    """
    documented = _collect_env_vars_mentioned_in_docs()
    src_read = _collect_env_vars_read_in_src()
    missing = documented - src_read - ENV_VAR_ALLOWLIST
    assert not missing, (
        f"Docs mention env vars not read anywhere in src/auto_lorebook: "
        f"{sorted(missing)}. Either remove them from docs, implement the "
        f"read path, or (if planned or third-party) add them to "
        f"ENV_VAR_ALLOWLIST in tests/test_docs.py."
    )


def test_registered_cli_subcommands_documented() -> None:
    """Every argparse subcommand in ``cli.py`` must appear in cli.md.

    One-directional: the CLI reference documents the full spec'd surface
    including commands not yet implemented, so documented-but-unregistered
    is expected while phases land. Registered-but-undocumented is drift.
    """
    cli_doc = CLI_DOC.read_text(encoding="utf-8")
    registered = _registered_subcommands()
    missing = sorted(
        cmd
        for cmd in registered
        if cmd not in CLI_SUBCOMMAND_ALLOWLIST and cmd not in cli_doc
    )
    assert not missing, (
        f"CLI subcommands registered in cli.py but missing from "
        f"docs/reference/cli.md: {missing}. Add an entry, or (for "
        f"debugging-only commands) add to CLI_SUBCOMMAND_ALLOWLIST."
    )
