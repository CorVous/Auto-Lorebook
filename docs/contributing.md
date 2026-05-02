# Contributing

## First step

Update uv before anything else:

```bash
uv self update
```

If uv was installed via Homebrew, use `brew update && brew upgrade uv`
instead.

## TDD workflow

Always follow red/green TDD:

1. Write a failing test first (red).
2. Write the minimum code to make it pass (green).
3. Refactor if needed.

**Import errors do not count as red.** A test that fails due to an
`ImportError` or `ModuleNotFoundError` is not a valid red test — the
module or function must exist before the test can legitimately fail for
the right reason.

## Live integration tests

Code that talks to a real external service (OpenRouter, yt-dlp /
YouTube, future providers) gets a `@pytest.mark.live` test in
`tests/test_live_integration.py` alongside its mocked unit tests.

Live tests are skipped by default and **not run in CI**: they cost real
money (OpenRouter) and depend on third-party availability (YouTube).
Opt in locally:

```bash
uv run pytest --run-live
```

Each test additionally skips if its required env var is missing
(`OPENROUTER_API_KEY` for OpenRouter), so `--run-live` on a fresh
checkout still passes for whatever subset the runner has credentials
for. Override the OpenRouter model with `LIVE_TEST_MODEL=...` to run
against a cheaper model than the project default.

When you add or change a real-world integration boundary, add or
update the matching live test in the same commit.

## QA seeding

`auto-lorebook seed-ingest --at=<stage>` mints a fresh disposable
`qa-<hex>` source_id and lays down synthetic stage-input artifacts, so
a single pipeline stage can be exercised in isolation without running
the prior stages or hitting the LLM.

```bash
uv run auto-lorebook seed-ingest --at=plan
# Seeded source qa-1a2b3c4d at stage 'plan' from fixture 'tiny-aldara'.
# Next: auto-lorebook replan qa-1a2b3c4d
```

Stage ladder (each `--at` value seeds everything from prior levels too):

| `--at`      | Next command                                              |
|-------------|-----------------------------------------------------------|
| `structure` | `generate-reading <sid>` — runs Stage 1a + 1b             |
| `summarize` | `regenerate-reading <sid> --from=summarize` — runs 1b     |
| `approve`   | `approve-reading <sid> --yes` — runs approve + plan + extract |
| `plan`      | `replan <sid>` — runs Stage 2 + 3                         |

Fixtures live in the package at `src/auto_lorebook/_qa_fixtures/`; the
default is `tiny-aldara` (a 4-cue SRT with two segments). Add new
fixtures by dropping a sibling directory containing the same set of
artifacts.

Clean up with `reject-ingest <sid>` (which knows how to remove pending
artifacts and any contributions written into the wiki). The interactive
review and reading-approval gates are out of scope for QA seeding —
exercise them manually if you need to test those paths.

## After every code assignment

Run these in order:

```bash
uv sync --dev          # keep dependencies fresh
uv run ruff check      # lint
uv run ruff format     # format
uv run ty check        # type-check
uv run pytest          # run tests
```

If the change touched any of:

- Environment variables or config keys.
- On-disk layout under `sources/`, `<category>/`, or
  `~/.auto-lorebook/`.
- Architecture (pipeline stages, entity model, artifact hashing).

Update the matching page under `docs/` **in the same commit**, then run:

```bash
uv run mkdocs build --strict
```

Doc drift is a CI failure, not a future cleanup task.

## Technical writing style

Docstrings and inline comments use **telegraphic** style:

- Omit articles ("the", "a"), auxiliary verbs, unnecessary prepositions,
  filler words.
- Keep nouns, verbs, adjectives, key modifiers.
- Don't restate types already in signatures.
- Don't summarize functions when the name is self-explanatory.
- Start inline comments lowercase.
- Use periods only for full sentences.

Wiki pages (`docs/*.md`) keep full sentences for readability, but still
aim for concise — trim wordiness, filler, restating.

## Behavior guidelines

These reduce common LLM coding mistakes. For trivial tasks, use
judgment; the guidelines bias toward caution over speed.

### Think before coding

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### Simplicity first

- No features beyond what was asked.
- No abstractions for single-use code.
- No flexibility or configurability that wasn't requested.
- No error handling for impossible scenarios.

Ask yourself: "Would a senior engineer say this is overcomplicated?"
If yes, simplify.

### Surgical changes

- Touch only what you must.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

Every changed line should trace directly to the user's request.

### Goal-driven execution

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them
  pass."
- "Fix the bug" → "Write a test that reproduces it, then make it pass."
- "Refactor X" → "Ensure tests pass before and after."

Strong success criteria let you loop independently. Weak criteria
("make it work") require constant clarification.

## Pull requests

Keep PRs narrow. One feature or fix per PR. Reference the spec section
or docs page any non-obvious decision draws from. If the change touches
architecture, update `docs/architecture/` in the same commit.
