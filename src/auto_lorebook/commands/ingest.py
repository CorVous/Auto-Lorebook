"""``ingest`` CLI subcommand.

Reads a lore source (SRT file, plain-text file, or stdin), parses it,
and prints a summary of what was ingested together with citation
information for each chunk.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

from auto_lorebook.parsers.chunks import group_into_chunks
from auto_lorebook.parsers.source import SourceType, make_source, youtube_timestamp_url
from auto_lorebook.parsers.srt import ParsedSRT, parse_srt

_logger = logging.getLogger(__name__)


def add_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    common_parser: argparse.ArgumentParser,
) -> None:
    """Register the ``ingest`` subcommand.

    :param subparsers: The parent subparsers action to attach to.
    :param common_parser: Shared parent parser for common flags.
    """
    parser = subparsers.add_parser(
        "ingest",
        parents=[common_parser],
        help="Ingest a lore source file and prepare it for wiki generation.",
        description=(
            "Parse an SRT subtitle file, plain-text file, or stdin and "
            "display a summary of parsed blocks and their source citations."
        ),
    )
    parser.add_argument(
        "path",
        metavar="<path>",
        help="Path to the lore file to ingest, or '-' to read from stdin.",
    )
    parser.add_argument(
        "--source-url",
        metavar="<url>",
        default=None,
        help=(
            "URL of the original source (e.g. a YouTube video URL). "
            "YouTube URLs will produce timestamped citation links."
        ),
    )
    parser.set_defaults(func=_run)


def _read_content(path: str) -> tuple[str, str]:
    """Read raw content from a file path or stdin.

    :param path: Filesystem path, or ``'-'`` for stdin.
    :return: Tuple of (content, filename).
    :raises FileNotFoundError: If *path* does not exist.
    """
    if path == "-":
        return sys.stdin.read(), "<stdin>"
    p = Path(path)
    if not p.exists():
        msg = f"File not found: {path}"
        raise FileNotFoundError(msg)
    return p.read_text(encoding="utf-8"), p.name


def _parse_content(content: str, filename: str) -> ParsedSRT | None:
    """Parse file content according to its type.

    SRT files (determined by filename extension) are parsed with the SRT
    parser.  All other content is treated as plain text and wrapped in a
    single synthetic block.

    :param content: Raw file content.
    :param filename: Original filename (used to detect file type).
    :return: A :class:`ParsedSRT` with the parsed blocks.
    """
    if filename.lower().endswith(".srt") or filename == "<stdin>":
        parsed = parse_srt(content)
        # If SRT parse produces no blocks, treat as plain text fallback
        if parsed.blocks:
            return parsed

    # Plain-text fallback: wrap entire content in a single synthetic block
    from auto_lorebook.parsers.srt import SubtitleBlock  # noqa: PLC0415

    block = SubtitleBlock(sequence=1, start=0.0, end=0.0, text=content.strip())
    return ParsedSRT(blocks=[block])


def _run(args: argparse.Namespace) -> int:
    """Execute the ingest command.

    :param args: Parsed CLI arguments.
    :return: Exit code.
    """
    try:
        content, filename = _read_content(args.path)
    except FileNotFoundError as exc:
        _logger.error("%s", exc)
        return 1

    parsed = _parse_content(content, filename)
    if parsed is None or not parsed.blocks:
        print("No content found to ingest.")  # noqa: T201
        return 1

    source = make_source(url=args.source_url, filename=filename)

    block_count = len(parsed.blocks)
    source_label = source.source_type.name  # "youtube", "url", or "local"

    print(f"Ingested: {filename}")  # noqa: T201
    print(f"Blocks:   {block_count}")  # noqa: T201
    print(f"Source:   {source_label}" + (f" ({source.url})" if source.url else ""))  # noqa: T201

    # For YouTube sources, emit a sample timestamp link per chunk
    if source.source_type == SourceType.youtube and source.url:
        chunks = group_into_chunks(parsed.blocks)
        print()  # noqa: T201
        print("Chunk citations:")  # noqa: T201
        for i, chunk in enumerate(chunks, start=1):
            link = youtube_timestamp_url(source.url, chunk.start)
            range_str = f"{chunk.start:.1f}s-{chunk.end:.1f}s"
            print(  # noqa: T201
                f"  Chunk {i} ({chunk.block_count} blocks, {range_str}): {link}"
            )

    return 0
