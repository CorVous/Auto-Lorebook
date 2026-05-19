# SQLite replaces YAML as the wiki store

The per-entity YAML model couldn't express fact identity across multiple entities without denormalization (N copies of the same fact sharing `claim_group_id`), couldn't answer relational queries without scanning every file, couldn't write a multi-target approval atomically, and had no story for typed fact cross-references. We replace all wiki YAML files with one SQLite database per wiki at `<wiki>/.wiki-state/wiki.db`, git-ignored. The database covers both canon (entities, aliases, facts, sources, transcription corrections, wiki context) and pending pipeline state (ingests, segments, plan routes, proposals) so that approval is a single transaction: `INSERT INTO facts ... INSERT INTO fact_targets ... DELETE FROM proposals`.

## Key schema decisions

- **`fact_targets`** join table (fact ↔ entity, M:N) replaces the N-copies-with-`claim_group_id` pattern. `claim_group_id` survives only in the `proposals` table during ingest; approved facts carry no such field.
- **`fact_refs`** typed edge table (`supersedes | contradicts | corroborates | qualifies`). A `supersedes` edge automatically sets the target fact's status to `disproven` and appends to `status_history`; removing it restores the prior status.
- Entity↔entity relationships are not a first-class table — they fall out as queries over shared `fact_targets` rows.

## What stays YAML

`~/.auto-lorebook/config.yaml` (the wiki registry, models, preamble budget, API key env var) stays YAML. It has no relational content and must not live inside any wiki that could be shared or pushed.

## Retired principles

The "filesystem is the registry," "YAML is truth," and "entity exists iff `<category>/<slug>.yaml` exists" principles from the original design are retired. The browsable canonical surface is now the Stage-4-regenerated `<category>/<slug>.md` summary files at the wiki root, which remain trackable in git. The DB is not in git; `auto-lorebook export` produces a YAML dump for inspection or backup.

## Considered alternatives

- **SQLite as a derived cache, YAML stays truth** — rejected: doesn't solve fact identity or write atomicity, which were the primary drivers.
- **Split truth (entity metadata in YAML, facts in SQLite)** — rejected: two-truth coordination creates the same partial-write hazards we were trying to eliminate, and leaves entity↔entity relationship queries with no clean home.
- **YAML round-trip export as the hand-edit surface** — rejected: mapping arbitrary YAML hand-edits back into a normalized schema reliably is a hard problem; the result is a worse SQLite with a YAML veneer. CLI verbs (`auto-lorebook entity edit`, `fact edit`, etc.) are the correct edit surface.
