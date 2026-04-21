# Entity model

An entity is a character, location, faction, event, item, or concept
in the wiki. Entity identity lives entirely in entity YAMLs — the
filesystem is the registry; no separate index file.

## Entity YAML schema

`<category>/<slug>.yaml`:

```yaml
schema_version: 1
entity: Aldara
category: locations
slug: aldara
aliases:
  - name: Kingdom of Aldara
    added_by_ingest: ingest-2026-01-16-a
    added_at: 2026-01-16T14:32:11Z
    source: hand-edited          # hand-edited | alias-confirmation | stub-creation | promoted-from-merge
  - name: Aldaran Realm
    added_by_ingest: ingest-2026-01-16-a
    added_at: 2026-01-16T14:47:03Z
    source: alias-confirmation
  - name: the Realm
    added_by_ingest: ingest-2026-02-03-b
    added_at: 2026-02-03T19:14:55Z
    source: alias-confirmation
superseded_by: null              # or "<category>/<slug>" when merged
created_at: 2026-01-16T14:32:11Z
created_by_ingest: ingest-2026-01-16-a
updated_at: 2026-02-03T19:14:55Z

facts:
  - id: aldara-f001
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
    edited_by_human: false
    edited_at: null
    source_id: yt-abc123
    locator: "0:04:32-0:04:41"
    speaker: DM
    status: authoritative       # authoritative | trustworthy | hearsay | disproven
    status_reason: null
    status_history:
      - status: authoritative
        at: 2026-01-16T18:22:47Z
        by: human-review
        reason: null
    session_date: 2026-01-15
    approved_at: 2026-01-16T18:22:47Z
    created_by_ingest: ingest-2026-01-16-a
    claim_group_id: cg-ingest-2026-01-16-a-001
    section: founding
```

## Field semantics

### Entity-level

- **`slug`** — filename stem. Renames are explicit: change `slug`, the
  tool moves the file.
- **`aliases`** — list of records, each
  `{name, added_by_ingest, added_at, source}`. `source` is one of:
    - `hand-edited` — user added directly to the YAML.
    - `alias-confirmation` — approved during a review alias
      sub-prompt.
    - `stub-creation` — accompanied the entity's first approved fact.
    - `promoted-from-merge` — carried over when a superseded entity
      was merged in. `added_by_ingest` is copied from the source
      entity's record, not the merge ingest, to preserve provenance.

    Duplicate names are deduplicated on write, keeping the earliest
    record. Aliases are compared by normalized name (case-insensitive,
    whitespace-trimmed); the record preserves the user's original
    casing.
- **`superseded_by`** — null, or `"<category>/<slug>"` pointing to
  the entity this one was merged into. The planner's entity index
  resolves mentions of this entity (including its aliases) to the
  target. The file stays as a historical record.
- **`created_by_ingest`** — the ingest that first created this entity
  stub (via the first approved fact targeting it). Used for
  `reject-ingest` cleanup and to derive the "created earlier in this
  review session" display note.

### Fact-level

- **`id`** — stable across renames and edits. Assigned at approval.
- **`text`** — current displayed version. Starts as extracted span
  with corrections applied; can be edited by human during review.
- **`raw_transcript_span`** — literal substring of the source
  transcript. Immutable. Evidence.
- **`text_corrects_transcript`** — true if `text` differs from
  `raw_transcript_span` (either through corrections or human edits).
- **`corrections_applied`** — audit trail of substitutions, with
  source (`global-transcription-correction`, `reading-name-correction`,
  or `human-edit`).
- **`source_id`** — foreign key to `sources/<source_id>/info.yaml`.
- **`locator`** — timestamp range in canonical `h:mm:ss-h:mm:ss`
  format for audio/video, or line range for text. See
  [timestamps](timestamps.md).
- **`speaker`** — free-text attribution. Conventions: "DM",
  "Player-Thorin", "Innkeeper NPC", "Narrator".
- **`status`** — epistemic tier:
    - **Authoritative** — stated by the canonical voice (DM
      narration, worldbuilding-video author, notes by the setting's
      author). The setting itself vouches for it.
    - **Trustworthy** — stated within fiction by a source with
      plausible domain knowledge over the claim (a maester on
      heraldry, a priest on their own god's rites, a guild captain
      on guild history). Not canonical voice, but not idle gossip
      either — the source has standing on _this topic_.
    - **Hearsay** — stated within fiction by a source without special
      standing on the claim (tavern rumor, street talk, secondhand
      retelling, NPC speculation outside their expertise).
    - **Disproven** — superseded by a later authoritative fact.

    Domain knowledge is topic-scoped: the same NPC can produce
    `trustworthy` facts on their specialty and `hearsay` facts on
    unrelated subjects. When in doubt between trustworthy and
    hearsay, prefer hearsay — the distinction is meant to elevate
    clear domain authority, not to launder every speaker with a
    title.
- **`status_reason`** — required for `trustworthy`, `hearsay`, and
  `disproven`; free-text. For `trustworthy`, name the domain warrant
  (e.g., "Speaker is the court maester discussing bloodline
  heraldry"). For `hearsay`, note why the source is unreliable. For
  `disproven`, cite the superseding fact.
- **`status_history`** — full log of status changes. Each entry
  carries `status`, `at`, `by` (e.g., `human-review`, `ingest-<id>`,
  `migration`), and `reason`. Append-only.
- **`session_date`** — when the claim entered canon. Can be null.
- **`approved_at`** — when the human approved the fact.
- **`created_by_ingest`** — ID of the ingest session that produced
  this fact. Used to bulk-reject an ingest if needed.
- **`claim_group_id`** — populated when this fact was routed to
  multiple entities from the same claim; null for single-target
  claims. Facts sharing a `claim_group_id` across entity YAMLs share
  the same `raw_transcript_span`, `locator`, `source_id`, and (at
  approval time) `text`. Scoped to the ingest: IDs are formatted
  `cg-<ingest_id>-NNN` so a group ID is globally unique without a
  separate registry.
- **`section`** — organizational bucket within the entity page
  (founding, government, legends, etc.). Free-text; the
  [summarizer](../pipeline/summarizer.md) normalizes case and trims
  whitespace when grouping.

## Entity index

The filesystem is the source of truth. An entity exists iff
`<category>/<slug>.yaml` exists. Canonical name, aliases, category,
and merge status all live in the entity YAML.

The planner builds an in-memory index from entity YAMLs at the start
of each command that needs one. The review loop refreshes the index
after each approval so that entities created earlier in a review
session are visible to later proposals in the same session. At small
scale (hundreds of entities) this is fast; if it becomes slow, the
tool may cache the index at `.cache/entity-index.json`. The cache is
never authoritative — it is rebuilt from YAMLs whenever any entity
YAML changes.

## Global transcription corrections

`.transcription-corrections.yaml` at the wiki root:

```yaml
schema_version: 1
corrections:
  - from: "Fair-on"
    to: "Theron"
    first_seen_in: yt-abc123     # source where this was first caught
    also_seen_in:
      - yt-def456
      - yt-ghi789
    promoted_at: 2026-01-18T10:04:21Z
    notes: "YouTube auto-captions consistently mishear this."
```

These are phonetic mishearings that apply across all sources. Distinct
from entity aliases (semantic, in-world) and from per-source
`name_corrections` in reading frontmatter (local to one source).

### Application

- **Reading stage** — the tool applies corrections as literal
  substitutions to the transcript before the LLM sees it in 1a, and
  includes them in every substage's preamble as an explicit
  instruction. Per-source `name_corrections` stack on top; per-source
  wins on conflict.
- **Extractor stage** — applies the union of global corrections and
  approved reading's `name_corrections` when producing `text` from
  `raw_transcript_span`. Each substitution is logged in
  `corrections_applied`.
- **Planner stage** — works from the corrected reading, so transcript
  corrections don't apply directly. Entity index matching handles
  aliases separately.

### Promotion

Corrections that recur across readings can be promoted from per-source
frontmatter to the global file:

```bash
auto-lorebook promote-correction "<from>" "<to>"
```

Per-source entries remain in reading frontmatter after promotion as
historical record; application code uses the union, so no duplication
issue. When a correction is promoted, `first_seen_in` is set to the
earliest source containing it; subsequent promotions of the same pair
append to `also_seen_in`.
