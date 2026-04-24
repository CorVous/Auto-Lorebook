"""CLI subcommands for auto-lorebook.

Each subcommand is defined in its own module and exports:
- add_parser(subparsers, common_parser): Register the subcommand
- run(args): Execute the subcommand logic
"""

from auto_lorebook.commands import configure_context as configure_context_cmd
from auto_lorebook.commands import ingest as ingest_cmd
from auto_lorebook.commands import version as version_cmd

__all__ = ["configure_context_cmd", "ingest_cmd", "version_cmd"]
