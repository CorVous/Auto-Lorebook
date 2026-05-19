# SQLite replaces YAML as the wiki store

## Context

The per-entity YAML model couldn't express fact identity across multiple entities without denormalization (N copies of the same fact sharing `claim_group_id`), couldn't answer relational queries without scanning every file, couldn't write a multi-target approval atomically, and had no story for typed fact cross-references like `supersedes` that need to flip another fact's status. Pending-state YAML moved under `<wiki>/.wiki-state/pending/` (ADR-0003) fixed portability but inherited the same shape problems for canon.

## Decision

Replace every wiki YAML file — canon and pending — with one SQLite database per wiki at `<wiki>/.wiki-state/wiki.db`, git-ignored. The schema covers canon (entities, aliases, facts, fact_targets, fact_refs, fact_status_history, sources, transcription corrections, wiki context) and pending pipeline state (ingests, segments, segment bullets, plan routes, proposals) in one file, so an approval is a single transaction: `INSERT INTO facts ... INSERT INTO fact_targets ... DELETE FROM proposals`. The browsable canonical surface becomes the Stage-4-regenerated `<category>/<slug>.md` summary files at the wiki root, which remain trackable in git. `auto-lorebook export` produces a YAML dump of the DB for inspection or backup. `~/.auto-lorebook/config.yaml` stays YAML — no relational content, and the API key must not live inside a shared wiki.

## Invariants the schema enforces

- **Supersedes auto-flip** — a `fact_refs` row of type `supersedes` from F_new to F_old automatically sets `F_old.status = 'disproven'` and appends a `system-ref-creation` entry to `fact_status_history`. Removing the edge restores the target's prior status from history and appends a `system-ref-deletion` entry. Enforced atomically inside the same transaction that writes the edge, not by application code racing on two updates.
- **Idempotent re-approval** — approving a proposal whose `proposed_id` already exists in `facts` is a silent skip. The proposal row is deleted in the same transaction that would otherwise insert the duplicate fact, so there is no window where both rows exist or both are missing.
- **`claim_group_id` scope** — `claim_group_id` is a column on `plan_routes` and `proposals` only; the `facts` table has no such column. Approval discards the routing identity once the fact is committed: a fact has N `fact_targets`, not N copies.
- **Entity↔entity via shared `fact_targets`** — there is no `entity_relations` table. Two entities are related iff at least one row of `fact_targets` ties each of them to the same fact; the kind and strength of the relationship is derived from `fact_targets` queries, not stored.

## Key schema decisions

- **`fact_targets`** join table (fact ↔ entity, M:N) replaces the N-copies-with-`claim_group_id` pattern.
- **`fact_refs`** typed edge table with `kind IN {supersedes, contradicts, corroborates, qualifies}`. Only `supersedes` mutates fact status; the other three record relationships without status side-effects.
- Entity↔entity relationships fall out as queries over shared `fact_targets` rows (see invariant above).

## Consequences

Positive:

- Multi-target approval is one SQLite transaction; no half-approved bundles on Ctrl-C.
- Fact identity is real (one row, many targets), so audit, dedupe, and cross-reference queries are trivial SQL.
- Typed cross-references (`supersedes`, `contradicts`, `corroborates`, `qualifies`) get schema-level support, including the auto-flip invariant.
- Pending and canon share one durable file: backing up a wiki backs up its in-flight reviews — the gap ADR-0003 noted.
- The visible/hidden line from ADR-0003 survives: the DB hides under `.wiki-state/`; the regenerated `<category>/<slug>.md` summaries are the visible canonical surface.

Negative:

- Hand-editing canon now requires CLI verbs (`auto-lorebook entity rename`, `fact edit`, `fact-ref add`, etc.); editing a YAML file is no longer the edit surface. `auto-lorebook export` is the inspection escape hatch.
- Schema migrations become a real concern; we need a numbered-migration framework (ADR doesn't re-derive it — see `docs/architecture/schema-versioning.md`).
- `wiki.db` is not human-mergeable. Conflicts between two machines need a merge strategy out of scope here.
- Tooling that grepped YAML no longer works against canon. The regenerated `.md` summaries cover read-only inspection; SQL covers everything else.

## Rejected alternatives

- **Per-entity YAML (status quo from ADR-0003)** — rejected because it forces N denormalized copies of every multi-target fact (one per entity dir) tied together only by a shared `claim_group_id`, makes "approve one bundle" a multi-file write with no atomicity, and has no clean home for typed cross-references like `supersedes` that need to flip another fact's status. Every relational query is a full filesystem scan.
- **Single-wiki YAML aggregate (one `wiki.yaml`)** — rejected because it solves atomic-write (one file, one write) but loses every other property: any edit rewrites the whole file (large diffs even when hidden from git), querying still means parsing the whole document, fact identity across entities is still denormalized unless you re-introduce a relational layer in YAML (at which point you've badly reinvented SQLite), and the file grows unboundedly across ingests.
- **Embedded key-value store (LMDB, sqlitedict, etc.)** — rejected because the workload is relational (M:N fact↔entity, typed edges between facts, plan/proposal/route joins), not key-value. SQLite is also a single file with no daemon and ships in the Python stdlib, so a KV layer would add a dependency without removing the need for a query and migration story.

## Also considered

- **SQLite as a derived cache, YAML stays truth** — rejected: doesn't solve fact identity or write atomicity, which were the primary drivers.
- **Split truth (entity metadata in YAML, facts in SQLite)** — rejected: two-truth coordination recreates the partial-write hazards we were trying to eliminate, and leaves entity↔entity relationship queries with no clean home.
- **YAML round-trip export as the hand-edit surface** — rejected: mapping arbitrary YAML hand-edits back into a normalized schema reliably is hard; the result is a worse SQLite with a YAML veneer. CLI verbs are the correct edit surface.

## What stays YAML

`~/.auto-lorebook/config.yaml` (the wiki registry, models, preamble budget, API key env-var name) stays YAML. It has no relational content and must not live inside any wiki that could be shared or pushed.

## Retired principles

The "filesystem is the registry," "YAML is truth," and "entity exists iff `<category>/<slug>.yaml` exists" principles from the original design are retired. ADR-0003's visible/hidden split (`.wiki-state/` hidden, summaries visible) is preserved; what changes is the storage format inside the hidden side. The browsable canonical surface remains the Stage-4-regenerated `<category>/<slug>.md` summary files. The DB itself is not in git; `auto-lorebook export` produces a YAML dump for inspection or backup.
