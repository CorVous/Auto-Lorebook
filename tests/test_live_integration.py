"""Live integration tests against real external services.

Skipped by default. Opt in with `uv run pytest --run-live`. Never run in
CI: cost money (OpenRouter) and depend on third-party availability
(YouTube). Each test additionally skips if its required env var is
missing, so `--run-live` on a fresh checkout still passes cleanly for
the subset the runner has credentials for.

Add a test here whenever you add or change a real-world integration
boundary; mirror the unit-test coverage in `test_openrouter.py` /
`test_ytdlp.py` with one round-trip against the real service.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from auto_lorebook import db
from auto_lorebook.entities import create_entity, list_aliases
from auto_lorebook.facts import create_fact_with_target, list_facts_by_entity
from auto_lorebook.openrouter import OpenRouterClient, OpenRouterResponse
from auto_lorebook.stage4 import SummarizeResult
from auto_lorebook.stage4 import run as stage4_run
from auto_lorebook.ytdlp import fetch

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.live


# Stable Rick Astley upload (2009); reliable English captions.
_LIVE_YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_LIVE_YOUTUBE_ID = "dQw4w9WgXcQ"

# Project default model; override with LIVE_TEST_MODEL for cheaper runs.
_DEFAULT_LIVE_MODEL = "anthropic/claude-sonnet-4-5"

_OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")


@pytest.mark.skipif(
    not _OPENROUTER_KEY,
    reason="set $OPENROUTER_API_KEY to run this live test",
)
def test_openrouter_round_trip_live() -> None:
    """Round-trip a tiny prompt through OpenRouter, verify usage fields."""
    model = os.environ.get("LIVE_TEST_MODEL", _DEFAULT_LIVE_MODEL)
    client = OpenRouterClient(api_key=_OPENROUTER_KEY, default_model=model)

    resp = client.complete(
        [
            {
                "role": "user",
                "content": "Reply with the single word PONG and nothing else.",
            },
        ],
        temperature=0.0,
    )

    assert isinstance(resp, OpenRouterResponse)
    assert "PONG" in resp.text.upper()
    assert resp.model  # provider echoes the model used
    assert resp.tokens_in is not None
    assert resp.tokens_in > 0
    assert resp.tokens_out is not None
    assert resp.tokens_out > 0


def test_ytdlp_fetch_subtitles_live(tmp_path: Path) -> None:
    """Fetch English SRT for a stable YouTube source via yt-dlp."""
    result = fetch(_LIVE_YOUTUBE_URL, tmp_path)

    assert result.video_id == _LIVE_YOUTUBE_ID
    assert result.title
    assert result.duration > 0
    assert result.srt_path.exists()
    assert result.srt_path.suffix == ".srt"
    body = result.srt_path.read_text(encoding="utf-8")
    assert body.strip(), "SRT file was empty"
    # SRT cue blocks contain `-->` between start/end timestamps.
    assert "-->" in body


@pytest.mark.skipif(
    not _OPENROUTER_KEY,
    reason="set $OPENROUTER_API_KEY to run this live test",
)
def test_stage4_summarizer_live() -> None:
    """Round-trip stage4 summarizer through OpenRouter with a real entity."""
    model = os.environ.get("LIVE_TEST_MODEL", _DEFAULT_LIVE_MODEL)
    client = OpenRouterClient(api_key=_OPENROUTER_KEY, default_model=model)

    conn = db.open(":memory:")
    conn.execute(
        "INSERT INTO sources(source_id, source_type, fetched_at, context_json)"
        " VALUES ('src-live', 'youtube', '2026-01-01T00:00:00Z', '{}')"
    )
    conn.execute(
        "INSERT INTO ingests(ingest_id, source_id, started_at, state)"
        " VALUES ('ing-live', 'src-live', '2026-01-01T00:00:00Z', 'done')"
    )
    entity = create_entity(
        conn,
        category="characters",
        slug="aldara",
        canonical_name="Aldara",
        ingest_id="ing-live",
    )
    create_fact_with_target(
        conn,
        fact_id="f-live-001",
        text="Aldara is an ancient sorceress who founded the city of Mireth.",
        raw_transcript_span=(
            "Aldara is an ancient sorceress who founded the city of Mireth."
        ),
        text_corrects_transcript=False,
        source_id="src-live",
        locator="0:01:00",
        status="authoritative",
        approved_at="2026-01-01T00:00:00Z",
        created_by_ingest="ing-live",
        entity_category="characters",
        entity_slug="aldara",
        section="biography",
        by="live-test",
    )
    conn.commit()

    aliases = list_aliases(conn, "characters", "aldara")
    facts = list_facts_by_entity(conn, "characters", "aldara")

    result = stage4_run(
        entity=entity,
        aliases=aliases,
        facts=facts,
        entity_index="characters:\n  - Aldara",
        wiki_setting="A high-fantasy world with ancient magic.",
        client=client,
        model=model,
    )

    assert isinstance(result, SummarizeResult)
    assert result.prose
    assert len(result.prose) > 20  # non-trivial prose returned
