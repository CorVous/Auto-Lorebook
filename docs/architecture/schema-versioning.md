# Schema versioning

The tool tracks schema versions in two separate places with different
mechanisms because they serve different purposes.

## Wiki SQLite database (`wiki.db`)

`<wiki>/.wiki-state/wiki.db` holds a single `schema_version` table with
one row:

```sql
SELECT version FROM schema_version;  -- e.g. 1
```

This is the authoritative version for the relational schema. It covers
all 17 tables that store entities, facts, ingests, proposals, and related
metadata.

### Versioning rules

- Version is a positive integer, starting at 1.
- Monotonically increasing; no skips; no semver.
- Each schema change appends one numbered migration function to `MIGRATIONS`
  in `auto_lorebook/db/migrations.py`. Never edit a prior migration.
- `CURRENT_SCHEMA_VERSION = len(MIGRATIONS)` updates automatically.

### Opening a database

`db.open(path)` migrates lazily:

1. Detect current version from `schema_version` table (0 if table absent).
2. If `db_version > CURRENT`: raise `SchemaVersionTooNewError` — message
   includes "upgrade the tool".
3. If `db_version == CURRENT`: return as-is (no-op).
4. Otherwise: run migrations `db_version+1 … CURRENT` inside a single
   `BEGIN IMMEDIATE … COMMIT` transaction; roll back on failure.

Fresh databases go from 0 → latest in one call. Already-current databases
open with zero overhead.

### Cross-reference

See [ADR-0004](../adr/0004-sqlite-replaces-yaml-wiki-store.md) for the rationale
behind choosing SQLite over YAML for mutable tool state.

## YAML artifacts

Every YAML file the tool reads or writes carries a `schema_version` key
as its first field. This is an independent integer per file type — bumping
`info.yaml`'s schema doesn't affect `plan.yaml`'s.

```yaml
schema_version: 1
# ...rest of the file
```

YAML `schema_version` applies to all tool-produced YAML and to
hand-maintained files (`.wiki-context.yaml`,
`.transcription-corrections.yaml`). Config (`~/.auto-lorebook/config.yaml`)
also uses a YAML `schema_version` because it isn't relational.

### YAML versioning rules

- Positive integer starting at 1.
- Each file type versions independently.
- The tool refuses to read a file whose `schema_version` exceeds any
  version it knows, naming the remedy (upgrade the tool).
- Migrations run lazily on first read, write back the upgraded form, and
  log what was migrated.
- Missing `schema_version` on a tool-produced file is a corruption signal;
  the read fails loudly rather than defaulting.

## Interaction with staleness

`schema_version` fields are excluded from the input hashes used for
[staleness detection](staleness.md). Schema migrations are orthogonal to
staleness; migrating a file should not invalidate downstream artifacts.
