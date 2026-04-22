# Plan 04 — OpenRouter client & preamble assembly

**Prerequisite:** Plans 01–03.

## Goal

Bring LLM capability online: OpenRouter client with configurable
model slots, deterministic preamble assembly, and the token-budget
check. No Stage 1a/1b logic yet — this plan lands the substrate and
one smoke-test command that exercises preamble + a trivial LLM round
trip.

## In scope

- OpenRouter client:
  - Reads API key from `OPENROUTER_API_KEY` (fail fast with a clear
    error if missing when an LLM call is attempted).
  - Uses model slots from `config.yaml`; both reading substages
    default to the primary model.
  - Retries with exponential backoff on transient errors; no retry
    on 4xx.
  - Records model identity + parameters for the `inputs` block (used
    later for staleness).
- Deterministic preamble assembly from:
  1. `info.yaml` context,
  2. `.wiki-context.yaml`,
  3. `.transcription-corrections.yaml`,
  4. Entity index — **empty in Phase 1** but the code path exists and
     renders an empty `Entities in this wiki` section.
- Token-budget check:
  - Uses a configurable fraction (default 0.8) of the model's
    context window.
  - Token count via a pure-Python counter that's deterministic and
    offline (e.g., `tiktoken` if dependency-light; otherwise a
    heuristic with a caveat).
  - On breach, fails with the specific component error text
    documented in `docs/pipeline/context.md` (names the oversized
    component; lists remedies).
- `generate-reading` command scaffold:
  - Accepts `<source_id>`.
  - Halts after preamble assembly + a single minimal LLM ping (a
    "reply OK" style call) purely to prove the pipe. Stage 1a lands
    in Plan 05.
  - Writes the preamble to `pending/<ingest_id>/reading/preamble.txt`
    as a debug artifact.

## Out of scope

- Stage 1a prompt or `structure.yaml` — Plan 05.
- Stage 1b — Plan 06.
- Real entity index — Phase 2.

## TDD plan

### Red tests to write first

- `test_preamble_order_is_deterministic` — same inputs → byte-identical
  preamble; re-ordering `speakers:` list yields different preambles
  (no hidden sorting).
- `test_preamble_entity_index_empty_section_rendered` — with no
  entities, the section header still appears (empty list).
- `test_preamble_omits_empty_wiki_context_gracefully` — empty file
  → preamble omits the setting block; no blank sections with
  trailing whitespace.
- `test_token_budget_fires_with_named_component` — seed a huge
  `.wiki-context.yaml`; budget breach message names that file and
  lists the three documented remedies.
- `test_openrouter_client_retries_on_5xx_not_4xx` — injected fake
  transport with scripted responses.
- `test_openrouter_client_requires_api_key` — unset env var →
  actionable error at call time, not at import time.
- `test_generate_reading_scaffold_writes_preamble_artifact` —
  pending dir contains `preamble.txt` after a successful smoke run.

### Implementation sketch

- `auto_lorebook/llm/openrouter.py` — client with injectable
  transport; dataclass for model params.
- `auto_lorebook/llm/budget.py` — token counter + budget check.
- `auto_lorebook/preamble.py` — pure assembly function; takes the
  three readers' outputs + entity-index snapshot; returns a string.
- `auto_lorebook/commands/generate_reading.py` — scaffold only.
- `tests/support/fakes/openrouter.py` — fake client.

### Docs touched

- `docs/pipeline/context.md` — confirm preamble skeleton matches
  exactly (section headers, ordering).
- `docs/reference/file-formats.md` — add the
  `pending/<ingest_id>/reading/preamble.txt` debug artifact.
- Verify `tests/test_docs.py` env-var check still passes after adding
  `OPENROUTER_API_KEY` reference.

## Integration test (plan exit gate)

`tests/integration/test_plan_04_preamble.py`:

1. Build a wiki tmp dir with a non-empty `.wiki-context.yaml` and a
   two-entry `.transcription-corrections.yaml`.
2. Ingest a source via the Plan 02 path (so `info.yaml` exists with
   context from Plan 03).
3. Run `generate-reading <source_id>` against the fake OpenRouter
   client.
4. Assert:
   - `pending/<ingest_id>/reading/preamble.txt` exists.
   - Preamble contains each of the four sections in order.
   - Fake client saw the preamble as prompt prefix.
5. Second assertion run: crank `.wiki-context.yaml` above the budget
   and re-run; expect the budget error naming `.wiki-context.yaml`.

Gate: integration test green; `mkdocs build --strict` green;
`tests/test_docs.py` green (including any new env vars).
