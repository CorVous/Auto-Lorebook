# Plan 07 — Reading review commands

**Prerequisite:** Plans 01–06.

## Goal

Close out Phase 1's command surface: approve a draft reading,
regenerate from a chosen substage (optionally per-segment), and
inspect readings. After this plan, a human can drive the full
reading stage end-to-end from the CLI.

## In scope

- `approve-reading <source_id>`:
  - Refuses on stale draft (staleness rules from
    `docs/architecture/staleness.md`, pending-artifact tier).
  - Flips `reading_status: draft` → `approved`.
  - Commits the approved `reading.md` into
    `<wiki>/sources/<source_id>/reading.md` alongside the raw
    transcript.
  - Retains `pending/<ingest_id>/reading/structure.yaml` as audit
    artifact.
  - Records inputs-at-approval-time snapshot.
- `regenerate-reading <source_id> --from={structure|summarize}
   [--segments id1,id2,...]`:
  - `--from=structure` → re-runs 1a + 1b, overwrites draft.
  - `--from=summarize` → re-runs 1b (all segments), preserves 1a.
  - `--from=summarize --segments ...` → re-runs 1b for listed
    segments only; other segments' bullets preserved.
  - `--segments` valid only with `--from=summarize` (argparse
    rejects otherwise).
  - Preserves `name_corrections` frontmatter across all regenerate
    modes.
  - Full-reading regenerations discard body hand-edits; per-segment
    regeneration preserves body hand-edits outside the targeted
    segments.
- `readings list` — all sources with their reading status
  (`draft`, `approved`, `not generated`).
- `readings show <source_id>` — prints the current reading path and
  status; non-zero exit if missing.
- Staleness integration: when upstream inputs have changed, the
  error messages from `docs/architecture/staleness.md` §Pending
  tier are produced verbatim (test against documented text).
- Approved reading with stale upstream → warning only, not blocker.

## Out of scope

- `promote-correction` — later phase.
- Stage 2+ commands.

## TDD plan

### Red tests to write first

- `test_approve_refuses_stale_draft` — mutate `structure.yaml`
  after draft is produced; `approve-reading` exits non-zero with
  the documented message.
- `test_approve_flips_status_and_writes_to_wiki`.
- `test_approve_preserves_structure_yaml_as_audit`.
- `test_approve_records_inputs_snapshot`.
- `test_regenerate_from_structure_replaces_both`.
- `test_regenerate_from_summarize_preserves_structure`.
- `test_regenerate_segments_only_touches_listed_segments` — bullets
  in unlisted segments byte-identical before/after.
- `test_regenerate_segments_requires_from_summarize` — argparse
  rejects mismatched combinations.
- `test_regenerate_preserves_name_corrections`.
- `test_regenerate_full_discards_body_edits` —  insert a manual
  edit into the body, full regenerate → edit gone; document this
  behavior matches the reading-stage doc.
- `test_regenerate_segment_preserves_body_edits_outside_targets`.
- `test_approved_reading_warns_on_upstream_change` — mutate
  `.wiki-context.yaml` after approval; reading still readable, but
  a warning is emitted when the reading is next consumed (even if
  no downstream stage exists yet, the warning API is in place).
- `test_readings_list_shows_statuses_across_sources`.
- `test_readings_show_missing_exits_nonzero`.

### Implementation sketch

- `auto_lorebook/commands/approve_reading.py`.
- `auto_lorebook/commands/regenerate_reading.py` — reuses Plan 05/06
  pipeline entry points; wires per-segment 1b driver for
  `--segments`.
- `auto_lorebook/commands/readings.py` — `list` / `show` subcommands.
- `auto_lorebook/staleness.py` — checker invoked by approve and any
  command that reads a pending artifact. Returns a structured
  result (`Fresh | Stale(reason, remedy)`).
- Per-segment 1b driver (internal): surface from Plan 06 hidden
  behind a callable that takes a list of segment IDs.

### Docs touched

- `docs/pipeline/reading.md` — verify behavior of each command
  matches; update the Regenerating substages section if wording
  drifts.
- `docs/architecture/staleness.md` — verify pending-tier and
  approved-tier messages are exactly what the code produces.
- `docs/reference/cli.md` — no manual edit (hook pulls from code),
  but verify rendered output looks right under `mkdocs build`.

## Integration test (plan exit gate)

`tests/integration/test_plan_07_review.py`:

Single end-to-end walk driven by fakes:

1. Ingest (fake yt-dlp) → context (non-interactive flags) →
   generate-reading (fake LLM, both substages) → draft written.
2. `readings list` shows one source in `draft`.
3. Hand-edit `name_corrections` to add one mapping; add a body edit
   inside segment `seg-003`.
4. `regenerate-reading --from=summarize --segments seg-003`. Assert:
   bullets in `seg-003` are the newly-mocked ones; bullets elsewhere
   unchanged; `name_corrections` preserved; body edits in other
   segments preserved.
5. `regenerate-reading --from=summarize` (all). Assert: body edits
   gone; `name_corrections` preserved.
6. Mutate `structure.yaml` on disk → `approve-reading` fails with
   the documented staleness message.
7. Undo the mutation → `approve-reading` succeeds;
   `<wiki>/sources/yt-abc123/reading.md` now exists with
   `reading_status: approved`; `structure.yaml` still in pending.
8. Mutate `.wiki-context.yaml`; re-read the approved reading via
   `readings show`; assert warning emitted but exit 0.

Gate: integration test green; `mkdocs build --strict` green;
`tests/test_docs.py` green.
