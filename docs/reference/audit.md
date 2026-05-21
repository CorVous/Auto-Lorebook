# Audit trail

Auto-Lorebook has no git dependency. The YAML files themselves are the
audit trail.

## Fields that preserve provenance

- **Every fact** carries `approved_at`, `created_by_ingest`,
  `edited_by_human`, `edited_at`, `corrections_applied`, and
  `status_history` (with actor).
- **Every entity** carries `created_at`, `created_by_ingest`,
  `updated_at`, and `superseded_by` for merges.
- **Every source** carries `fetched_at` and `session_date`.
- **Every alias record** carries `added_by_ingest`, `added_at`, and
  `source` (hand-edited, alias-confirmation, stub-creation,
  promoted-from-merge).
- **Every LLM-generated artifact** carries an `inputs` block with
  SHA-256 hashes of inputs and model identity.

See [entity model](../architecture/entity-model.md) for the full
field inventory and
[staleness](../architecture/staleness.md#input-hashes-on-artifacts)
for the hash model.

## Queries the audit trail supports

Because every artifact tags its origin, the audit trail naturally
answers questions like:

- "Which facts came from ingest X?" → filter by `created_by_ingest`.
- "Which facts were extracted against a
  `.transcription-corrections.yaml` predating correction X?" →
  compare `corrections_sha256` on each fact's `inputs` block.
- "What did this ingest add?" → facts and entities tagged with its
  ID.
- "When did this alias enter the wiki?" → `added_at` on the alias
  record.
- "Who approved this status change?" → `by` field in
  `status_history`.

## Rejecting an ingest

```bash
auto-lorebook reject-ingest <ingest_id>
```

Removes everything attributable to that ingest:

1. Removes all facts with matching `created_by_ingest` from every
   entity YAML.
2. Removes all alias records with matching `added_by_ingest` from
   every entity YAML. An entity whose canonical `name` is unaffected
   but whose aliases shrink is still considered modified;
   `updated_at` is bumped.
3. Removes any entity whose own `created_by_ingest` matches _and_
   whose `facts` list is now empty. Entities that were created by
   this ingest but have since received facts from other ingests stay
   (with the rejected facts and aliases removed).
4. Page reconciliation: the removed entities' `.md` pages are deleted
   and linked survivors (entities that shared a fact with a now-deleted
   entity) are re-summarized. When an API key is available the
   re-summarization uses the LLM page step; otherwise it falls back
   to the mechanical renderer. An entity that loses all its facts
   but is not deleted (created by a different ingest) gets a
   mechanical stub.

This gives a clean "what did this ingest add" answer: an ingest's net
contribution is exactly the facts tagged with its ID plus the entities
tagged with its ID.

## Replanning

`replan` discards only unreviewed proposals; it never deletes approved
facts or entity pages. Because no entity data is removed, there is
nothing to reconcile — no page deletion or re-summarization runs.

## What the audit trail does not do

- **Version history of edits.** Edits to fact `text` update
  `edited_at` and `edited_by_human` but do not preserve the prior
  text. If edit history matters, use `git` on the wiki repo as an
  external layer.
- **Output equivalence.** Hashes detect input changes, not LLM output
  equivalence. Identical inputs can produce different outputs on
  re-run. See
  [staleness: what this does not catch](../architecture/staleness.md#what-this-does-not-catch).
