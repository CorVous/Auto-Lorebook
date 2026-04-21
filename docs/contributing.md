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
