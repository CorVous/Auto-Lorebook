# Plan 03 — Context files & prompts

**Prerequisite:** Plans 01, 02.

## Goal

Add the `context:` block to `info.yaml` and the interactive prompts
that populate it, plus tolerant readers for `.wiki-context.yaml` and
`.transcription-corrections.yaml`. After this plan, the full
context-gathering step of Phase 1 works (no LLM yet).

## In scope

- `info.yaml` extension: add the `context` block with
  `perspective`, `source_nature`, `setting`, `speakers`, `notes`.
  All fields optional; blank permitted.
- Interactive context prompts matching `docs/pipeline/context.md`:
  - Each field Enter-to-skip.
  - Defaults: CLI flag → `.wiki-context.yaml` → `last-context.yaml`.
  - `last-context.yaml` is written under `~/.auto-lorebook/` after
    each completed ingest (perspective + source_nature only).
  - Ctrl-C mid-prompt saves partial state.
  - Invalid input re-prompts with hint.
- CLI flags on `ingest`: `--session-date`, `--perspective`,
  `--source-nature`, `--setting`, `--no-interactive`.
- Non-interactive detection: no TTY or piped stdin → auto
  `--no-interactive` with a notice.
- `.wiki-context.yaml` reader:
  - Missing file → returns a neutral empty object (not an error).
  - Empty file → same.
  - Unknown keys logged at debug; not fatal.
- `.transcription-corrections.yaml` reader:
  - Same tolerance as above.
  - Returns an ordered mapping preserving insertion order for
    deterministic preamble output.
- `auto-lorebook configure-context <source_id>` command — re-runs
  prompts for an existing `info.yaml`, preserving unrelated fields.

## Out of scope

- Preamble assembly — Plan 04.
- `generate-reading` LLM invocation — Plan 04+.
- Name-correction promotion (`promote-correction`) — later phases.

## TDD plan

### Red tests to write first

- `test_wiki_context_reader_missing_file_returns_empty`.
- `test_wiki_context_reader_empty_file_returns_empty`.
- `test_wiki_context_reader_preserves_known_fields`.
- `test_corrections_reader_preserves_order`.
- `test_interactive_prompt_defaults_precedence` — flags > wiki-context
  > last-context.
- `test_no_interactive_skips_all_prompts`.
- `test_non_tty_auto_no_interactive`.
- `test_invalid_session_date_reprompts`.
- `test_ctrlc_saves_partial_info_yaml` — simulate KeyboardInterrupt
  mid-prompt; assert the partially-captured fields made it to disk.
- `test_configure_context_preserves_base_fields` —
  `configure-context` does not clobber `source_id`, `title`,
  `duration_seconds`, etc.
- `test_last_context_roundtrip` — completing a full context run
  writes `last-context.yaml`; next run pre-fills from it.

### Implementation sketch

- `auto_lorebook/context/wiki.py` — tolerant reader returning a
  frozen dataclass with optional fields.
- `auto_lorebook/context/corrections.py` — ordered mapping reader.
- `auto_lorebook/context/prompts.py` — interactive driver isolated
  from stdin/stdout so tests drive it with an iterator.
- `auto_lorebook/commands/configure_context.py`.
- Extend `commands/ingest.py` with the new flags and post-ingest
  prompt flow.

### Docs touched

- `docs/pipeline/context.md` — verify prompt text, defaults, and
  flag behavior match exactly.
- `docs/reference/cli.md` — no change expected (CLI reference hook
  pulls flags from code).

## Integration test (plan exit gate)

`tests/integration/test_plan_03_context.py`:

1. Fresh `tmp_path` wiki; empty `.wiki-context.yaml`.
2. Run `auto-lorebook ingest <url> --session-date=2026-01-15
   --perspective="Cor playing Kiki" --source-nature=actual-play
   --no-interactive` with the Plan 02 mocked yt-dlp.
   Assert: `sources/yt-abc123/info.yaml` now contains the full
   `context` block; `last-context.yaml` written.
3. Delete `info.yaml.context`; run `configure-context yt-abc123`
   driven by scripted prompt input including one invalid date that
   re-prompts. Assert: final `info.yaml` matches expected shape and
   base fields are untouched.
4. Populate `.wiki-context.yaml` with a `setting.name`; run another
   ingest without `--setting`; assert default pre-filled in prompt
   input log.

Gate: integration test green; `mkdocs build --strict` green;
`tests/test_docs.py` still passing (env var / config key drift check).
