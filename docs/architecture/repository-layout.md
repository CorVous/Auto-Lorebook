# Repository layout

Auto-Lorebook operates on two separate locations: the wiki repo
(human-approved state) and the tool's state directory (pending work).
The separation is deliberate — the wiki filesystem only ever reflects
human-approved state, so browsing it shows canon and only canon.

## Wiki repo

Any directory you point the tool at:

```
<wiki-repo>/
  .transcription-corrections.yaml   # global phonetic / mishearing fixes
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

Entity identity lives entirely in entity YAMLs. An entity exists iff
`<category>/<slug>.yaml` exists. No separate registry file — see
[entity model](entity-model.md).

## Tool state directory

`~/.auto-lorebook/`:

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

Pending state persists across sessions. An ingest can be started,
paused mid-review, and resumed.

## Lifecycle

- **Ingest start.** Transcript, `info.yaml`, and (eventually)
  `reading.md` land under `<wiki-repo>/sources/<source_id>/`.
  Intermediate reading artifacts (`structure.yaml`, draft
  `reading.md`) live under `pending/`.
- **Reading approval.** `reading_status` in the draft frontmatter
  flips to `approved` and the approved `reading.md` is committed to
  the wiki alongside the raw transcript. `structure.yaml` is retained
  as an audit artifact for the lifetime of the ingest.
- **Planning + extraction.** Produces `pending/<ingest_id>/plan.yaml`
  and one YAML per proposed fact under
  `pending/<ingest_id>/proposals/`.
- **Fact review.** Approvals append to (or create) entity YAMLs
  under `<category>/<slug>.yaml`. Rejected proposals are discarded.
  If every proposal for a new entity is rejected, no stub is ever
  written.
- **Ingest complete.** When all proposals are decided, the ingest's
  pending directory is discarded. The audit trail lives on in
  `created_by_ingest` and `approved_at` fields on the resulting
  facts.

## QA fixtures

Synthetic stage-input artifacts ship with the package under
`src/auto_lorebook/_qa_fixtures/<name>/` and are loaded via
`importlib.resources` by the `seed-ingest` command. See
[QA seeding](../contributing.md#qa-seeding).

## Why the split

Keeping pending state outside the wiki repo means:

- Browsing the wiki never surfaces unapproved content.
- `git log` on the wiki repo (if you use git) shows only approved
  state changes — no churn from LLM retries.
- `reject-ingest` has a clean blast radius: remove everything tagged
  with the ingest's ID, done.
