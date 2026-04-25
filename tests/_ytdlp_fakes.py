"""Shared fake YoutubeDL class factory for ytdlp tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self


def make_fake_youtubedl(
    *,
    info: dict[str, Any] | None,
    subs: dict[str, str] | None = None,
    raises: Exception | None = None,
) -> tuple[type, dict[str, Any]]:
    """Return ``(FakeYDL class, captured_opts)`` for patching `YoutubeDL`.

    On `extract_info`:
    - If `raises` is set, raise it.
    - Otherwise write each `subs` entry into `Path(captured_opts["outtmpl"]).parent`
      and return `info`.

    `captured_opts` is populated when the fake is constructed (i.e. when
    `YoutubeDL(opts)` is called in the patched code).
    """
    captured_opts: dict[str, Any] = {}

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            captured_opts.clear()
            captured_opts.update(opts)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def extract_info(
            self,
            _url: str,
            *,
            download: bool = True,  # noqa: ARG002
        ) -> dict[str, Any] | None:
            if raises is not None:
                raise raises
            if subs:
                target_dir = Path(captured_opts["outtmpl"]).parent
                for fname, body in subs.items():
                    (target_dir / fname).write_text(body, encoding="utf-8")
            return info

    return FakeYDL, captured_opts
