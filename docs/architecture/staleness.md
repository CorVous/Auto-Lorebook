# Artifact staleness

Every generated artifact in the pipeline records the inputs that
produced it, so the tool can detect when an artifact has gone stale —
an input has changed since the artifact was produced — and surface the
right remedy. Without this, editing an approved reading after planning,
or changing `.wiki-context.yaml` mid-review, produces silent
inconsistency between artifacts the tool treats as coherent.

## Input hashes on artifacts

Every LLM-generated artifact carries an `inputs` block with SHA-256
hashes of each file-level input plus the model identity:

```yaml
inputs:
  transcript_sha256: a1b2c3...           # sources/<id>/transcript.en.srt
  info_sha256: d4e5f6...                 # sources/<id>/info.yaml
  wiki_context_sha256: 7g8h9i...         # .wiki-context.yaml
  corrections_sha256: jk0lm1...          # .transcription-corrections.yaml
  entity_index_sha256: n2o3p4...         # canonical serialization of the in-memory index
  preamble_sha256: q5r6s7...             # the fully-assembled preamble string
  model: anthropic/claude-sonnet-4-5
  model_params_sha256: t8u9v0...         # temperature, max_tokens, etc.
generated_at: 2026-04-20T14:32:00Z
```

Hashes are over the raw file bytes. Whitespace-only edits register as
changes and trigger regeneration — intentional, since the cost of a
spurious regenerate is low and canonicalization bugs are a real risk.
Hashes are recorded on `structure.yaml`, `reading.md` (draft and
approved), `plan.yaml`, each `proposals/*.yaml`, and on entity facts
at approval time.

The entity index is in-memory, not a file. Its hash is computed over a
canonical serialization: entries sorted by category then slug, each
entry rendered as `{canonical_name, category, aliases (alias names
sorted, provenance fields excluded), superseded_by}`, emitted as a
single normalized YAML string which is then hashed. Alias provenance
is deliberately excluded — when a name became an alias is metadata;
whether it is an alias is what matters to downstream stages.

## Dependency table

| Artifact | Invalidated when any of these change |
|---|---|
| `structure.yaml` | transcript, `info.yaml`, `.wiki-context.yaml`, `.transcription-corrections.yaml`, entity index, model, model params |
| `reading.md` (draft) | everything above, plus `structure.yaml` |
| `reading.md` (approved) | same as draft (but staleness is a warning, not a blocker — see below) |
| `plan.yaml` | approved `reading.md`, `.wiki-context.yaml`, entity index, model, model params |
| `proposals/*.yaml` | `plan.yaml`, approved `reading.md`, transcript, `.transcription-corrections.yaml`, model, model params |
| entity `.md` (summary) | entity's own facts, linked entities' facts, entity index, `.wiki-context.yaml` setting, model, model params |

The preamble hash is derived from several of these inputs; it's
recorded for debugging but the individual input hashes determine
staleness.

## Entity index: session-scoped, not globally consistent

The entity index changes whenever any entity YAML is written —
including during fact review, when approving a proposal for a new
entity creates a stub. Strict invalidation would mean every approval
during review stales all other pending artifacts in every in-flight
ingest across the wiki, which is hostile to normal use.

Instead, the entity index hash on a pending artifact is compared
against the index as it was _at the start of the current stage's run_,
not the live index. Within a single review session, entities created
earlier in the session do not invalidate proposals generated earlier
in the session — consistent with the "in-memory entity index refresh
after each approval" behavior, which treats the index as a running
context rather than a snapshot. Staleness detection against the index
fires only across session boundaries, or when another ingest has
written entity YAMLs that the current ingest's planner didn't see.

## How staleness surfaces

Three tiers by pipeline position.

### Pending artifacts (unapproved)

The tool refuses to consume a stale artifact and names the remedy.

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

The refusal names the specific input that changed, so the user knows
which regenerate command to run and at which `--from` point.

### Approved reading with stale upstream

Warning, not blocker. The reading approval is a human commitment;
upstream edits (a new `.wiki-context.yaml`, new entries in
`.transcription-corrections.yaml`) do not silently revoke it.

```
⚠ Approved reading for yt-abc123 was generated against an older
  .wiki-context.yaml. Downstream stages will use the current version.
  To regenerate the reading against current context:
    auto-lorebook regenerate-reading yt-abc123 --from=structure
```

### Approved facts (entity YAMLs)

No warning at read time — approved content is past the gate. The
`inputs` snapshot on each fact is an audit artifact: it supports
queries like "which facts were extracted against a
`.transcription-corrections.yaml` predating correction X?" when the
user wants to decide whether to re-examine historical approvals. No
command uses staleness of an approved fact to gate behavior.

## Integration with commands

- `regenerate-reading` and `replan` are the remedies the tool
  suggests when pending artifacts are stale. Both write a fresh
  `inputs` block on their outputs.
- `approve-reading` records the inputs-at-approval-time on the
  approved reading, which becomes the reference point for downstream
  stages.
- `reject-ingest` is unaffected — it works by `created_by_ingest`,
  independent of hashes.
- `wiki rebuild` skips regeneration for entity `.md` files whose
  recorded inputs hash (stored in `entity_page_staleness` keyed by
  `(category, slug)`) matches the current inputs. Inputs are: entity's
  own facts, linked entities' facts, entity index, `.wiki-context.yaml`
  setting, model, model params. `wiki rebuild --force` regenerates
  unconditionally. Hashes are stored only in the DB, not in `.md` files.
- `replan` preserves approved proposals as facts in entity YAMLs,
  with their original `inputs` snapshots intact. Re-planning does not
  retroactively "update" historical facts to reflect the new input
  state — that would discard the audit trail.

## What this does not catch

**Model non-determinism.** Identical inputs can produce different
outputs on re-run. Hashes detect input changes, not output
equivalence. If the user wants to force regeneration despite matching
hashes, `regenerate-reading` and `replan` run unconditionally;
hash-matching only suppresses automatic staleness warnings, never
overrides an explicit regenerate command.

**Manual edits to intermediate YAML artifacts** (`structure.yaml`,
`plan.yaml`) will not change the recorded `inputs` block, so
downstream stages won't detect the edit as staleness. Intermediate
artifacts are not intended as hand-edit surfaces — the designated
hand-edit surfaces are `reading.md` before approval and entity YAMLs
after approval. Users who hand-edit intermediate artifacts are on
their own.
