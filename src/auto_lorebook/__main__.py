"""Allow running auto-lorebook as a module: python -m auto_lorebook."""

import sys

from auto_lorebook.cli import main

if __name__ == "__main__":
    sys.exit(main())
