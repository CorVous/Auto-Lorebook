"""CLI subcommands for auto-lorebook.

Each subcommand is defined in its own module and exports:
- add_parser(subparsers, common_parser): Register the subcommand
- run(args): Execute the subcommand logic
"""

from auto_lorebook.commands import approve_reading as approve_reading_cmd
from auto_lorebook.commands import configure_context as configure_context_cmd
from auto_lorebook.commands import entities as entities_cmd
from auto_lorebook.commands import generate_reading as generate_reading_cmd
from auto_lorebook.commands import ingest as ingest_cmd
from auto_lorebook.commands import plans as plans_cmd
from auto_lorebook.commands import regenerate_reading as regenerate_reading_cmd
from auto_lorebook.commands import reject_ingest as reject_ingest_cmd
from auto_lorebook.commands import replan as replan_cmd
from auto_lorebook.commands import review as review_cmd
from auto_lorebook.commands import version as version_cmd

__all__ = [
    "approve_reading_cmd",
    "configure_context_cmd",
    "entities_cmd",
    "generate_reading_cmd",
    "ingest_cmd",
    "plans_cmd",
    "regenerate_reading_cmd",
    "reject_ingest_cmd",
    "replan_cmd",
    "review_cmd",
    "version_cmd",
]
