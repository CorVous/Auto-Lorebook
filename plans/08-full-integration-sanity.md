# Plan 08 — Full integration & sanity check

**Prerequisite:** Plans 01–07 all merged and green.

## Goal

Prove Phase 1's exit criterion on real content, surface any drift
between docs and code, and confirm the human review budget is
realistic. This is a validation plan, not a feature plan — if it
uncovers bugs, open focused follow-ups; if it uncovers doc drift,
fix in this same PR.

## Phase 1 exit criterion (verbatim from roadmap)

> Ingest a real actual-play VOD, fill in context, run both substages,
> review the combined reading, approve. The reading covers the full
> transcript (no gaps); segments with no claims are visibly empty
> rather than absent; at least one off-topic segment is correctly
> rendered with an empty bullet list; the mechanical gap check fires
> at least once on real content and the warning is actionable. Total
> human review time for a two-hour source fits inside the
> 10–20 min/hour target on a representative session.

## Part A — Scripted end-to-end (automated)

`tests/integration/test_plan_08_e2e.py` — no network; largest fake
content we have.

1. Load a ≥ 1-hour recorded YouTube fixture (SRT + yt-dlp JSON) in
   `tests/support/fixtures/ytdlp/long_session/`. If one doesn't
   exist yet, record one as part of this plan (one-time manual
   capture of a real public VOD's captions; store checksums).
2. Prime the fake OpenRouter client with real-shape 1a and 1b
   responses (captured once from a real model run against this
   fixture during development and stored as JSON). These are
   **canned but realistic** — not hand-authored to pass the test.
3. Run: `ingest` → `generate-reading` → (no hand edits) →
   `approve-reading`.
4. Assert against the full-coverage + empty-segment + gap-check
   conditions:
   - `structure.yaml` segments cover the transcript with zero gaps.
   - At least one segment in `reading.md` renders the "No claims
     extracted" marker.
   - At least one gap-check warning is produced and its message
     names a concrete transcript range (not a placeholder).
   - `reading.md` frontmatter has `reading_status: approved` after
     approval.
   - `<wiki>/sources/yt-*/reading.md` now exists alongside the raw
     transcript.

## Part B — Manual one-shot (gated checklist)

A human runs this against a **real** actual-play VOD and an
OpenRouter API key. Record the results in
`plans/08-runbook-results.md` (new file created during this plan).

Steps:

1. Fresh `AUTO_LOREBOOK_HOME`; empty `.wiki-context.yaml`.
2. Populate `.wiki-context.yaml` with setting name, naming
   conventions, and 2–3 `recurring_speakers`.
3. `auto-lorebook ingest <real URL>` → interactive context.
4. `auto-lorebook generate-reading <source_id>` → observe the
   substages run, gap-check warning (or absence) printed.
5. Open `pending/<ingest_id>/reading/reading.md`. Time the review
   with a stopwatch. Add `name_corrections` entries as needed.
6. `auto-lorebook approve-reading <source_id>`.

Acceptance checklist (all must be "yes"):

- [ ] Full transcript covered; no visible gaps between segments.
- [ ] At least one off-topic / low-yield segment rendered with an
      empty bullet list (not missing).
- [ ] Mechanical gap check fired at least once with a message that
      named a real low-yield stretch (actionable — not false alarm
      on the whole source).
- [ ] Human review time ≤ 20 min × hours of footage.
- [ ] `name_corrections` flow worked as designed (no mass
      find/replace required).
- [ ] `approve-reading` committed the reading to
      `<wiki>/sources/<source_id>/reading.md`.
- [ ] `sources/<source_id>/info.yaml` has the full context block;
      nothing unexpected under the wiki root.

If any item fails: open a focused issue or PR against the relevant
plan; this plan reopens until all boxes are ticked.

## Part C — Sanity sweep

Run and fix before closing:

1. `uv run ruff check`
2. `uv run ruff format --check`
3. `uv run ty check`
4. `uv run pytest` (includes `tests/test_docs.py`)
5. `uv run mkdocs build --strict`
6. Roadmap update: flip Phase 1's status line in
   `docs/roadmap/index.md` from "next target" to "complete"; add a
   one-line pointer to `plans/08-runbook-results.md` under Phase 1
   for future readers.
7. Delete `.auto-lorebook/pending/` test residue from any developer
   machines used for Part B (runbook results file is what persists,
   not the ingest state).

## Part D — Known non-goals to reconfirm

Document in `plans/08-runbook-results.md` that the following are
explicitly **not** tested in Phase 1, per the roadmap, and belong to
later phases:

- Entity index populated from real entity YAMLs.
- Planner, extractor, fact review.
- Summarizer regeneration.
- Web UI.

## Exit criterion for this plan

Part A integration test green in CI; Part B checklist fully ticked
by at least one human operator with the results file committed; Part
C sanity sweep clean; roadmap updated.
