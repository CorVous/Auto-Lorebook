"""DB-specific exceptions."""

from __future__ import annotations


class SchemaVersionTooNewError(RuntimeError):
    """Raised when wiki.db schema_version exceeds what this tool supports."""

    def __init__(self, db_version: int, tool_version: int) -> None:
        self.db_version = db_version
        self.tool_version = tool_version
        super().__init__(
            f"wiki.db schema_version {db_version} is newer than this tool "
            f"supports ({tool_version}); upgrade the tool"
        )
