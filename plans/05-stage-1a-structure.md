# Plan 05 — Stage 1a structure + gap check

**Prerequisite:** Plans 01–04.

## Goal

Run Stage 1a end-to-end: LLM call with the real structure prompt,
`structure.yaml` output, mechanical validation, and the deterministic
gap-check warning. Still driven by a fake OpenRouter client in tests.

## In scope

- Stage 1a prompt template assembled with the full preamble plus the
  structure-task instructions (segmentation + attribution in one pass,
  uncertainty flags, sub-segment overrides).
- LLM response parser → `structure.yaml`:
  - `schema_version`, `source_id`, `generated_at`, `default_speaker`,
    `segments[]`, `uncertainty_flags[]`, `inputs` block with all
    hashes from `docs/architecture/staleness.md`.
- Mechanical validation (hard failures; tool does not ship a broken
  `structure.yaml`):
  - Every segment start/end maps to a real transcript timestamp
    (within SRT cue boundaries, tolerating ±1 cue for rounding).
  - Segments cover the full transcript with no gaps.
  - Sub-segment overrides fall within their parent segment.
  - Uncertainty-flag locators fall within some segment.
- Mechanical gap check (soft; warning only):
  - Heuristic over segment titles + notes matching configurable
    patterns ("rules discussion", "break", "off-topic", "silence",
    etc.) and/or explicit low-yield hints in `notes`.
  - Contiguous stretch ≥ threshold (default 5 min, configurable).
  - Produces a structured warning object ready for later
    surfacing during reading review.
- Canonical timestamp writer (`h:mm:ss`); lenient reader accepts
  `m:ss` and `hh:mm:ss`.
- `generate-reading <source_id>` now runs 1a and stops before 1b.

## Out of scope

- Stage 1b — Plan 06.
- Surfacing warnings in `reading.md` UI — Plan 06 (rendering).
- Regenerate paths — Plan 07.

## TDD plan

### Red tests to write first

- `test_structure_validation_rejects_gap` — LLM output with a gap
  between two segments → validation error names the gap range.
- `test_structure_validation_rejects_nonexistent_timestamps` —
  timestamps falling outside the SRT duration or not aligned with
  any cue → error.
- `test_structure_validation_accepts_override_within_parent`.
- `test_structure_validation_rejects_override_outside_parent`.
- `test_uncertainty_flag_locator_must_be_in_segment`.
- `test_canonical_timestamp_writer` — `4:32` → `0:04:32`; `3665`s →
  `1:01:05`.
- `test_lenient_timestamp_reader` — accepts both short and full
  forms.
- `test_gap_check_fires_on_long_low_yield_stretch` — fixture
  structure with a 15-min stretch of "Rules discussion" / "Break" →
  warning produced, actionable message includes the range.
- `test_gap_check_silent_on_mixed_content` — same fixture with a
  "Founding of Aldara" segment interrupting the low-yield stretch →
  no warning.
- `test_structure_yaml_has_inputs_block` — all documented hashes
  present and stable across repeated runs with identical inputs.

### Implementation sketch

- `auto_lorebook/pipeline/stage_1a.py` — prompt build, call, parse.
- `auto_lorebook/pipeline/validation.py` — mechanical checks, shared
  with later stages where appropriate.
- `auto_lorebook/pipeline/gap_check.py` — heuristic pattern list
  (tunable via config later; hard-coded defaults for now).
- `auto_lorebook/timestamps.py` — canonical writer + lenient reader.
- `auto_lorebook/hashing.py` — input SHA-256 + canonical entity-index
  serialization per staleness doc.
- Fixture LLM responses under
  `tests/support/fixtures/readings/1a_*.json`.

### Docs touched

- `docs/pipeline/reading.md` — verify `structure.yaml` example
  matches the emitted shape; adjust if fields drift.
- `docs/architecture/staleness.md` — confirm the `inputs` block keys
  Plan 05 actually writes match the documented table row for
  `structure.yaml`.
- `docs/architecture/timestamps.md` — verify canonical/lenient
  behavior matches implementation.

## Integration test (plan exit gate)

`tests/integration/test_plan_05_structure.py`:

1. Seed a ~20-minute fixture transcript (enough cues to exercise
   segmentation) under `tests/support/fixtures/srt/aether_short.srt`.
2. Prime the fake OpenRouter client with a canned 1a response whose
   segments cover the full transcript and include one deliberate
   low-yield stretch spanning ≥ 5 minutes.
3. Run `generate-reading yt-fixture`.
4. Assert:
   - `pending/<ingest_id>/reading/structure.yaml` exists and parses.
   - Mechanical validation passes.
   - Gap check returns exactly one warning naming the seeded range.
   - `inputs` block contains the expected keys and stable hashes.
5. Negative case: re-prime the fake client with a response containing
   a 90-second gap; assert the command exits non-zero with the gap
   error message.

Gate: integration test green; `mkdocs build --strict` green;
`tests/test_docs.py` green.
