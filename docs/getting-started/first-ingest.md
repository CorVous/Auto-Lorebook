# First ingest

This walkthrough takes a YouTube URL from raw source to approved entity
facts.

## Quickstart: single command

```bash
auto-lorebook run https://youtube.com/watch?v=abc123
```

`run` detects where the source is in the pipeline and drives it forward
to completion — ingest, reading generation, reading approval, planning,
extraction, and fact review — stopping at each human gate.

For unattended or CI runs, pass both gate flags to skip the interactive
prompts:

```bash
auto-lorebook run https://youtube.com/watch?v=abc123 --yes --auto-approve
```

`--yes` auto-approves the reading review; `--auto-approve` auto-approves
all fact proposals. In a non-interactive shell, `run` refuses to proceed
past a gate unless the matching flag is supplied.

You can also resume a partially completed ingest by passing the source ID
instead of the URL:

```bash
auto-lorebook run yt-abc123
```

`run` skips any stages already complete and continues from where it left off.

## What happens at each gate

### Reading review (Stage 1 output)

After Stage 1a (structure) and Stage 1b (summarize) run, `run` opens the
interactive reading review. The session prints the draft and prompts:

- `a` — approve (flips status, copies to wiki).
- `e` — preview assembled draft in `$EDITOR` (read-only; edits are
  discarded). Edit segment files directly for now.
- `r` — queue the draft for deletion.
- `u` — restore the draft to its session-start contents and clear any
  queued reject.
- `q` — quit; commits a queued reject (after a `y/N` confirm) or
  exits cleanly.

Pass `--yes` to skip this loop and auto-approve. See
[reading stage](../pipeline/reading.md) for deeper treatment.

### Fact review (Stage 3 output)

After the planner and extractor run, `run` opens the fact review loop.
Each proposal shows the claim text, the raw transcript span it came
from, corrections applied, source locator, and routing rationale.
Decide per proposal: **approve**, **edit**, **reject**, or **play**
(opens the source at the right timestamp to verify). There is no skip —
every proposal gets a decision.

First approval of a claim targeting a new entity creates the entity
stub atomically. If review reveals systematic routing errors, bail out
with `auto-lorebook replan <ingest_id>` instead of fighting proposal by
proposal. See [fact review](../pipeline/review.md).

Pass `--auto-approve` to skip this loop and approve all proposals.

## View the wiki

Approved facts land in `<category>/<slug>.yaml`; the summarizer
regenerates `<category>/<slug>.md` with citation-backed prose. Browse
either directly, or via the web UI once implemented:

```bash
auto-lorebook wiki show Aldara
auto-lorebook wiki list --category locations
```

See [summarizer stage](../pipeline/summarizer.md) for how YAML facts
become readable prose.

## Running stages individually

If you need fine-grained control, you can run each stage as a separate
command. This is useful when resuming after an error, inspecting
intermediate artifacts, or running only part of the pipeline.

### 1. Ingest the source

```bash
auto-lorebook ingest https://youtube.com/watch?v=abc123
```

Fetches the transcript via `yt-dlp`, stores it under
`sources/yt-abc123/`, and prints what it captured. Then prompts for
context (session date, perspective, source nature, setting, notes) —
every field is skippable.

If only auto-generated captions are available, the tool warns that
proper-noun mishearings are likely and suggests adding name corrections
during reading review.

### 2. Generate and review the reading

```bash
auto-lorebook generate-reading yt-abc123
auto-lorebook approve-reading yt-abc123
```

`generate-reading` runs Stage 1a → 1b and writes a draft reading.
`approve-reading` opens the interactive review loop described above.

### 3. Plan and extract

```bash
auto-lorebook plan yt-abc123
auto-lorebook extract yt-abc123
```

`plan` routes claim bullets to entities and writes
`pending/yt-abc123/plan.yaml`; it refuses if the wiki-side `reading.md`
is missing. `extract` locates verbatim transcript spans for each
planned claim and writes proposal YAMLs under
`pending/yt-abc123/proposals/`; it refuses if `plan.yaml` is missing.

### 4. Review facts

```bash
auto-lorebook review ingest-2026-04-20-a
```

Opens the fact review loop described above.
