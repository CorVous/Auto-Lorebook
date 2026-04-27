# First ingest

This walkthrough takes a YouTube URL from raw source to approved entity
facts.

## 1. Ingest the source

```bash
auto-lorebook ingest https://youtube.com/watch?v=abc123
```

The tool fetches the transcript via `yt-dlp`, stores it under
`sources/yt-abc123/`, and prints what it captured:

```
✓ Manual subtitles available (English)
Source stored as yt-abc123.
  Title: Aether Chronicles S3E14
  Duration: 2:20:32
  Captions: manual (English)
```

If only auto-generated captions are available, the tool warns that
proper-noun mishearings are likely and suggests adding name corrections
during reading review.

## 2. Fill in context

The tool prompts interactively. Every field is skippable; defaults come
from flags, then `.wiki-context.yaml`, then your last ingest.

```
Session date (YYYY-MM-DD): 2026-01-15
Perspective (e.g. "Cor playing Kiki"): Cor playing Kiki in Aether Chronicles
Source nature [actual-play/dm-lore/worldbuilding-video/interview/notes/other]: actual-play
Setting [Aether Chronicles]:
Any notes? (one line, or Enter to skip): Picks up mid-session after a long rest.

Context saved to sources/yt-abc123/info.yaml.

Generate reading now? [Y/n]: y
```

Context fields are optional — blank is allowed. Unfilled fields reduce
LLM quality but don't block the pipeline. See
[context pipeline](../pipeline/context.md) for schema details.

## 3. Review the reading

After Stage 1a (structure) and Stage 1b (summarize) run, you get a
draft at `pending/yt-abc123/reading/reading.md` — a segmented,
attributed, claim-bulleted version of the transcript. Open the
interactive review:

```bash
auto-lorebook approve-reading yt-abc123
```

The session prints the draft and prompts:

- `a` — approve (flips status, copies to wiki, kicks off plan + extract).
- `e` — edit in `$EDITOR`. Fix:
    - Segment boundaries or titles.
    - Speaker attributions.
    - Claim bullets that don't match what was said, are invented, or
      are missing.
    - `name_corrections` frontmatter map.
- `r` — queue the draft for deletion.
- `u` — restore the draft to its session-start contents and clear any
  queued reject.
- `q` — quit; commits a queued reject (after a `y/N` confirm) or
  exits cleanly.

Pass `--yes` to skip the loop and auto-approve (required for
scripted/CI runs). See [reading stage](../pipeline/reading.md) for
deeper treatment.

## 4. Review facts

The planner and extractor run automatically after reading approval. To
walk through proposed facts:

```bash
auto-lorebook review ingest-2026-04-20-a
```

Each proposal shows the claim text, the raw transcript span it came
from, corrections applied, source locator, and routing rationale.
Decide per proposal: **approve**, **edit**, **reject**, or **play**
(opens the source at the right timestamp to verify). There is no skip —
every proposal gets a decision.

First approval of a claim targeting a new entity creates the entity
stub atomically. If review reveals systematic routing errors, bail out
with `auto-lorebook replan <ingest_id>` instead of fighting proposal by
proposal. See [fact review](../pipeline/review.md).

## 5. View the wiki

Approved facts land in `<category>/<slug>.yaml`; the summarizer
regenerates `<category>/<slug>.md` with citation-backed prose. Browse
either directly, or via the web UI once implemented:

```bash
auto-lorebook wiki show Aldara
auto-lorebook wiki list --category locations
```

See [summarizer stage](../pipeline/summarizer.md) for how YAML facts
become readable prose.
