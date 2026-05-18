# Context pipeline

LLM output quality depends heavily on context that can't be inferred
from a transcript alone. The pipeline accumulates context from two
sources: per-ingest (`info.yaml`) and wiki-wide (`.wiki-context.yaml`),
plus global transcription corrections and the entity index. These are
combined deterministically into a preamble fed to every LLM stage.

## Source ingestion

### Supported inputs

- YouTube URL → tool fetches transcript via `yt-dlp`.
- Local SRT file + `--source-url` flag → tool uses the file as the
  transcript.
- Plain text or markdown file → tool treats the file as the raw source.

### YouTube fetch behavior

Uses `yt-dlp` as a subprocess. Prefers manual captions over
auto-generated. Captures only transcript (SRT), title, and duration.

Does **not** capture upload date. Session dates are manual — upload
date and session date can diverge significantly for recorded
worldbuilding content.

Reports caption type to the user:

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

- YouTube: `yt-<video_id>` (e.g., `yt-abc123`).
- Local SRT: `srt-<short_content_hash>` or user-provided via
  `--source-id`.
- Text file: `txt-<short_content_hash>`.

`<short_content_hash>` is the first 10 hex characters of the SHA-256
of the raw file bytes. Hashing content rather than path means the same
file at different paths resolves to the same source ID. Whitespace-
only changes produce a new ID — intentional, since the transcript is
what every downstream artifact is hashed against.

Collision probability at 10 hex chars (40 bits) is negligible for
expected wiki sizes; if two distinct source files ever collide,
`--source-id` provides an explicit override.

Re-ingesting the same source produces no duplicates; the tool detects
and refuses.

## Per-source context (`info.yaml`)

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
  speakers:                    # optional; one-off speakers not in .wiki-context.yaml
    - name: Finn
      role: guest-player
      character: Brannoc
  notes: "Picks up mid-session after a long rest in the Dusk Marches."
```

All `context` fields are optional. Blank is allowed. Fields left
unfilled reduce LLM quality but don't block the pipeline.

The URL is the backbone of the citation system. All downstream stages
reference source URLs via `source_id` lookup.

## Wiki-level context (`.wiki-context.yaml`)

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

All fields optional. An empty `.wiki-context.yaml` is fine; the tool
degrades gracefully.

## Preamble assembly

Before invoking any LLM stage, the tool assembles a context preamble.
Assembly is deterministic — no LLM involvement.

Contents, in order:

1. Per-source context from `info.yaml` (perspective, source_nature,
   session_date, speakers, notes).
2. Setting context from `.wiki-context.yaml` (name, description,
   naming conventions, interpretation defaults).
3. Global transcription corrections from
   `.transcription-corrections.yaml`.
4. **Complete entity index** — every entity in the wiki, listed as
   canonical name + category + aliases. Built in-memory by scanning
   all `<category>/<slug>.yaml` files at command start. No truncation.

Stage-specific task instructions follow the preamble.

The reading substages (1a and 1b) and the planner receive the full
preamble and run on the primary model. The extractor receives a
reduced preamble — transcription corrections and entity aliases only.
It doesn't need setting lore or interpretation defaults, and narrower
context reduces the risk of the extractor "improving" text beyond
literal correction.

### Skeleton

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

### Token budget

If the assembled preamble would exceed a configurable fraction of the
model's context window (default: 80%, leaving room for the transcript
and response), the tool fails with a clear error naming the oversized
component and suggesting remedies:

1. Switch to a larger-context model in `config.yaml`.
2. Trim the named component (e.g., `.wiki-context.yaml`, transcription
   corrections).
3. Enable retrieval mode for the entity index (deferred; see
   [roadmap](../roadmap/index.md)).

The tool does not silently truncate.

## Interactive context step

After fetching the source, the tool prompts interactively. Every field
is skippable (Enter to skip). Defaults pre-fill from flags, then
`.wiki-context.yaml`, then `<wiki>/.wiki-state/last-context.yaml`
(perspective and source_nature from the most recent ingest).

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

Speakers are not prompted for at ingest time. They're defined once in
`.wiki-context.yaml` as `recurring_speakers` and reused across all
sources. Per-source speaker variation (guest players, one-off NPCs)
can be added to `info.yaml` manually if needed.

After context is captured, the tool offers to run the reading pipeline
immediately. Declining leaves the source at "context captured, reading
not yet generated"; the user runs `auto-lorebook generate-reading
<source_id>` manually later.

## Flags and non-interactive mode

Flags override their corresponding prompts and skip them entirely. If
every field is supplied by flags, the interactive step is skipped.
`--no-interactive` skips all prompts and uses only flag values,
leaving the rest blank.

- **Non-interactive environments** (no TTY, piped stdin, CI): detected
  automatically; falls back to `--no-interactive` behavior with a
  notice.
- **User aborts mid-prompt** (Ctrl-C): partially-captured context is
  saved to `info.yaml`; the tool prints where and exits cleanly.
- **Invalid input**: re-prompts with a hint ("Expected YYYY-MM-DD,
  got 'yesterday'"). Enter still skips.

To re-run the prompts for an existing source (filling in skipped
fields, correcting mistakes):

```bash
auto-lorebook configure-context <source_id>
```
