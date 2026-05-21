# Stage 1: Reading

The reading stage runs two LLM substages in sequence. 1a (structure)
segments the transcript and attributes speakers in a single pass. 1b
(summarize) produces claim bullets per segment. The human reviews the
combined output as a single reading — one review gate.

> Test this stage in isolation with
> `auto-lorebook seed-ingest --at=structure` (Stage 1a + 1b) or
> `--at=summarize` (Stage 1b only). See
> [QA seeding](../contributing.md#qa-seeding).

## Design drivers

Two properties of the intended use dominate the design of this stage:

1. **The human reviews claims, not transcript.** A two-hour actual-play
   VOD at a realistic review budget (10–20 minutes per hour of footage)
   means the human cannot read the full transcript. The review surface
   is claim bullets with localized timestamps and context windows;
   everything else is scaffolding for producing good bullets.
2. **Missed claims are worse than spurious ones.** An omitted claim in
   a one-shot ingest is a permanent gap nothing downstream will
   surface. A spurious claim costs the human seconds to reject. The
   pipeline tilts toward over-inclusion — but only *within* the fact
   category: surface borderline lore and let the human filter. It never
   licenses the session's own play-narrative as a claim (see Stage 1b).

These drive three design decisions: 1a covers the whole transcript (no
scope filter); segmentation and attribution run as one pass; a
mechanical gap check sits between 1a and 1b.

## Stage 1a: Structure

**Purpose.** Segment the full transcript by topic and attribute
speakers in a single pass — with sub-segment overrides where speakers
change mid-segment — and flag uncertainty. Segmentation and attribution
are combined because topic boundaries and speaker changes are heavily
correlated in actual-play content and line content is a strong
attribution signal. Splitting them across two passes throws away
information the joint pass has.

Segments are contiguous and cover the whole transcript — every moment
belongs to some segment. If the pass cannot identify a topic for a
stretch (long pause, unintelligible audio), it still emits a segment
with an appropriate title ("silence", "inaudible"): explicit is better
than implicit.

**Input.** Raw transcript (after literal-substitution corrections
applied from `.transcription-corrections.yaml`) and the full preamble
(including `recurring_speakers` and `interpretation_defaults`).

**Output.** Stored in `wiki.db` (`segments` table, one row per segment).
The structure below shows the logical shape:

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

**Mechanical checks.** Segment start/end correspond to real transcript
timestamps. Segments cover the full transcript duration without gaps.
Override ranges fall within their parent segment. Uncertainty flag
locators fall within some segment.

**Uncertainty over-flagging.** The prompt instructs the model to err
on the side of flagging — dismissing a flag costs seconds; a silently-
swallowed uncertain name pollutes a downstream fact.

## Mechanical gap check

After 1a completes, a deterministic check (no LLM) identifies any
contiguous transcript stretch longer than a configurable threshold
(default: 5 minutes) whose segments all have thin claim-bearing
signals: titles matching patterns like "rules discussion", "break",
"off-topic", "silence", or segments with `notes` suggesting low yield.

This is a heuristic sanity check — the tool does not act on it, only
surfaces it in reading review:

```
⚠ Possible coverage gap:
  0:34:10–0:48:22 covered only by segments titled
  "Pizza discussion", "Break", "Rules: initiative".
  If this stretch contained worldbuilding, regenerate with a hint.
```

The human confirms the stretch is genuinely low-yield or regenerates
1a with a hint about what to look for.

Gap warnings are derived at read time by running the gap check over the
stored structure — not persisted separately. They are re-rendered below
the segment list in the approve-reading outer view on each session.

## Stage 1b: Summarize

**Purpose.** For each segment from 1a, produce claim bullets — or
explicitly none. This is the only substage that can invent content.

**What counts as a claim.** A claim is a *fact candidate* — standing
knowledge about the setting (history, geography, peoples, factions,
religions, powers, items) or about a character who inhabits it. In-world
history counts, including events from the world's past. When session
play changes the world's durable state, 1b extracts the resulting
state, not the action behind it — "King Theron is dead", not the
dagger-throw and dice roll that killed him.

1b does **not** extract the session's own play-narrative: combat
blow-by-blow, dice and mechanics, the party's moment-to-moment actions
and travel, scene blocking, or out-of-character table talk. A combat or
travel segment typically yields no bullets. Recapping what happened in
a session is a separate concern — not the reading stage's job.

**Input.** Segmented, speaker-attributed transcript from 1a and the
full preamble (including `interpretation_defaults`).

**Output.** Stored in `wiki.db`:
- `segment_bullets` table (one row per bullet, keyed to the `segments` table).
- `ingests` table row updated with `default_speaker`, `name_corrections_json`,
  `session_date`.

The wiki-side `reading.md` is assembled from these at approval time,
not written during generation.

**Per-segment extraction.** 1b processes each segment independently
(trivially parallelizable). Empty bullet lists are allowed and
expected — a "Rules discussion: grappling" segment typically yields no
bullets, and that's the correct output. The bullet list's emptiness is
information at review time.

### Locator hints for downstream stages

Alongside each bullet, 1b emits a `locator_hint` range — a small
window around the bullet's anchor timestamp that downstream stages can
use to narrow search. The hint is internal pipeline metadata: it flows
from 1b through the planner into the extractor and is never surfaced
in `reading.md`.

Shape, per bullet:

```yaml
bullet_index: 0
text: "King Theron's grandfather founded Aldara in the Second Age"
anchor: "0:04:32"                  # the point timestamp shown in reading.md
locator_hint: "0:04:25-0:04:50"    # search window for Stage 3
```

The hint is a window, not a precise range: 1b picks an anchor that's
approximately where the claim lands and pads it generously
(default ±15s). The authoritative locator on the final proposal is
produced by [Stage 3](extractor.md), not by this hint.

The `locator_hint` window is stored alongside each bullet in
`segment_bullets.locator_hint` and passed to the extractor; it is not
surfaced in `reading.md`.

**Anchor tolerance.** When an LLM returns an anchor a few seconds
outside a segment's bounds — common with plain-text (.txt) sources
where Stage 1a invents second-based bounds — Stage 1b clamps the
anchor to the nearest boundary rather than failing. Anchors within
`DEFAULT_ANCHOR_TOLERANCE_SECONDS` (default 2.0s) of a boundary are
silently clamped and a warning is logged; anchors further outside
still raise `Stage1bError`. The `anchor_tolerance_seconds` kwarg on
`run()` overrides the default when needed.

## Reading assembly

At approval, the wiki-side `reading.md` is assembled from the DB (segments
+ bullets + sidecar). The assembled document interleaves segment headers
(from 1a) with their bullet lists (from 1b):

```markdown
---
schema_version: 1
source_id: yt-abc123
source_name: "Worldbuilding Session 3: The Founding of Aldara"
source_url: https://youtube.com/watch?v=abc123
source_type: youtube
session_date: null              # human fills in during review
ingested_at: 2026-04-20T14:35:12Z
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

Uncertainty flags from 1a are preserved in the assembled reading as
inline markers the human can resolve. Segments with no extracted
claims are rendered with an explicit "No claims extracted" marker so
that empty segments are visible rather than invisible — the marker
lets the human notice a segment that _should_ have contributed but
didn't.

See [timestamps](../architecture/timestamps.md) for how timestamps
render as clickable links.

## Name corrections

When the human notices a mishearing (e.g., "Fair-on" should be
"Theron"), they add it to the `name_corrections` map via the `[m]` meta
editor rather than find-replacing throughout the reading. The tool
applies the substitutions during rendering and passes the map to
downstream stages. Corrections are preserved across regenerations
(stored in `ingests.name_corrections_json`).

Corrections from approved readings can be promoted to the global
`.transcription-corrections.yaml` so future sources benefit
automatically — see
[entity model](../architecture/entity-model.md#promotion).

## Uncertainty flags

1a flags words, names, or attributions it's unsure about. Uncertainty
flags appear inline in the assembled reading:

```markdown
- [0:05:47] A proper noun here was unclear; appears to be a place name starting with V
```

The human resolves by listening to the audio (or using setting
context), then replaces with the correct content.

## Reading review

The reading-review engine operates over segments stored in `wiki.db`.
Each segment carries one of four statuses:

| Status | Meaning |
|--------|---------|
| `draft` | Fresh — not yet decided. |
| `accepted` | Reviewer approved; included in the assembled reading. |
| `skipped` | Reviewer skipped; body replaced with the "no claims" marker in the assembled reading. |
| `regenerating` | Flagged for re-summarisation (slice #5); blocks the gate. |

**Deferred-commit semantics.** The engine accumulates pending marks in
memory during the walk — no DB writes until the reviewer commits.
On commit, status updates are written to the `segments` table in one
transaction, then the gate predicate is evaluated.

**Gate predicate.** Every segment is `accepted` or `skipped`. When the
gate fires, `reading_assembly.assemble` renders the wiki-side
`reading.md` and writes it to
`<wiki-repo>/sources/<source_id>/reading.md`. The presence of this
file is the approval artefact — there is no `reading_status`
frontmatter flag.

**Decision verbs:** `accept`, `skip-bullets`,
`regenerate-again` (queue segment for quit-time re-summarisation; marks
segment `regenerating`, blocks gate, triggers parallel Stage 1b call on
`[q]uit`),
`undo` (clears the pending mark for one segment), `commit` (the quit path
that writes and evaluates the gate).

All committed status changes are produced by the reading-review engine; the
command layer only translates keystrokes into engine decisions.

```bash
auto-lorebook approve-reading <source_id> --yes
```

`--yes` drives an `AutoAcceptReviewer` that marks every still-`draft`
segment `accepted` and commits unconditionally. The gate always fires
for fixtures where every segment is decidable.

`approve-reading` opens a hierarchical interactive session over the draft.

**Outer view** — numbered list of all segments with their current status and
any pending mark for the session:

| Key | Action |
|-----|--------|
| `#` | Open the numbered segment in the per-segment prompt. |
| `n` | Jump to the next undecided `draft` segment. |
| `m` | Open the sidecar (`ingests` row fields) in `$EDITOR` via a temp file. |
| `q` | Commit pending marks. If every segment is now decided, write wiki-side `reading.md` (gate fires). |

Below the segment list, any persisted gap-check warnings are rendered as
⚠ Possible coverage gap: blocks (one per stretch, transcript order).

**Per-segment prompt** — shows segment body (up to 60 lines) and current /
pending status. Each claim bullet is followed by the verbatim transcript
cues behind it — the cues overlapping that bullet's `locator_hint` window —
so the reviewer can compare the claim against what was actually said and
against the clickable source timestamp without leaving the terminal. The
transcript shown is the raw (globally-corrected) transcript, not the
reading's `name_corrections`-applied text, so mishearings stay visible.
Plain-text sources (no cue timing) and missing transcripts fall back to
plain bullets:

| Key | Action |
|-----|--------|
| `a` | Accept: queue this segment for `accepted` status; return to outer. |
| `s` | Skip-bullets: queue this segment for `skipped` status; return to outer. |
| `g` | Regenerate-again: queue this segment for `regenerating` status; on `[q]uit`, this segment is re-summarised in parallel against a snapshot of accepted segments and returns to `draft` for re-decision. |
| `e` | Edit: open a display snapshot of the segment in `$EDITOR` (display only — bullets come from DB). Stays in per-segment prompt on return. |
| `u` | Undo: clear this segment's pending mark. Stays in per-segment prompt. |
| `b` | Back: return to outer without changing any pending mark. |

Pending marks live in memory until `[q]`. On `[q]`, the engine commits
all marks in one transaction and evaluates the gate. Ctrl-C at any
prompt exits 130 with no committed mutations; pending marks are not
persisted.

The outer segment list shows `→regenerating` for pending regenerate-again
marks.

## Quit-time regeneration batch

When `[q]` commits and at least one segment has status `regenerating`, the
engine returns a `RegenBatch` instead of (or alongside) the gate check.
The gate cannot fire on the same quit that includes regenerating segments —
`regenerating` is not a decided status.

**Snapshot.** After the commit-write loop, the pipeline takes a snapshot
of all committed segments with status `accepted`. This snapshot becomes
the accepted-context for every re-summarised segment; flagged segments do
not see each other's regenerations.

**Stage 1b user message for a regen call.** The system preamble is
unchanged. The user message gains a compact accepted-segments block before
the target segment's transcript slice:

```
Accepted segments (context only — do not re-extract):

## seg-001 [0:00:00–0:02:15] Introduction (DM)
- Intro bullet [0:00:15]

---

Segment seg-002: "Rules discussion"
Range: ...

Transcript for this segment:

<sliced transcript>
```

**After regen.** Regenerated segments' `segment_bullets` rows are
replaced in `wiki.db`; status is reset to `draft` for re-decision in
the next review session.

**Exit message.** `[q]` with a regen batch prints "Still N undecided" —
the gate cannot fire on the same quit that regenerates.

`--yes` skips the loop and auto-approves; required for non-TTY runs
(scripts, CI).

After approval, the reading is committed to the wiki alongside the
raw transcript. The intermediate reading state (`segments`,
`segment_bullets`, `ingests` rows) is retained in `wiki.db` so that
`regenerate-reading` and `replan` can operate without re-running prior
stages. Future re-runs of extraction operate on the approved reading.

Running Stage 2 and Stage 3 is done via separate commands after
approval — `auto-lorebook plan <id>` and `auto-lorebook extract <id>`.
See the [CLI reference](../reference/cli.md) for details.

## Regenerating substages

If reading review reveals the structure (segmentation or attribution)
is badly wrong in ways that are tedious to fix by hand, re-run from a
given point:

```bash
auto-lorebook regenerate-reading <source_id> --from=structure   # reruns 1a, 1b
auto-lorebook regenerate-reading <source_id> --from=summarize   # reruns 1b only
auto-lorebook regenerate-reading <source_id> --from=summarize --segments seg-003,seg-007
                                                                # reruns 1b on listed segments only
```

Per-segment 1b regeneration is cheap because 1b is parallelized
per-segment; if one segment's bullets are clearly wrong but the rest
are fine, this leaves the rest of the review work untouched.

`name_corrections` in frontmatter are preserved across all
regenerations. Human edits to the reading body are preserved by
per-segment 1b regeneration but discarded by full-reading
regenerations. If hand edits are worth keeping, approve the reading;
if the machine output is too broken to edit, regenerate from scratch.

Next stage: [Stage 2 planner](planner.md).
