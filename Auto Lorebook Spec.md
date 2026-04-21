# Auto-Lorebook Specification

A CLI tool that ingests fantasy worldbuilding content (primarily YouTube transcripts) and produces a citation-backed markdown wiki. Every claim in the wiki traces to a specific timestamped moment in a source, reviewed and approved by a human.

## Goals

- **Trustworthy citations.** Every fact in the wiki links to a specific, verifiable moment in a source. Clicking a citation opens the source at that moment.
- **Human judgment at the right points.** The LLM does mechanical work (parsing, locating, routing). The human makes judgment calls (what was actually said, what status it has, whether an entity exists).
- **No silent decisions.** Every addition to the wiki is an explicit human approval.
- **Compounding corrections.** Name corrections and entity aliases accumulate across ingests, so the tool gets better as the wiki grows.

## Non-goals

- Automatic transcription of audio (relies on YouTube’s existing transcripts)
- Real-time or collaborative editing
- Cross-source synthesis beyond what a human approves

## Architecture overview

```
YouTube URL / SRT / text file
        │
        ▼
  Fetch & store source
        │
        ▼
  [Human context step]  fill in info.yaml
        │
        ▼
  Assemble prompt preamble
        │
        ▼
  Stage 1a: Structure (LLM)    segment + attribute
        │
        ▼
  Mechanical gap check         warning only
        │
        ▼
  Stage 1b: Summarize (LLM)    per-segment claim bullets
        │
        ▼
  [Human review gate]  approve reading
        │
        ▼
  Stage 2: Planner (LLM)       route claims → entities
        │                      (new entities exist only on the plan)
        ▼
  Stage 3: Extractor (LLM)     locate verbatim spans
        │                      (no gate between planner and extractor)
        ▼
  [Human review gate]  per-fact approve / edit / reject
        │              (first approval creates the entity stub;
        │               `replan` is the escape hatch)
        ▼
  Stage 4: Summarizer (LLM)    regenerate entity prose
```

## Schema versioning

Every YAML file produced by the tool carries a top-level `schema_version` field as its first key, so that future changes to file shapes can be detected and migrated rather than silently misread.

```yaml
schema_version: 1
# ...rest of the file
```

This applies to every YAML artifact the tool writes:

- `sources/<source_id>/info.yaml`
- `pending/<ingest_id>/reading/structure.yaml`
- `pending/<ingest_id>/plan.yaml`
- `pending/<ingest_id>/proposals/<proposal_id>.yaml`
- `<category>/<slug>.yaml` (entity YAMLs)
- `~/.auto-lorebook/config.yaml`

Hand-maintained files (`.wiki-context.yaml`, `.transcription-corrections.yaml`) also carry `schema_version`; the tool writes it when it first touches an empty or missing file, and preserves it on any write-back (e.g., `promote-correction`). A missing `schema_version` on a hand-maintained file is read as `schema_version: 1` with a warning suggesting the user add it.

Markdown artifacts with YAML frontmatter (`reading.md`, entity `.md` summaries) carry `schema_version` in their frontmatter.

**Versioning rules:**

- `schema_version` is a positive integer, starting at 1 for the MVP. No semver, no dates — one monotonically increasing number per file type.
- Each file type versions independently. Bumping `info.yaml`'s schema does not bump `plan.yaml`'s.
- The tool refuses to read a file whose `schema_version` is greater than any version it knows about, and names the remedy (upgrade the tool).
- When a file's schema changes, the tool includes a migration path from the immediately-previous version. Migrations run lazily on first read, write back the upgraded form, and log what was migrated. The tool does not support skipping versions; upgrading from version N to N+2 runs N→N+1 then N+1→N+2.
- Missing `schema_version` on a tool-produced file is treated as a corruption signal (the tool always writes it); the read fails loudly rather than defaulting.

The `schema_version` field is excluded from the input hashes used for staleness detection — bumping a schema version should not invalidate every downstream artifact in the wiki. Migrations are orthogonal to staleness.

## Artifact dependencies and staleness

Every generated artifact in the pipeline records the inputs that produced it, so the tool can detect when an artifact has gone stale (an input has changed since the artifact was produced) and surface the right remedy. Without this, editing an approved reading after planning, or changing `.wiki-context.yaml` mid-review, produces silent inconsistency between artifacts the tool treats as coherent.

### Input hashes on artifacts

Every LLM-generated artifact carries an `inputs` block recording a SHA-256 of each file-level input plus the model identity:

```yaml
inputs:
  transcript_sha256: a1b2c3...           # sources/<id>/transcript.en.srt
  info_sha256: d4e5f6...                 # sources/<id>/info.yaml
  wiki_context_sha256: 7g8h9i...         # .wiki-context.yaml
  corrections_sha256: jk0lm1...          # .transcription-corrections.yaml
  entity_index_sha256: n2o3p4...         # canonical serialization of the in-memory index
  preamble_sha256: q5r6s7...             # the fully-assembled preamble string
  model: openrouter/anthropic/claude-sonnet-4
  model_params_sha256: t8u9v0...         # temperature, max_tokens, etc.
generated_at: 2026-04-20T14:32:00Z
```

Hashes are over the raw file bytes. Whitespace-only edits will register as changes and trigger regeneration; this is intentional — the cost of a spurious regenerate is low, and canonicalization bugs are a real risk. Hashes are recorded on `structure.yaml`, `reading.md` (draft and approved), `plan.yaml`, each `proposals/*.yaml`, and on entity facts at approval time (snapshotting the inputs as they were when the fact was extracted).

The entity index is in-memory, not a file. Its hash is computed over a canonical serialization: entries sorted by category then slug, each entry rendered as `{canonical_name, category, aliases (alias names sorted, provenance fields excluded), superseded_by}`, emitted as a single normalized YAML string which is then hashed. Alias provenance is deliberately excluded from the hash — when a name becomes an alias is metadata; whether it is an alias is what matters to downstream stages.

### Dependency table

|Artifact|Invalidated when any of these change|
|---|---|
|`structure.yaml`|transcript, `info.yaml`, `.wiki-context.yaml`, `.transcription-corrections.yaml`, entity index, model, model params|
|`reading.md` (draft)|everything above, plus `structure.yaml`|
|`reading.md` (approved)|same as draft (but staleness is a warning, not a blocker — see below)|
|`plan.yaml`|approved `reading.md`, `.wiki-context.yaml`, entity index, model, model params|
|`proposals/*.yaml`|`plan.yaml`, approved `reading.md`, transcript, `.transcription-corrections.yaml`, model, model params|
|entity `.md` (summary)|entity YAML, entity index|

The preamble hash is derived from several of these inputs; it's recorded on the artifact for debugging but the individual input hashes are what determine staleness.

### Entity index: session-scoped, not globally consistent

The entity index changes whenever any entity YAML is written — including during fact review, when approving a proposal for a new entity creates a stub. Strict invalidation would mean every approval during review stales all other pending artifacts in every in-flight ingest across the wiki, which is hostile to normal use.

Instead, the entity index hash on a pending artifact is compared against the index as it was _at the start of the current stage's run_, not the live index. Within a single review session, entities created earlier in the session do not invalidate proposals generated earlier in the session — consistent with the existing "in-memory entity index refresh after each approval" behavior, which treats the index as a running context rather than a snapshot. Staleness detection against the index fires only across session boundaries, or when another ingest has written entity YAMLs that the current ingest's planner didn't see.

### How staleness surfaces

Three tiers by pipeline position:

**Pending artifacts (unapproved).** The tool refuses to consume a stale artifact and names the remedy. Examples:

```
$ auto-lorebook review ingest-2026-04-20-a
✗ plan.yaml is stale: approved reading.md has changed since planning.
  Run: auto-lorebook replan ingest-2026-04-20-a
```

```
$ auto-lorebook approve-reading yt-abc123
✗ reading.md is stale: structure.yaml has changed since this reading was generated.
  Run: auto-lorebook regenerate-reading yt-abc123 --from=summarize
```

The refusal names the specific input that changed, so the user knows which regenerate command to run and at which `--from` point.

**Approved reading with stale upstream.** Warning, not blocker. The reading approval is a human commitment; upstream edits (a new `.wiki-context.yaml`, new entries in `.transcription-corrections.yaml`) do not silently revoke it. The warning appears when downstream stages run against the approved reading:

```
⚠ Approved reading for yt-abc123 was generated against an older .wiki-context.yaml.
  Downstream stages will use the current version.
  To regenerate the reading against current context: auto-lorebook regenerate-reading yt-abc123 --from=structure
```

**Approved facts (entity YAMLs).** No warning at read time — approved content is past the gate. The `inputs` snapshot on each fact is an audit artifact: it supports queries like "which facts were extracted against a `.transcription-corrections.yaml` predating correction X?" when the user wants to decide whether to re-examine historical approvals. No command uses staleness of an approved fact to gate behavior.

### Integration with existing commands

- `regenerate-reading` and `replan` are the remedies the tool suggests when pending artifacts are stale. Both write a fresh `inputs` block on their outputs.
- `approve-reading` records the inputs-at-approval-time on the approved reading, which becomes the reference point for downstream stages.
- `reject-ingest` is unaffected — it works by `created_by_ingest`, independent of hashes.
- `wiki rebuild` optimizes: it skips regeneration for entity `.md` files whose recorded inputs match the current entity YAML and index. `wiki rebuild --force` regenerates unconditionally.
- `replan` preserves approved proposals as facts in entity YAMLs, with their original `inputs` snapshots intact. Re-planning does not retroactively "update" historical facts to reflect the new input state — that would discard the audit trail.

### What this does not catch

Model non-determinism: identical inputs can produce different outputs on re-run. Hashes detect input changes, not output equivalence. If the user wants to force regeneration despite matching hashes, `regenerate-reading` and `replan` run unconditionally; hash-matching only suppresses automatic staleness warnings, never overrides an explicit regenerate command.

Manual edits to intermediate YAML artifacts (`structure.yaml`, `plan.yaml`) will not change the recorded `inputs` block, so downstream stages won't detect the edit as staleness. Intermediate artifacts are not intended as hand-edit surfaces — the designated hand-edit surfaces are `reading.md` before approval and entity YAMLs after approval. Users who hand-edit intermediate artifacts are on their own; this is consistent with the existing design.

## Repository layout

The tool operates on two separate locations:

**The wiki repo** (any directory the user points the tool at):

```
<wiki-repo>/
  .transcription-corrections.yaml   # global phonetic/mishearing fixes
  .wiki-context.yaml                # setting info, conventions, defaults
  sources/
    <source_id>/
      transcript.en.srt             # raw transcript, untouched
      info.yaml                     # url, title, duration, caption_type, context
      reading.md                    # corrected reading (after approval)
  characters/
    <slug>.yaml                     # canonical name, slug, aliases, facts
    <slug>.md                       # summary (regenerated view)
  locations/
  factions/
  events/
  items/
  concepts/
  index.md                          # auto-generated table of contents
```

The wiki filesystem only ever reflects human-approved state; pending work lives in the tool’s state directory below. Entity identity lives entirely in entity YAMLs — see “Entity index.”

**The tool’s state directory** (`~/.auto-lorebook/`):

```
~/.auto-lorebook/
  config.yaml                       # model selection, wiki repo path
  pending/
    <ingest_id>/
      reading/                      # intermediate reading artifacts
        structure.yaml              # Stage 1a output (segments + attribution)
        reading.md                  # Stage 1b output (draft)
      plan.yaml                     # planner output
      proposals/
        <proposal_id>.yaml          # one per proposed fact
```

Pending state persists across sessions. An ingest can be started, paused mid-review, and resumed.

## Source ingestion

### Supported inputs

- YouTube URL → tool fetches transcript via `yt-dlp`
- Local SRT file + `--source-url` flag → tool uses the file as the transcript
- Plain text or markdown file → tool treats the file as the raw source

### YouTube fetch behavior

Uses `yt-dlp` as a subprocess. Prefers manual captions over auto-generated. Captures only:

- Transcript (SRT format)
- Title
- Duration

Does **not** capture upload date. Session dates are manual — upload date and session date can diverge significantly for recorded worldbuilding content.

Reports to the user which caption type was retrieved:

```
✓ Manual subtitles available (English)
```

or

```
⚠ Auto-generated subtitles only. Expect transcription errors in
  proper nouns; add name corrections during reading review.
```

### Source ID

Derived from the source type:

- YouTube: `yt-<video_id>` (e.g., `yt-abc123`)
- Local SRT: `srt-<short_content_hash>` or user-provided via `--source-id`
- Text file: `txt-<short_content_hash>`

`<short_content_hash>` is the first 10 hex characters of the SHA-256 of the raw file bytes. Hashing content rather than path means the same file at different paths (moved, renamed, copied to a new location) resolves to the same source ID. Whitespace-only changes produce a new ID — intentional, since the transcript is what every downstream artifact is hashed against and "same content modulo whitespace" is not a property the tool tries to preserve.

Collision probability at 10 hex chars (40 bits) is negligible for expected wiki sizes; if two distinct source files ever collide, `--source-id` provides an explicit override.

Stable IDs mean re-ingesting the same source produces no duplicates; the tool detects and refuses. For local sources, "same source" is now defined by content equality, so the refusal correctly catches re-ingests of a moved or renamed file — not just a re-ingest of the exact same path.

### `info.yaml` structure

```yaml
schema_version: 1
source_id: yt-abc123
source_type: youtube
source_url: https://youtube.com/watch?v=abc123
title: "Aether Chronicles S3E14"
duration_seconds: 8432
caption_type: manual           # manual | auto-generated | n/a
fetched_at: 2026-04-20T09:15:42Z
session_date: null             # manual; null until filled in

context:
  perspective: "Stream from Cor's perspective roleplaying as Kiki"
  source_nature: actual-play   # actual-play | dm-lore | worldbuilding-video | interview | notes | other
  setting: Aether Chronicles
  speakers:                    # optional; for one-off speakers not in .wiki-context.yaml's recurring_speakers
    - name: Finn
      role: guest-player
      character: Brannoc
  notes: "Picks up mid-session after a long rest in the Dusk Marches."
```

All `context` fields are optional; blank is allowed. Fields left unfilled reduce LLM quality but don’t block the pipeline.

The URL is the backbone of the citation system. All downstream stages reference source URLs via `source_id` lookup.

## Context gathering

LLM output quality depends heavily on context that can’t be inferred from a transcript alone. The pipeline accumulates context from two sources: per-ingest (`info.yaml`) and wiki-wide (`.wiki-context.yaml`).

### Per-source context (`info.yaml`)

Defined above. Captured at ingest time via interactive prompting (default), CLI flags, or direct editing of `info.yaml`. See “Context step in the ingest flow” below for the interactive flow and flag handling.

### Wiki-level context (`.wiki-context.yaml`)

Persistent across all ingests. Hand-maintained by the user.

```yaml
schema_version: 1
setting:
  name: Aether Chronicles
  description: |
    A high-fantasy setting with aetheric magic and pre-industrial
    technology. Main continent is the Western Kingdoms. Timeline
    divided into five Ages.

naming_conventions: |
  - Characters referred to by first name in-narrative
  - Locations use definite article ("the Dusk Marches")
  - Proper nouns capitalized

interpretation_defaults: |
  - DM-narrated content is authoritative
  - In-character speech by players is hearsay
  - NPC statements voiced by DM are hearsay unless explicitly confirmed
  - NPCs speaking on their own domain (a maester on heraldry, a priest
    on their god's rites) are trustworthy; record the warrant in
    status_reason

recurring_speakers:
  - name: Cor
    role: player
    usual_character: Kiki
  - name: Jess
    role: DM
```

All fields optional. An empty `.wiki-context.yaml` is fine; the tool degrades gracefully.

### Prompt preamble assembly

Before invoking any of the LLM stages (reading substages, planner, extractor), the tool assembles a context preamble. Preamble assembly is deterministic — no LLM involvement.

Contents, in order:

- Per-source context from `info.yaml` (perspective, source_nature, session_date, speakers, notes)
- Setting context from `.wiki-context.yaml` (name, description, naming conventions, interpretation defaults)
- Global transcription corrections from `.transcription-corrections.yaml`
- **Complete entity index**: every entity in the wiki, listed as canonical name + category + aliases. Built in-memory by scanning all `<category>/<slug>.yaml` files at command start. No truncation.

Stage-specific task instructions follow the preamble. The reading substages (1a and 1b) and the planner receive the full preamble and run on the primary model. The extractor receives a reduced preamble (transcription corrections and entity aliases only) — it doesn’t need setting lore or interpretation defaults, and narrower context reduces the risk of the extractor “improving” text beyond literal correction.

**Token budget:** if the assembled preamble would exceed a configurable fraction of the model’s context window (default: 80%, leaving room for the transcript and response), the tool fails with a clear error naming the oversized component and suggesting remedies: (a) switching to a larger-context model in `config.yaml`, (b) trimming the named component (e.g., `.wiki-context.yaml`, transcription corrections), or (c) enabling retrieval mode for the entity index (deferred; see below). The tool does not silently truncate.

Example skeleton:

```
## Context for this source

<assembled from info.yaml: perspective, source_nature, session_date,
speakers, notes>

## Setting context

<assembled from .wiki-context.yaml: setting name, description,
naming conventions, interpretation defaults>

## Known transcription corrections

<from .transcription-corrections.yaml>

## Entities in this wiki

Characters:
  - Theron (aliases: King Theron, Theron IV)
  - Aelindra
  - Marcus
Locations:
  - Aldara (aliases: Kingdom of Aldara, Aldaran Realm, the Realm)
  - Valoria
  - Dusk Marches
Events:
  - War of the Dusk

## Your task

<stage-specific prompt>
```

### Context step in the ingest flow

After fetching the source, the tool prompts interactively for context. Every field is skippable (Enter to skip). Defaults pre-fill from flags, then `.wiki-context.yaml` (e.g. `setting.name`), then `~/.auto-lorebook/last-context.yaml` (perspective and source_nature from the most recent ingest), in that priority order. Defaults show as bracketed text; Enter accepts.

```
Source stored as yt-abc123.
  Title: Aether Chronicles S3E14
  Duration: 2:20:32
  Captions: manual (English)

Let's add some context for this source. Press Enter to skip any field.

Session date (YYYY-MM-DD): 2026-01-15
Perspective (e.g. "Cor playing Kiki"): Cor playing Kiki in Aether Chronicles
Source nature [actual-play/dm-lore/worldbuilding-video/interview/notes/other]: actual-play
Setting [Aether Chronicles]:
Any notes? (one line, or Enter to skip): Picks up mid-session after a long rest.

Context saved to sources/yt-abc123/info.yaml.

Generate reading now? [Y/n]: y
Generating reading (segment → attribute → summarize)...
```

Speakers are not prompted for at ingest time. They’re defined once in `.wiki-context.yaml` as `recurring_speakers` and reused across all sources. Per-source speaker variation (guest players, one-off NPCs) can be added to `info.yaml` manually if needed.

After context is captured, the tool offers to run the reading pipeline immediately. Declining leaves the source at “context captured, reading not yet generated”; the user runs `auto-lorebook generate-reading <source_id>` manually later.

### Flags, non-interactive, and edge cases

Flags override their corresponding prompts and skip them entirely. If every field is supplied by flags, the interactive step is skipped altogether. `--no-interactive` skips all prompts and uses only flag values, leaving the rest blank — the only non-interactive flag.

- **Non-interactive environments** (no TTY, piped stdin, CI): detected automatically; falls back to `--no-interactive` behavior with a notice.
- **User aborts mid-prompt** (Ctrl-C): partially-captured context is saved to `info.yaml`; the tool prints where and exits cleanly.
- **Invalid input**: re-prompts with a hint (“Expected YYYY-MM-DD, got ‘yesterday’”). Enter still skips.

To re-run the prompts for an existing source (filling in skipped fields, correcting mistakes):

```bash
auto-lorebook configure-context <source_id>
```

## Stage 1: Reading

The reading stage runs two LLM substages in sequence. 1a (structure) segments the transcript and attributes speakers in a single pass. 1b (summarize) produces claim bullets per segment. The human reviews the combined output as a single reading (one review gate).

### Design drivers

Two properties of the intended use dominate the design of this stage:

1. **The human reviews claims, not transcript.** A two-hour actual-play VOD at a realistic review budget (10–20 minutes per hour of footage) means the human cannot read the full transcript. The review surface is claim bullets with localized timestamps and context windows; everything else is scaffolding for producing good bullets.
2. **Missed claims are worse than spurious ones.** An omitted claim in a one-shot ingest is a permanent gap nothing downstream will surface. A spurious claim costs the human seconds to reject. The pipeline is tilted toward over-inclusion: surface anything plausibly claim-bearing and let the human filter.

These drive the three design decisions below: 1a covers the whole transcript (no scope filter); segmentation and attribution run as one pass; and a mechanical gap check sits between 1a and 1b.

### Stage 1a: Structure

**Purpose.** Segment the full transcript by topic and attribute speakers in a single pass (with sub-segment overrides where speakers change mid-segment), and flag uncertainty. Segmentation and attribution are combined because topic boundaries and speaker changes are heavily correlated in actual-play content and line content is a strong attribution signal — splitting them across two passes throws away information the joint pass has. Segments are contiguous and cover the whole transcript — every moment belongs to some segment. If the pass cannot identify a topic for a stretch (long pause, unintelligible audio), it still emits a segment with an appropriate title (“silence”, “inaudible”): explicit is better than implicit.

**Input.**

- Raw transcript (after literal-substitution corrections applied from `.transcription-corrections.yaml`)
- Full preamble (including `recurring_speakers` and `interpretation_defaults`)

**Output.** `pending/<ingest_id>/reading/structure.yaml`:

```yaml
schema_version: 1
source_id: yt-abc123
generated_at: 2026-04-20T14:32:00Z
default_speaker: DM
segments:
  - id: seg-001
    start: "0:00:00"
    end: "0:02:15"
    title: "Introduction"
    speaker: DM
  - id: seg-002
    start: "0:02:15"
    end: "0:04:30"
    title: "Rules discussion: grappling"
    speaker: mixed
    notes: "Off-topic rules lookup; unlikely to yield claims."
  - id: seg-003
    start: "0:04:30"
    end: "0:08:00"
    title: "Founding of Aldara"
    speaker: DM
  - id: seg-004
    start: "0:08:00"
    end: "0:12:00"
    title: "The War of the Dusk"
    speaker: DM
    overrides:
      - start: "0:09:45"
        end: "0:10:12"
        speaker: "Innkeeper NPC"
        voiced_by: DM
        note: "DM voicing an NPC in conversation with the party."
uncertainty_flags:
  - locator: "0:05:47"
    span: "a place name starting with V"
    kind: name                      # name | attribution | other
    note: "proper noun unclear; sounds like Val- or Vel-"
```

**Mechanical checks.** Segment start/end correspond to real transcript timestamps. Segments cover the full transcript duration without gaps. Override ranges fall within their parent segment. Uncertainty flag locators fall within some segment.

**Uncertainty over-flagging.** The prompt instructs the model to err on the side of flagging — dismissing a flag costs seconds; a silently-swallowed uncertain name pollutes a downstream fact.

### Mechanical gap check

After 1a completes, a deterministic check (no LLM) identifies any contiguous transcript stretch longer than a configurable threshold (default: 5 minutes) whose segments all have thin claim-bearing signals: titles matching patterns like “rules discussion”, “break”, “off-topic”, “silence”, or segments with `notes` suggesting low yield. This is a heuristic sanity check — the tool does not act on it, only surfaces it in reading review:

```
⚠ Possible coverage gap:
  0:34:10–0:48:22 covered only by segments titled
  "Pizza discussion", "Break", "Rules: initiative".
  If this stretch contained worldbuilding, regenerate with a hint.
```

The human confirms the stretch is genuinely low-yield or regenerates 1a with a hint about what to look for.

### Stage 1b: Summarize

**Purpose.** For each segment from 1a, produce claim bullets (or explicitly none). This is the only substage that can invent content.

**Input.**

- Segmented, speaker-attributed transcript from 1a
- Full preamble (including `interpretation_defaults`)

**Output.** `pending/<ingest_id>/reading/reading.md` — assembled from 1a’s segments plus 1b’s per-segment bullets. This is the artifact the human reviews.

**Per-segment extraction.** 1b processes each segment independently (trivially parallelizable). Empty bullet lists are allowed and expected — a “Rules discussion: grappling” segment typically yields no bullets, and that’s the correct output. The bullet list’s emptiness is information at review time.

**Locator hints for downstream stages.** Alongside each bullet, 1b emits a `locator_hint` range — a small window around the bullet's anchor timestamp that downstream stages can use to narrow search. The hint is internal pipeline metadata: it flows from 1b through the planner into the extractor and is never surfaced in `reading.md`. It is an optimization for Stage 3, not a claim for the human to review. (See "Locator hint schema" below for the emitted shape and Stage 3 for how it's consumed.)

**Assembly.** The final `reading.md` interleaves segment headers (from 1a) with their bullet lists (from 1b):

```markdown
---
schema_version: 1
source_id: yt-abc123
source_name: "Worldbuilding Session 3: The Founding of Aldara"
source_url: https://youtube.com/watch?v=abc123
source_type: youtube
session_date: null              # human fills in during review
ingested_at: 2026-04-20T14:35:12Z
reading_status: draft           # draft | approved
default_speaker: DM
name_corrections:
  # empty initially; human adds transcription fixes here
  # "Fair-on": "Theron"
---

# Reading: Worldbuilding Session 3

## [[0:00:00-0:02:15]](https://youtube.com/watch?v=abc123&t=0) Introduction

Speaker: DM

The DM introduces the session and previews the topics covered.

## [[0:02:15-0:04:30]](https://youtube.com/watch?v=abc123&t=135) Rules discussion: grappling

Speaker: mixed

_No claims extracted from this segment._

## [[0:04:30-0:08:00]](https://youtube.com/watch?v=abc123&t=270) Founding of Aldara

Speaker: DM

- King Theron's grandfather founded Aldara in the Second Age [[0:04:32]](https://youtube.com/watch?v=abc123&t=272)
- The founding displaced an earlier elven presence [[0:05:14]](https://youtube.com/watch?v=abc123&t=314)
- In-world scholars dispute the exact founding year [[0:06:02]](https://youtube.com/watch?v=abc123&t=362)
```

Uncertainty flags from 1a are preserved in the assembled reading as inline markers the human can resolve. Segments with no extracted claims are rendered with an explicit “No claims extracted” marker so that empty segments are visible rather than invisible — the marker is the mechanism that lets the human notice a segment that _should_ have contributed but didn’t.

### Clickable timestamps

All timestamps in the reading render as markdown links. Clicking a timestamp opens the source at that moment (e.g., YouTube with `&t=` query param). The LLM emits plain `[4:32]` text; the tool post-processes to add link URLs. This post-processing applies to the final assembled reading, not to the intermediate YAML artifacts.

On save, the tool re-syncs display-vs-URL seconds if the human edited a timestamp.

### Timestamp format

Two distinct kinds of timestamps appear in the system and do not get confused:

**Source locators** (positions inside a transcript) use canonical format `h:mm:ss`, including locators in fact YAMLs and reading sections. Ranges use `h:mm:ss-h:mm:ss`. For sources under an hour, the leading `0:` is still written for consistency (`0:04:32`, not `4:32`), except in user-facing display where the leading zero hour may be elided for readability. Parsers accept either form; writers produce the canonical form.

**Wall-clock event timestamps** (when something happened in the real world: the tool wrote a file, the human approved a fact, an ingest was rejected) use RFC 3339 with explicit timezone offset — either `Z` for UTC or `±HH:MM` for a local offset. Examples: `2026-01-16T18:22:47Z`, `2026-04-20T09:15:42-07:00`. Fields: `fetched_at`, `ingested_at`, `generated_at`, `planned_at`, `approved_at`, `created_at`, `updated_at`, `edited_at`, `promoted_at`, `added_at`, and `at` inside `status_history` entries. The tool writes UTC by default; a future flag may let users opt into local-offset writes. Parsers accept any valid RFC 3339 string and normalize to UTC for comparison.

**`session_date` is exempt.** It represents an in-world or calendar-day concept — "which session did this claim first enter canon" — not a wall-clock event. It stays as a plain `YYYY-MM-DD` date and is the only date-only field in the system.

### Name corrections

When the human notices a mishearing (e.g., “Fair-on” should be “Theron”), they add it to the `name_corrections` map in frontmatter rather than find-replacing throughout the reading. The tool applies the substitutions during rendering and passes the map to downstream stages.

Corrections from approved readings can be promoted to the global `.transcription-corrections.yaml` so future sources benefit automatically.

### Uncertainty flags

1a flags words, names, or attributions it’s unsure about. Uncertainty flags appear inline in the assembled reading:

```markdown
- [0:05:47] A proper noun here was unclear; appears to be a place name starting with V
```

The human resolves by listening to the audio (or using setting context), then replaces with the correct content. The prompt biases 1a toward over-flagging: a flag the human dismisses is cheap, a missed mishearing silently pollutes a claim.

### Locator hint schema

Each bullet 1b emits carries an internal `locator_hint`: a narrow time range inside which the claim's verbatim source should fall. This is pipeline metadata, not reading content — the hint is persisted in 1b's per-segment output YAML, passed through `plan.yaml` unchanged by the planner, and consumed by Stage 3.

Shape, per bullet:

```yaml
bullet_index: 0
text: "King Theron's grandfather founded Aldara in the Second Age"
anchor: "0:04:32"                  # the point timestamp shown in reading.md
locator_hint: "0:04:25-0:04:50"    # search window for Stage 3
```

The hint is a window, not a precise range: 1b picks an anchor that's approximately where the claim lands and pads it generously (default ±15s). The authoritative locator on the final proposal is produced by Stage 3, not by this hint — see Stage 3 for why.

The hint is not surfaced in `reading.md` and is not part of the reading review. Hand-edits to bullet timestamps in `reading.md` sync back to the bullet's `anchor`; the `locator_hint` window is recentered on the edited anchor at save time. This preserves the hint's usefulness after routine timestamp corrections without requiring the human to think about windows.

### Reading review

The review is over the combined, assembled reading — the human sees the full reading.md and corrects whatever’s wrong in it. Two scans:

**Scan 1: segment titles as a table of contents.** Skim titles top-to-bottom, looking for gaps, mislabeled segments, or the mechanical gap-check warning. A two-hour session produces maybe 40–80 segment titles; skimming takes a minute or two.

**Scan 2: bullets within segments.** For claim-bearing segments, read the bullets and confirm they correspond to reality. Empty bullet lists get a quick sanity check — a “Founding of Aldara” segment with no bullets is a red flag.

Corrections fall into buckets aligned with the substages:

- Segment boundaries, titles, or coverage gaps → edit section headers or regenerate 1a (1a-class fix)
- Wrong speaker attribution → edit `Speaker:` lines (1a-class fix, attribution subset)
- Claim doesn’t match what was said, or invented, or missing → edit/delete/add the bullet, or regenerate 1b for that segment (1b-class fix)

The web UI (Phase 7) can surface structure and claims distinctly when that helps, but the terminal MVP treats the reading as a single markdown file to edit.

Transitioning `reading_status: draft` → `reading_status: approved` is the gate. The tool refuses to run downstream stages on a draft reading.

```bash
auto-lorebook approve-reading <source_id>
```

After approval, the reading is committed to the wiki alongside the raw transcript. The intermediate `structure.yaml` is retained in the pending directory as an audit artifact for the lifetime of the ingest, then discarded when the ingest is fully completed or rejected. Future re-runs of extraction operate on the approved reading.

### Regenerating substages

If reading review reveals the structure (segmentation or attribution) is badly wrong in ways that are tedious to fix by hand, the user can re-run from a given point:

```bash
auto-lorebook regenerate-reading <source_id> --from=structure   # reruns 1a, 1b
auto-lorebook regenerate-reading <source_id> --from=summarize   # reruns 1b only
auto-lorebook regenerate-reading <source_id> --from=summarize --segments seg-003,seg-007
                                                                # reruns 1b on listed segments only
```

Per-segment 1b regeneration is cheap because 1b is parallelized per-segment; if one segment’s bullets are clearly wrong but the rest are fine, this leaves the rest of the review work untouched.

`name_corrections` in frontmatter are preserved across all regenerations. Human edits to the reading body are preserved by per-segment 1b regeneration but discarded by full-reading regenerations — if hand edits are worth keeping, approve the reading; if the machine output is too broken to edit, regenerate from scratch.

## Stage 2: Planner

### Purpose

Route claims from the approved reading to entities. Resolve entity identity (existing vs. new). Flag ambiguity for human review.

### Input

- Approved reading (frontmatter + body)
- Entity index (in-memory, built from entity YAMLs)
- Existing entity YAML files (for richer context where needed)
- Prompt preamble (same assembly as reading stage)

### Output

Single file at `pending/<ingest_id>/plan.yaml`:

```yaml
schema_version: 1
reading_id: yt-abc123
planned_at: 2026-04-20T14:58:33Z

entity_resolutions:
  - mention: "the Aldaran Realm"
    mention_locations: ["[4:30-8:00] founding"]
    resolution: existing          # existing | new | ambiguous
    matched_entity: Aldara
    rationale: "Listed in Aldara's aliases."
    suggested_aliases_to_add: []
  - mention: "the War of the Dusk"
    mention_locations: ["[8:00-12:00] war"]
    resolution: new
    proposed_entity_name: War of the Dusk
    proposed_category: events
    rationale: "No existing entity matches."
  - mention: "the elven sorceress"
    mention_locations: ["[1:23:40-1:24:15] hearsay"]
    resolution: ambiguous
    rationale: "Unnamed referent. Attach hearsay to Aldara with note."
    human_review_needed: true

new_entities:
  - name: War of the Dusk
    category: events
    aliases_suggested: []

planned_claims:
  - claim_group_id: cg-001
    reading_section: "[4:30-8:00] Founding of Aldara"
    reading_bullet_index: 0
    locator: "0:04:32"
    locator_hint: "0:04:25-0:04:50"
    proposed_speaker: DM
    proposed_status: authoritative
    proposed_status_reason: null
    targets:
      - entity: Aldara
        entity_state: existing    # existing | new
        proposed_section: founding
        rationale: "Claim concerns Aldara's founding."
      - entity: Theron
        entity_state: existing
        proposed_section: lineage
        rationale: "Claim establishes Theron's grandfather as founder."
      - entity: Second Age
        entity_state: new
        proposed_category: events
        proposed_section: events-in-era
        rationale: "Claim dates founding to the Second Age."

unresolved:
  - reading_section: "[8:00-12:00] The War of the Dusk"
    locator: "0:09:12"
    issue: "Reading flagged uncertain place name here; unresolved."
```

### Multi-target routing

A single claim can route to multiple entities when it carries information about more than one. The example above routes one claim to Aldara (founding), Theron (lineage), and the Second Age (events-in-era). Each target gets its own proposal; approvals are per-target, so the human can accept the claim onto Aldara and Theron but reject it from the Second Age page without affecting the others. Targets sharing a `claim_group_id` share the same `raw_transcript_span`, `locator`, and extracted `text` — the extractor locates once per claim group and copies the result across the group's proposals.

The planner is instructed to route to a target only when the claim directly concerns that entity, not merely mentions it. "Theron met Aelindra at the Festival of Masks" routes to Theron and Aelindra, not to the Festival (which is just the setting of the meeting, not the subject of the claim). This is a soft constraint — systematic over-routing surfaces during fact review as reject-heavy proposals for incidental targets, and is a signal to `replan` with a hint.

Single-target routing remains the common case; the schema permits a `targets` list of length one, and most planned claims will have exactly that.

### No approval gate, no filesystem side effects

The planner is an intermediate stage, not a gate. Its output (`plan.yaml` plus alias suggestions) is an audit artifact and input to the extractor, which runs automatically after the planner completes. The planner writes nothing to the wiki — new entities are proposals on the plan, not stub YAMLs. Stub creation happens at fact review, atomically with the first approved fact targeting each new entity.

This is deliberate. The plan gate’s main job — catching duplicate entities and bad routing — is work the fact-review gate does equally well: routing metadata (`matched_via`, resolution rationale, new-vs-existing status) travels with each proposal, so a human reviewing proposal #7 sees the same “wait, this should match Aldara” signal they’d see in a plan review, with the added benefit of reading the actual claim. And because the planner never touches the filesystem, a hallucinated or poorly-routed new entity never pollutes the entity index, and `replan` can discard unreviewed proposals with no residue to clean up.

Inspection is still available via `auto-lorebook plans show <ingest_id>`.

### Replan escape hatch

If fact review reveals systematic routing errors (same misroute across many proposals, an entity the planner missed entirely), the human can bail out of review and re-run the planner:

```bash
auto-lorebook replan <ingest_id>
```

This discards unreviewed proposals from the current run, re-invokes the planner (which sees any entity YAMLs created since the last run, including stubs created by approvals earlier in this ingest), and re-runs the extractor. Proposals already approved are unaffected — their facts are in entity YAMLs and the planner’s new-entity detection will see those entities as existing.

## Stage 3: Extractor

### Purpose

For each planned new fact, locate the verbatim span in the raw transcript and produce a proposal with full metadata. No paraphrasing — extraction only.

### Input

- Approved plan (including per-fact `locator_hint` ranges)
- Approved reading (including `name_corrections` map)
- Raw transcript (accessed per-proposal via the hint window, not fed whole)
- Reduced preamble (transcription corrections and entity aliases only)

### Output

One YAML file per proposed fact at `pending/<ingest_id>/proposals/<proposal_id>.yaml`:

```yaml
schema_version: 1
proposal_type: new_fact          # new_fact | new_entity_with_facts
target_entity: Aldara
proposed_id: aldara-f004
claim_group_id: cg-001           # shared with sibling proposals routing the same claim elsewhere
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

### Extraction rules

- `raw_transcript_span` must be a literal substring of the transcript between the given timestamps. The tool verifies this after the LLM responds; if verification fails, retry or flag.
- `text` differs from `raw_transcript_span` only through applied `corrections_applied`. No rewriting, cleanup, or filler removal at this stage.
- **Windowed search.** Each proposal's prompt is fed only the transcript slice covering its `locator_hint` window, not the whole transcript or the whole segment. This is the primary lever keeping Stage 3 prompts small and uniform in size across proposals.
- **Fallback on miss.** If substring verification fails within the hint window, the extractor retries once with the span widened to the full parent segment from 1a. If that also fails, flag with `extractor_flagged: true`. A "widened to segment" retry is logged on the proposal (`hint_widened: true`) so systematic anchor drift in 1b is visible.
- **Hints are advisory; the authoritative locator is produced here.** The final `locator` on the proposal is the precise range where the span actually lands in the transcript, derived during extraction — not copied from `locator_hint`. The hint only narrows the search space.
- If the claim cannot be found in a single contiguous span, flag with `extractor_flagged: true` and an explanation. Do not synthesize across non-adjacent spans.
- Parallelizable: each proposal is independent.
- **Claim-group deduplication.** Sibling proposals within a `claim_group_id` share the same `raw_transcript_span`, `locator`, `text`, and `corrections_applied`. The extractor runs once per claim group (not once per proposal), then copies the result to each sibling. Each sibling still gets its own proposal file, its own `proposed_id`, and its own `target_entity`/`section` — only the span-location fields are shared. If extraction fails for a claim group, all sibling proposals inherit the same `extractor_flagged` state.

The extractor’s job is narrow by design: locate and snip. Cleanup happens upstream (reading-stage corrections) and downstream (summarizer prose). Narrowness buys the mechanical substring guarantee, which a paraphrasing extractor can’t provide.

## Human fact review

### Purpose

Per-fact approval gate. The only point where new facts _and new entities_ enter the wiki.

### New entity creation during review

When a proposal targets an entity the planner marked `new`, nothing in the wiki has been created yet. The entity stub is created atomically with the first approval of a fact targeting it:

1. On the first approval, the tool creates `<category>/<slug>.yaml` with canonical name, aliases confirmed so far, `created_at`, and `created_by_ingest` set to the current ingest ID. It then appends the approved fact.
2. Subsequent approvals for the same entity in the same review session just append to the now-existing file.
3. Aliases confirmed during any approval for that entity are merged into its `aliases` list (on stub creation for the first approval; on update for later ones).
4. The in-memory entity index is refreshed after each approval, so a proposal reviewed later in the same session that references an entity created earlier in the session sees it as existing.

If every proposal for a proposed new entity is rejected, no stub is ever written. This falls out of the design rather than requiring cleanup logic.

### MVP: terminal review

```bash
auto-lorebook review <ingest_id>
```

Walks through proposals one at a time. Each proposal must be approved, edited, or rejected before the next is shown — there is no skip or defer. If the user exits (Ctrl-C or closes the terminal), untouched proposal files remain in `pending/<ingest_id>/proposals/`, and the next invocation of `review` resumes with the first remaining file.

**Claim-group ordering.** Proposals sharing a `claim_group_id` are shown in a contiguous block, so the human reads the claim once and then decides per-target. The header names the group's position and size so the user knows what they're in the middle of. Review order within a group is arbitrary; across groups, order follows transcript position.

```
─── Proposal 1 of 12  ·  Claim group cg-001 (1 of 3 targets) ────────

Target entity: Aldara (existing)
  Matched via: alias "the Aldaran Realm"
Section: founding

Proposed text:
  "Theron's grandfather founded Aldara in the Second Age."

Raw transcript:
  "Fair-on's grandfather founded all-dara in the Second Age."

Corrections applied:
  • "Fair-on" → "Theron"       (global)
  • "all-dara" → "Aldara"      (reading)

Source: Worldbuilding Session 3
Locator: 0:04:32-0:04:41  → https://youtube.com/watch?v=abc123&t=272
Speaker: DM
Status: authoritative
Session date: 2026-01-15

Context:
  Before: "So let's talk about the founding of Aldara."
  After:  "And that's why the Theron name matters so much now."

Also routes to:
  → Theron (lineage)              — next
  → Second Age (events-in-era)    — then

[a]pprove  [e]dit  [r]eject  [p]lay (open URL)
>
```

After approval or rejection, the next claim-group sibling shows with an abbreviated header (the claim text and locator are unchanged; only the target and section differ):

```
─── Proposal 2 of 12  ·  Claim group cg-001 (2 of 3 targets) ────────

Target entity: Theron (existing)
Section: lineage

(Same claim text, locator, and context as previous proposal.)

[a]pprove  [e]dit  [r]eject  [p]lay (open URL)
>
```

An edit to `text` inside a claim group applies only to the proposal being edited — sibling proposals keep the original text. This is deliberate: the human might want to phrase the founding claim differently on Theron's page than on Aldara's, and forcing edits to propagate across siblings would undercut that. If propagation _is_ wanted, the user edits each sibling in turn; the terminal can offer a `[E]dit-all` variant as a convenience in the Phase 7 web UI, but the terminal MVP does not.

For proposals targeting a proposed new entity that does not yet exist on disk:

```
Target entity: War of the Dusk (NEW — events, will be created on approval)
  Proposed aliases: (none)
```

For proposals targeting an entity created earlier in the same review session:

```
Target entity: War of the Dusk (events)
  Created earlier in this review session
```

This note is derived at display time by comparing the entity’s `created_by_ingest` to the current ingest ID — no extra state required.

For proposals where the planner matched via a mention that isn’t yet an alias, the display offers alias confirmation:

```
Target entity: Aldara (existing)
  Matched via: "the Realm" (not currently an alias)
  Add "the Realm" as alias? [y/n]
```

Alias confirmation is a sub-prompt, not a main action. It applies immediately on `y` and is recorded in the entity YAML as an alias record with `source: alias-confirmation` and `added_by_ingest` set to the current ingest (or merged into the stub at creation time if the entity is new and this is its first approval, in which case the source on the first-approval aliases is `stub-creation`). Aliases added via alias confirmation are cleanly removable by `reject-ingest`.

Actions:

- **Approve**: proposal becomes a fact, appended to the entity’s YAML (creating the YAML if this is the first approval for a new entity), summary regenerated. Proposal file deleted.
- **Edit**: opens `text` (and optionally other fields) for inline editing. Tracks original as `text_source` on the approved fact. Then approves.
- **Reject**: proposal discarded.
- **Play**: prints the URL; user clicks through to verify against audio. Play is not a decision — after playing, the user still must approve, edit, or reject the current proposal.

Status, speaker, and section can all be overridden during review. Text edits are supported but optional — approving as-is is fine since the summarizer produces readable prose downstream.

The no-skip-or-defer rule (stated above) is deliberate: a “come back to it later” action would accumulate stale state that drifts out of sync with the entity index and planner output. If review surfaces systematic problems, the correct move is `replan` (see Stage 2), not deferral.

### Post-MVP: web review UI

Same actions, richer presentation. Side-by-side views, inline editing, one-click playback.

## Stage 4: Summarizer

### Purpose

Regenerate readable summary prose for an entity from its approved facts. Runs after fact approvals. May batch regeneration at session end rather than per-fact for efficiency.

### Input

- Entity YAML (all approved facts)
- Entity index (for alias-aware rendering of cross-references)

### Output

Entity markdown file, overwritten in full on each regeneration:

```markdown
# Aldara

## Summary

Aldara is a kingdom founded in the Second Age [^1][^2] by the grandfather
of King Theron [^1]. Its ruling bloodline has remained unbroken since
its founding [^3]. Some tavern rumors suggest the founding king was
cursed by an elven sorceress [^4], though this is unconfirmed.

## Facts

### Authoritative

**Founding**

[^1]: "Theron's grandfather founded Aldara in the Second Age."
  — DM, [Worldbuilding Session 3, 0:04:32-0:04:41](https://youtube.com/watch?v=abc123&t=272)
  (session: 2026-01-15)

[^2]: "Scholars dispute the exact year, but the Second Age attribution is well-attested."
  — DM, [Worldbuilding Session 3, 0:06:02-0:06:14](https://youtube.com/watch?v=abc123&t=362)
  (session: 2026-01-15)

**Government**

[^3]: "Aldara's kings have always come from the Theron bloodline."
  — DM, Campaign Notes, lines 47-48 (session: 2026-01-20)

### Hearsay

[^4]: "The founding king was cursed by an elven sorceress."
  — Innkeeper NPC, [Worldbuilding Session 3, 1:23:40-1:24:15](https://youtube.com/watch?v=abc123&t=5020)
  (session: 2026-02-03)
  *Told to the party by a tavern NPC, not confirmed.*

### Disproven

_(none)_

## References

1. Worldbuilding Session 3 — https://youtube.com/watch?v=abc123
2. Campaign Notes — sources/txt-campaign-notes/notes.txt
```

### Summarizer rules

- **Authoritative facts** stated as plain fact in summary prose
- **Trustworthy facts** stated as fact but with the source surfaced in prose ("According to Maester Aemon,…", "Guild records attest that…"). Grouped in their own `### Trustworthy` section under `## Facts`, below Authoritative and above Hearsay. The domain warrant from `status_reason` is rendered as an italicized note under the footnote, parallel to the hearsay treatment.
- **Hearsay facts** attributed and hedged (“tavern rumors suggest…”, “one account holds…”)
- **Disproven facts** excluded from summary by default; rendered struck-through in their own section with the reason
- Every summary sentence cites fact IDs; citation labels in the rendered view are footnote numbers
- **Section ordering and normalization**: the summarizer reads the free-text `section` field on each fact and groups facts by normalized section name (case-insensitive, trimmed). If two facts have sections “founding” and “Founding”, they group together under the canonical casing of whichever appears more often, with a tie broken by first-seen. This papers over drift without forcing a controlled vocabulary. A future enhancement may allow per-category section vocabularies in `.wiki-context.yaml`.

The entity YAML is the source of truth. The markdown file is a regenerable view. If the two disagree (because someone hand-edited the markdown), the YAML wins on next regeneration.

## Entity data model

### Entity YAML schema

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
superseded_by: null          # or "<category>/<slug>" when merged
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
    claim_group_id: cg-ingest-2026-01-16-a-001  # null if routed to a single entity
    section: founding

  - id: aldara-f002
    # ...
```

### Field semantics

- **`slug`**: filename stem (`<category>/<slug>.yaml`). Renames are explicit: change `slug`, tool moves the file.
- **`aliases`**: list of records, each `{name, added_by_ingest, added_at, source}`. `source` is `hand-edited` (user added directly to the YAML), `alias-confirmation` (approved during a review alias sub-prompt), `stub-creation` (accompanied the entity's first approved fact), or `promoted-from-merge` (carried over when a superseded entity was merged in; `added_by_ingest` on those records is copied from the source entity's record, not the merge ingest, to preserve provenance). Duplicate names are deduplicated on write, keeping the earliest record. Aliases are compared by normalized name (case-insensitive, whitespace-trimmed) throughout the tool; the record preserves the user's original casing.
- **`superseded_by`**: null, or `"<category>/<slug>"` pointing to the entity this one was merged into. The planner’s entity index resolves mentions of this entity (including its aliases) to the target. The file stays as a historical record.
- **`created_by_ingest`** (on the entity): the ingest that first created this entity stub (via the first approved fact targeting it). Mirrors the same field on facts. Used for `reject-ingest` cleanup and to derive the “created earlier in this review session” display note.
- **`id`**: stable across renames and edits. Assigned at approval.
- **`text`**: current displayed version. Starts as extracted span with corrections applied; can be edited by human during review.
- **`raw_transcript_span`**: literal substring of the source transcript. Immutable. Evidence.
- **`text_corrects_transcript`**: true if `text` differs from `raw_transcript_span` (either through corrections or human edits).
- **`corrections_applied`**: audit trail of substitutions, with source (`global-transcription-correction` | `reading-name-correction` | `human-edit`).
- **`source_id`**: foreign key to `sources/<source_id>/info.yaml`.
- **`locator`**: timestamp range in canonical `h:mm:ss-h:mm:ss` format (for audio/video) or line range (for text).
- **`speaker`**: free-text attribution. Conventions: “DM,” “Player-Thorin,” “Innkeeper NPC,” “Narrator.”
- **`status`**: epistemic tier.
    - **Authoritative** = stated by the canonical voice (DM narration, worldbuilding-video author, notes written by the setting's author). The setting itself vouches for it.
    - **Trustworthy** = stated within fiction by a source with plausible domain knowledge over the claim (a maester on heraldry, a priest on their own god's rites, a guild captain on guild history). Not canonical voice, but not idle gossip either — the source has standing on _this topic_.
    - **Hearsay** = stated within fiction by a source without special standing on the claim (tavern rumor, street talk, secondhand retelling, NPC speculation outside their expertise).
    - **Disproven** = superseded by a later authoritative fact. Domain knowledge is topic-scoped: the same NPC can produce `trustworthy` facts on their specialty and `hearsay` facts on unrelated subjects. When in doubt between trustworthy and hearsay, prefer hearsay — the distinction is meant to elevate clear domain authority, not to launder every speaker with a title.
- **`status_reason`**: required for `trustworthy`, `hearsay`, and `disproven`; free-text. For `trustworthy`, name the domain warrant (e.g., "Speaker is the court maester discussing bloodline heraldry"). For `hearsay`, note why the source is unreliable. For `disproven`, cite the superseding fact.
- **`status_history`**: full log of status changes. Each entry carries `status`, `at`, `by` (e.g., `human-review`, `ingest-<id>`, `migration`), and `reason`. Append-only.
- **`session_date`**: when the claim entered canon. Can be null.
- **`approved_at`**: when the human approved the fact.
- **`created_by_ingest`** (on a fact): ID of the ingest session that produced this fact. Used to bulk-reject an ingest if needed.
- **`claim_group_id`**: populated when this fact was routed to multiple entities from the same claim; null for single-target claims. Facts sharing a `claim_group_id` across entity YAMLs share the same `raw_transcript_span`, `locator`, `source_id`, and (at approval time) `text` — though `text` can later diverge if edited on one entity's copy. Scoped to the ingest that produced them: IDs are formatted `cg-<ingest_id>-NNN` so a group ID is globally unique without a separate registry. The tool surfaces sibling facts via queries like `entities show <entity> --claim-group <id>`.
- **`section`**: organizational bucket within the entity page (founding, government, legends, etc.). Free-text; the summarizer normalizes case and trims whitespace when grouping.

### Entity index

The filesystem is the source of truth for entity identity. An entity exists iff `<category>/<slug>.yaml` exists. Canonical name, aliases, category, and merge status all live in the entity YAML.

There is no separate registry file. The planner builds an in-memory index from entity YAMLs at the start of each command that needs one. The review loop refreshes the index after each approval so that entities created earlier in a review session are visible to later proposals in the same session. At small scale (hundreds of entities) this is fast; if it becomes slow, the tool may cache the index at `.cache/entity-index.json`. The cache is never authoritative — it’s rebuilt from YAMLs whenever any entity YAML changes.

### Global transcription corrections

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
  - from: "Al-Dora"
    to: "Aldara"
    first_seen_in: yt-abc123
    also_seen_in: []
    promoted_at: 2026-01-18T10:04:21Z
    notes: null
```

These are phonetic mishearings that apply across all sources. Distinct from entity aliases (semantic, in-world) and from per-source `name_corrections` in reading frontmatter (local to one source).

**Application:**

- Reading stage: the tool applies corrections as literal substitutions to the transcript before the LLM sees it in 1a, and includes them in every substage’s preamble as an explicit instruction. Per-source `name_corrections` stack on top; per-source wins on conflict.
- Extractor stage: applies the union of global corrections + approved reading’s `name_corrections` when producing `text` from `raw_transcript_span`. Each substitution is logged in `corrections_applied` with source (`global-transcription-correction` or `reading-name-correction`).
- Planner stage: works from the corrected reading, so transcript corrections don’t apply here. Entity index matching handles aliases separately.

**Promotion:** corrections that recur across readings can be promoted from per-source frontmatter to the global file via `auto-lorebook promote-correction "<from>" "<to>"`. Per-source entries remain in reading frontmatter after promotion as historical record; application code uses the union, so no duplication issue. When a correction is promoted, `first_seen_in` is set to the earliest source containing it; subsequent promotions of the same pair append to `also_seen_in`.

## Audit trail

With no git dependency, the YAML files themselves are the audit trail:

- Every fact carries `approved_at`, `created_by_ingest`, `edited_by_human`, `edited_at`, `corrections_applied`, and `status_history` (with actor).
- Every entity carries `created_at`, `created_by_ingest`, `updated_at`, and `superseded_by` for merges.
- Every source carries `fetched_at` and `session_date`.

### Rejecting an ingest

```bash
auto-lorebook reject-ingest <ingest_id>
```

Removes everything attributable to that ingest:

1. Removes all facts with matching `created_by_ingest` from every entity YAML.
2. Removes all alias records with matching `added_by_ingest` from every entity YAML. An entity whose canonical `name` is unaffected but whose aliases shrink is still considered modified; `updated_at` is bumped.
3. Removes any entity whose own `created_by_ingest` matches _and_ whose `facts` list is now empty. Entities that were created by this ingest but have since received facts from other ingests stay (with the rejected facts and aliases removed).
4. Regenerates affected summaries.

This gives a clean “what did this ingest add” answer: an ingest’s net contribution is exactly the facts tagged with its ID plus the entities tagged with its ID. Also makes debugging queries straightforward (e.g., `entities list --created-by <ingest_id>`).

## CLI reference

```bash
# Ingest (interactive prompts by default; flags skip corresponding prompts)
auto-lorebook ingest <url-or-path> \
  [--source-url <url>] \
  [--source-id <id>] \
  [--session-date <YYYY-MM-DD>] \
  [--perspective <text>] \
  [--source-nature <kind>] \
  [--setting <name>] \
  [--no-interactive]

# Re-run context prompts for an existing source
auto-lorebook configure-context <source_id>

# Generate reading (auto-offered after ingest; can also run manually)
# Runs 1a → 1b in sequence
auto-lorebook generate-reading <source_id>

# Regenerate specific reading substages if the first pass needs work
auto-lorebook regenerate-reading <source_id> --from={structure|summarize} \
  [--segments <id1,id2,...>]   # --segments valid only with --from=summarize

# Readings
auto-lorebook approve-reading <source_id>
auto-lorebook readings list
auto-lorebook readings show <source_id>

# Plans (intermediate artifact; no approval gate)
auto-lorebook plans list
auto-lorebook plans show <ingest_id>
auto-lorebook replan <ingest_id>           # re-run planner + extractor on unreviewed proposals

# Review
auto-lorebook review <ingest_id>           # walks through proposals

# Corrections
auto-lorebook promote-correction "<from>" "<to>"

# Entities
auto-lorebook entities rebuild-index       # rebuild in-memory cache
auto-lorebook entities list [--created-by <ingest_id>]
auto-lorebook reject-ingest <ingest_id>    # remove all facts (and empty entity stubs) from an ingest

# Wiki
auto-lorebook wiki list [--category <cat>]
auto-lorebook wiki show <entity>
auto-lorebook wiki rebuild                 # regenerate all summaries from YAML

# Sources
auto-lorebook sources list                 # flags sources with missing session_date
auto-lorebook sources show <source_id>

# Web UI (Phase 6+)
auto-lorebook serve [--port 8080]
```

## Build phases

### Phase 1: Reading stage

Goal: ingest a YouTube URL, gather context, produce a reviewable, correctable reading via the two-substage pipeline.

- CLI skeleton + config file handling
- `yt-dlp` subprocess wrapper: transcript + title + duration
- SRT parser
- OpenRouter client with configurable model slots (both reading substages default to the primary model)
- Source metadata → `sources/<source_id>/info.yaml` including `context` block
- `.wiki-context.yaml` reader (tolerates missing or empty file)
- `.transcription-corrections.yaml` reader (tolerates missing or empty file)
- Ingest halt + `generate-reading` command for the context-gathering step
- CLI flags for common context fields (`--session-date`, `--perspective`, etc.)
- Prompt preamble assembly from `info.yaml` + `.wiki-context.yaml` + corrections + entity index, with per-substage variation
- Token-budget check on preamble with component-specific error messages
- Stage 1a (structure): produces `structure.yaml` with full-coverage segments, per-segment speaker assignment, sub-segment overrides, and uncertainty flags; mechanical validation that timestamps are real, that segments cover the transcript without gaps, and that override ranges fall within parent segments
- Mechanical gap check: heuristic pass over `structure.yaml` that surfaces long stretches covered only by low-yield-looking segments as warnings during review
- Stage 1b (summarize): per-segment claim extraction with empty bullet lists permitted; parallelized across segments; emits per-bullet `locator_hint` windows as internal pipeline metadata; produces draft `reading.md` by interleaving 1a’s segment headers with 1b’s bullets; post-processes for clickable timestamps
- Canonical timestamp format (`h:mm:ss`) enforced in writers, lenient in readers
- `name_corrections` frontmatter + rendering
- `regenerate-reading` with `--from` flag, including per-segment 1b regeneration via `--segments`
- `approve-reading` command

Exit criterion: ingest a real actual-play VOD, fill in context, run both substages, review the combined reading, approve. The reading covers the full transcript (no gaps); segments with no claims are visibly empty rather than absent; at least one off-topic segment is correctly rendered with an empty bullet list; the mechanical gap check fires at least once on real content and the warning is actionable. Total human review time for a two-hour source fits inside the 10–20 min/hour target on a representative session.

### Phase 2: Entity scaffolding

- Entity YAML schema + directory structure
- In-memory entity index, built from filesystem scan
- Commands to list/show entities
- Ability to hand-create entity stubs that the tool recognizes (bootstrapping aid; hand-creation is not the normal path — entities are normally created via approved facts)

Exit criterion: hand-create a few entities; tool lists them and exposes them to the next stage as an entity index.

### Phase 3: Planner

- Stage 2 planner with trimmed MVP schema
- Plan written to pending state as intermediate artifact
- **No filesystem side effects from the planner**: new entities recorded on the plan only, no stub creation
- Alias suggestions recorded on the plan for per-proposal confirmation in fact review
- Multi-target routing: planned claims carry a `targets` list, allowing a single claim to route to multiple entities with per-target section and rationale
- `plans list` and `plans show` inspection commands

Exit criterion: ingest a source, review reading, planner runs automatically and produces a plan. New entities are _proposals on the plan_, not files in the wiki. Aliases are proposed, not yet written. At least one planned claim in a representative session routes to multiple targets (covering the common case of a claim that concerns two named entities, e.g., a character doing something at a location).

### Phase 4: Extractor + review

- Stage 3 extractor with parallelized proposals, runs automatically after planner
- Reduced preamble for extractor
- Windowed transcript input per proposal, driven by `locator_hint` from the plan; fallback to parent-segment window on substring-verification miss, with `hint_widened` logged on the proposal
- Claim-group deduplication: extractor runs once per `claim_group_id` and copies the located span to all sibling proposals
- Post-extraction mechanical verification that `raw_transcript_span` is a real substring
- Terminal review loop with approve / edit / reject actions, with claim-group siblings shown contiguously
- **Atomic entity stub creation on first approval for a proposed new entity**, with `created_by_ingest` set to the current ingest
- Routing metadata surfaced per-proposal (matched_via, new-vs-existing, proposed aliases, "created earlier this session" for in-review creations, sibling targets for multi-target claims)
- Inline alias-confirmation sub-prompt; aliases merged into stub at creation or appended on update
- In-memory entity index refresh after each approval
- Per-fact approval → fact appended to entity YAML, carrying `claim_group_id` when applicable
- Handling for edited text (edits scoped to the proposal being edited, not propagated to claim-group siblings)
- `replan` command (discard unreviewed proposals, re-run planner + extractor; already-approved entities visible to the new plan as existing)
- `reject-ingest` command (removes facts and any entity stubs left empty as a result)

Exit criterion: end-to-end ingest produces approved facts in entity YAMLs, with confirmed aliases and routing decisions visible at review time. Rejecting all proposals for a proposed new entity leaves no trace in the wiki. A multi-target claim produces sibling facts on multiple entities sharing a `claim_group_id`; independently rejecting one sibling leaves the others intact.

### Phase 5: Summarizer

- Stage 4 summarizer with status-aware rendering
- Section normalization (case-insensitive grouping of free-text section names)
- Integration with fact approval flow (batched regeneration at session end)
- `wiki rebuild` command to regenerate all summaries from scratch
- `promote-correction` command (with `first_seen_in` / `also_seen_in` tracking)

Exit criterion: entity `.md` files render cleanly, citations link to sources, status sections correctly grouped.

### Phase 6: Web UI (reader)

- Minimal HTTP server
- Markdown rendering with working footnote links
- Sidebar navigation by category
- Clickable cross-links and citations

Exit criterion: wiki browsable in a browser.

### Phase 7: Web UI (reviewer)

- Reading review with optional substage views (structure and claims surfaced separately when helpful; unified markdown view as default; coverage-gap warnings highlighted)
- Fact review (side-by-side raw vs. proposed, inline edit, one-click playback, inline alias confirmation, new-entity-on-approval behavior preserved)
- Plan inspection view (read-only; shows routing decisions and their rationale for debugging)

Exit criterion: terminal review no longer needed for daily use.

## Deferred / out of scope

Not in MVP; add only when proven necessary:

- Plain-text and markdown source types as first-class (MVP: only YouTube; plain text via file path works but with minimal handling)
- **Retrieval-based entity context.** When a wiki grows large enough that the full entity index exceeds the preamble token budget, the replacement is embedding-based retrieval of entities plausibly relevant to the current source (matched against transcript text), not truncation. Trigger: token-budget failure in preamble assembly. Until that trigger fires on a real wiki, the full-index approach is simpler and higher-quality.
- Fuzzy/phonetic entity matching beyond entity-index lookup
- Status-change proposals (contradiction detection)
- Automatic alias promotion from mention frequency
- Dedicated merge/split commands (handle by editing YAML by hand and setting `superseded_by` if needed)
- Per-category controlled section vocabularies in `.wiki-context.yaml` (MVP uses case-insensitive normalization of free-text sections)
- Configuration UI beyond a config file
- Multi-user collaboration / locking
- Non-English content
- Sources beyond YouTube + text files

## Key design principles

A terse index of principles argued elsewhere in the spec:

- **Mechanical guarantees where cheap, human verification where not.** Verbatim span extraction is mechanically verifiable; epistemic status is not.
- **Missed claims are worse than spurious ones.** The pipeline tilts toward over-inclusion at every stage. See Stage 1 “Design drivers.”
- **Review is over claims, not transcript.** Segment titles are the scope-audit layer; bullets are the claim-review layer. See Stage 1.
- **Raw evidence preserved at every stage.** Transcripts untouched; facts store their raw transcript span; audit trails everywhere.
- **The wiki filesystem only ever reflects human-approved state.** Pending proposals live in `~/.auto-lorebook/pending/`. See “Fact review.”
- **Compounding corrections.** Name corrections accumulate in `.transcription-corrections.yaml`; aliases accumulate on entity YAMLs.
- **The YAML is truth; the markdown is a view.** Hand-edits to markdown are overwritten on regeneration.
- **Two review gates, not three.** Reading approval and fact approval. The planner runs between them without a gate; its failure modes surface during fact review, with `replan` as the escape hatch.
- **Every proposal gets a decision.** No skip, no defer — approve, edit, or reject.
- **Filesystem is the registry.** Entity identity lives in entity YAMLs; no separate index file.