"""Shared filesystem helpers: atomic writes, streaming file hash."""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from pathlib import Path

_CHUNK = 65536  # 64 KiB


def atomic_write_text(path: Path, text: str) -> None:
    """Write text to path atomically via tempfile + Path.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        tmp_path.replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def atomic_copy(src: Path, dest: Path) -> None:
    """Stream-copy src to dest atomically via tempfile + Path.replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as out, src.open("rb") as fh:
            while chunk := fh.read(_CHUNK):
                out.write(chunk)
        tmp_path.replace(dest)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def hash_file(path: Path) -> str:
    """SHA-256 hex digest of file bytes, streamed."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()
