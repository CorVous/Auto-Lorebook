# Schema versioning

Every YAML file the tool produces carries a top-level `schema_version`
as its first key, so that future changes to file shapes can be detected
and migrated rather than silently misread.

```yaml
schema_version: 1
# ...rest of the file
```

## Where it applies

All YAML artifacts the tool writes:

- `sources/<source_id>/info.yaml`
- `pending/<ingest_id>/reading/structure.yaml`
- `pending/<ingest_id>/plan.yaml`
- `pending/<ingest_id>/proposals/<proposal_id>.yaml`
- `<category>/<slug>.yaml` (entity YAMLs)
- `~/.auto-lorebook/config.yaml`

Hand-maintained files (`.wiki-context.yaml`,
`.transcription-corrections.yaml`) also carry `schema_version`. The
tool writes it when it first touches an empty or missing file and
preserves it on write-back. A missing `schema_version` on a
hand-maintained file is read as `schema_version: 1` with a warning
suggesting the user add it.

Markdown artifacts with YAML frontmatter (`reading.md`, entity `.md`
summaries) carry `schema_version` in their frontmatter.

## Versioning rules

- `schema_version` is a positive integer, starting at 1 for the MVP.
  No semver, no dates — one monotonically increasing number per file
  type.
- Each file type versions independently. Bumping `info.yaml`'s schema
  does not bump `plan.yaml`'s.
- The tool refuses to read a file whose `schema_version` is greater
  than any version it knows about, and names the remedy (upgrade the
  tool).
- When a file's schema changes, the tool includes a migration path
  from the immediately-previous version. Migrations run lazily on
  first read, write back the upgraded form, and log what was migrated.
- The tool does not support skipping versions. Upgrading from version
  N to N+2 runs N→N+1 then N+1→N+2.
- Missing `schema_version` on a tool-produced file is treated as a
  corruption signal — the tool always writes it — so the read fails
  loudly rather than defaulting.

## Interaction with staleness

The `schema_version` field is excluded from the input hashes used for
[staleness detection](staleness.md). Bumping a schema version should
not invalidate every downstream artifact in the wiki. Migrations are
orthogonal to staleness.
