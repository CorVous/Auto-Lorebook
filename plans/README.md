# Phase 1 build plans

Implementation plans for [Phase 1: Reading stage](../docs/roadmap/index.md).
Each plan is independently buildable and ends with an integration test
that gates merge. Follow the red/green TDD workflow in
[`AGENTS.md`](../AGENTS.md) inside every plan.

## Ordering

Plans depend on their predecessors. Do not skip ahead.

| #  | Plan                                                              | Ends with                                            |
|----|-------------------------------------------------------------------|------------------------------------------------------|
| 01 | [Config & pending state](01-config-and-pending-state.md)          | `config.yaml` round-trip + `pending/` layout         |
| 02 | [Source ingestion](02-source-ingestion.md)                        | Ingest a YouTube URL (recorded fixture) → `sources/` |
| 03 | [Context files & prompts](03-context-and-prompts.md)              | Interactive context → full `info.yaml` context block |
| 04 | [OpenRouter client & preamble](04-openrouter-and-preamble.md)     | Deterministic preamble + token-budget error          |
| 05 | [Stage 1a structure + gap check](05-stage-1a-structure.md)        | `structure.yaml` + heuristic warning on fixture      |
| 06 | [Stage 1b summarize + reading](06-stage-1b-summarize.md)          | Draft `reading.md` with clickable timestamps         |
| 07 | [Reading review commands](07-reading-review.md)                   | Approve + regenerate + list/show                     |
| 08 | [Full integration & sanity check](08-full-integration-sanity.md)  | Phase 1 exit criterion met end-to-end                |

## After each plan

Run the full after-assignment checklist from `AGENTS.md`:

1. `uv sync --dev`
2. `uv run ruff check`
3. `uv run ruff format`
4. `uv run ty check`
5. `uv run pytest`
6. Update the matching `docs/` page in the same commit; run
   `uv run mkdocs build --strict`.

## TDD discipline reminder

- Write the failing test first. `ImportError` is **not** a valid red —
  the target module/function must exist first.
- Stop at "minimum to pass". Resist speculative abstraction.
- Integration tests at plan end exercise real on-disk artifacts and
  mocked external processes (yt-dlp, OpenRouter); they are **not**
  end-to-end against real network unless explicitly stated.

## Fixtures

Store shared fixtures under `tests/support/`. Expected additions over
the course of Phase 1:

- `tests/support/fixtures/srt/` — sample SRT files (short, long,
  malformed).
- `tests/support/fixtures/ytdlp/` — recorded `yt-dlp` JSON outputs.
- `tests/support/fixtures/readings/` — canned LLM responses for 1a
  and 1b.
- `tests/support/fakes/openrouter.py` — in-process fake client that
  serves canned responses by prompt fingerprint.
