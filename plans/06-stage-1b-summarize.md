# Plan 06 — Stage 1b summarize + reading assembly

**Prerequisite:** Plans 01–05.

## Goal

Run Stage 1b per-segment, assemble the draft `reading.md`, and
surface the gap-check warning inline. Output is the artifact the
human reviews.

## In scope

- Stage 1b prompt template (per-segment, full preamble + segment
  context + segment transcript window).
- Per-segment claim extraction:
  - Runs in parallel across segments (use `trio` nursery or
    thread pool — pick one and stay consistent).
  - Each call produces a list of `{bullet_index, text, anchor,
    locator_hint}` entries; empty list permitted and common.
  - `locator_hint` is the anchor ± configurable pad (default 15 s),
    clamped to the parent segment boundaries.
- Per-segment 1b output is persisted internally (in `structure.yaml`
  or a sidecar under `pending/<ingest_id>/reading/`) so per-segment
  regeneration (Plan 07) doesn't have to re-serialize the whole
  structure.
- Draft `reading.md` assembler:
  - Frontmatter matches the example in `docs/pipeline/reading.md`
    (`schema_version`, `source_id`, `source_name`, `source_url`,
    `source_type`, `session_date`, `ingested_at`, `reading_status:
    draft`, `default_speaker`, empty `name_corrections: {}`).
  - Section per segment: `## [[start-end]](url&t=...) Title`,
    `Speaker:` line, then bullets (or `_No claims extracted from
    this segment._`).
  - Bullets with clickable per-bullet anchors.
  - Uncertainty flags from 1a rendered inline as human-resolvable
    markers.
  - Gap-check warning rendered as a collapsible/inline block near
    the top so the reviewer sees it before scanning.
- Clickable-timestamp post-processing:
  - LLM emits plain `[0:04:32]` in bullet text; post-processor
    attaches URL with correct `&t=` seconds.
  - Idempotent — re-running on an already-linked doc is a no-op.
- `name_corrections` frontmatter rendering path (empty map by
  default; rendering applies substitutions when the map is non-empty
  — but the *map itself* is owned by the human editor, so the
  generator just writes the empty map).
- `inputs` block on the draft `reading.md` with the hashes specified
  in the staleness doc.

## Out of scope

- `approve-reading`, `regenerate-reading`, review commands — Plan 07.
- Promoting name corrections to the global file — later phase.

## TDD plan

### Red tests to write first

- `test_1b_parallel_segments_independent` — fake client records which
  segments it saw; assert each segment prompted once and in parallel
  (order-independent outputs reassemble deterministically).
- `test_1b_empty_bullets_permitted` — fake 1b returns `[]` for a
  segment; assembler emits `_No claims extracted from this segment._`.
- `test_locator_hint_padded_and_clamped` — anchor near segment
  boundary → hint clamped to segment; interior anchor → ±15 s.
- `test_reading_md_frontmatter_shape` — exact keys, order, default
  values match `docs/pipeline/reading.md`.
- `test_clickable_timestamps_youtube` — `[0:04:32]` in a YouTube
  source → `[[0:04:32]](https://youtube.com/watch?v=...&t=272)`.
- `test_clickable_timestamps_idempotent`.
- `test_name_corrections_map_empty_by_default`.
- `test_uncertainty_flags_rendered_inline`.
- `test_gap_warning_surfaced_in_reading_md`.
- `test_reading_inputs_block_includes_structure_hash`.

### Implementation sketch

- `auto_lorebook/pipeline/stage_1b.py` — per-segment call + parallel
  driver.
- `auto_lorebook/pipeline/reading_writer.py` — frontmatter +
  section assembler; pure function over (structure, bullets,
  warnings) tuple.
- `auto_lorebook/timestamps.py` — extend with clickable-link
  post-processor.
- Extend `generate-reading` to run 1a → gap check → 1b → assemble →
  write `pending/<ingest_id>/reading/reading.md`.

### Docs touched

- `docs/pipeline/reading.md` — verify the rendered example matches
  the assembler's actual output byte-for-byte (update example if
  formatting tightens).
- `docs/architecture/staleness.md` — verify the `reading.md (draft)`
  row in the dependency table matches the hashes actually recorded.
- `docs/architecture/timestamps.md` — verify clickable link form
  matches.

## Integration test (plan exit gate)

`tests/integration/test_plan_06_reading.py`:

1. Reuse the Plan 05 fixture transcript and 1a canned response.
2. Prime the fake client with 1b responses per segment — one of
   them deliberately empty (to prove the "no claims" path), one
   with three bullets whose anchors span the segment.
3. Run `generate-reading yt-fixture`.
4. Assert:
   - `reading.md` exists with the documented frontmatter.
   - Segment with empty 1b response has the "No claims extracted"
     marker.
   - Bulleted segment has three clickable bullets with correct
     `&t=` seconds.
   - Gap-check warning from Plan 05 surfaces near the top.
   - Re-running the clickable-link post-processor is a no-op
     (content hash unchanged).
   - `inputs` block includes the `structure.yaml` hash.

Gate: integration test green; `mkdocs build --strict` green;
`tests/test_docs.py` green.
