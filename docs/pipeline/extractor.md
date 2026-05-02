# Stage 3: Extractor

For each planned claim, the extractor locates the verbatim span in the
raw transcript and produces a proposal with full metadata. No
paraphrasing — extraction only.

> Test this stage in isolation with
> `auto-lorebook seed-ingest --at=plan`, then `replan <sid>` (which
> runs the planner then this extractor). See
> [QA seeding](../contributing.md#qa-seeding).

## Purpose

Narrow by design: locate and snip. Cleanup happens upstream (reading-
stage corrections) and downstream (summarizer prose). Narrowness buys
the mechanical substring guarantee, which a paraphrasing extractor
can't provide.

## Input

- Approved [plan](planner.md), including per-claim `locator_hint`
  ranges.
- Approved [reading](reading.md), including its `name_corrections`
  map.
- Raw transcript, accessed per-proposal via the hint window — not fed
  whole.
- **Reduced preamble** — transcription corrections and entity aliases
  only. See [context pipeline](context.md#preamble-assembly).

## Output

One YAML file per proposed fact at
`pending/<ingest_id>/proposals/<proposal_id>.yaml`:

```yaml
schema_version: 1
proposal_type: new_fact          # new_fact | new_entity_with_facts
target_entity: Aldara
proposed_id: aldara-f004
claim_group_id: cg-001           # shared with siblings routing the same claim elsewhere
claim_group_siblings:            # other targets of the same claim, informational
  - entity: Theron
    proposed_id: theron-f011
  - entity: Second Age
    proposed_id: second-age-f002

text: "Theron's grandfather founded Aldara in the Second Age."
raw_transcript_span: "Fair-on's grandfather founded all-dara in the Second Age."
text_corrects_transcript: true
corrections_applied:
  - from: "Fair-on"
    to: "Theron"
    source: global-transcription-correction
  - from: "all-dara"
    to: "Aldara"
    source: reading-name-correction

source_id: yt-abc123
locator: "0:04:32-0:04:41"
speaker: DM
status: authoritative
status_reason: null
session_date: 2026-01-15
section: founding

reading_section: "[4:30-8:00] Founding of Aldara"
reading_bullet_index: 0

context_before: "So let's talk about the founding of Aldara."
context_after: "And that's why the Theron name matters so much now."
```

## Extraction rules

- `raw_transcript_span` must be a literal substring of the transcript
  between the given timestamps. The tool verifies this after the LLM
  responds; if verification fails, retry or flag.
- `text` differs from `raw_transcript_span` only through applied
  `corrections_applied`. No rewriting, cleanup, or filler removal at
  this stage.
- **Windowed search.** Each proposal's prompt is fed only the
  transcript slice covering its `locator_hint` window, not the whole
  transcript or the whole segment. This is the primary lever keeping
  Stage 3 prompts small and uniform in size across proposals.
- **Fallback on miss.** If substring verification fails within the
  hint window, the extractor retries once with the span widened to
  the full parent segment from 1a. If that also fails, flag with
  `extractor_flagged: true`. A "widened to segment" retry is logged
  on the proposal (`hint_widened: true`) so systematic anchor drift
  in 1b is visible.
- **Hints are advisory; the authoritative locator is produced here.**
  The final `locator` on the proposal is the precise range where the
  span actually lands in the transcript, derived during extraction —
  not copied from `locator_hint`. The hint only narrows the search
  space.
- If the claim cannot be found in a single contiguous span, flag with
  `extractor_flagged: true` and an explanation. Do not synthesize
  across non-adjacent spans.
- Parallelizable: each proposal is independent.

## Claim-group deduplication

Sibling proposals within a `claim_group_id` share the same
`raw_transcript_span`, `locator`, `text`, and `corrections_applied`.
The extractor runs once per claim group — not once per proposal — then
copies the result to each sibling.

Each sibling still gets its own proposal file, its own `proposed_id`,
and its own `target_entity` and `section`; only the span-location
fields are shared. If extraction fails for a claim group, all sibling
proposals inherit the same `extractor_flagged` state.

Next stage: [human fact review](review.md).
