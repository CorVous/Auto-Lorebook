# Hand-creating entities

Entity files normally appear automatically: when you approve a fact in
the review loop (Phase 4), the tool creates the entity stub atomically
on first approval. Hand-creation is a bootstrapping aid for seeding a
fresh wiki with characters, locations, or other entities you already
know about.

The full schema is documented in
[entity model](../architecture/entity-model.md). For bootstrapping you
only need the four required keys.

## Minimum viable stub

Save as `<wiki>/<category>/<slug>.yaml`. Categories are the six
directories under your wiki repo: `characters`, `locations`,
`factions`, `events`, `items`, `concepts`.

```yaml
schema_version: 1
entity: Aldara
category: locations
slug: aldara
```

Optional fields the tool will read if present:

- `aliases` — list of `{name, added_by_ingest, added_at, source}` records.
- `superseded_by` — `"<category>/<slug>"` if this entity was merged into
  another. Hides it from the entity index.
- `created_at`, `created_by_ingest`, `updated_at` — provenance fields
  (left blank for hand-created entries).

## Scaffolding command

`entities new` writes a minimum stub for you. The slug is derived from
the name (lowercase, spaces to hyphens, non-alphanumeric stripped) but
can be overridden.

```bash
auto-lorebook entities new --category characters --name "King Theron"
# created /path/to/wiki/characters/king-theron.yaml

auto-lorebook entities new --category locations --name "Aldara" --slug aldara
```

The command refuses if the target file already exists; use
`entities show` to inspect the existing file.

## Verifying recognition

The entity index is rebuilt from the filesystem on every command run.
After hand-editing or scaffolding a stub, list it:

```bash
auto-lorebook entities list
auto-lorebook entities list --category characters
```

Inspect a single entity by slug, canonical name, or alias:

```bash
auto-lorebook entities show theron
auto-lorebook entities show "King Theron"
```

When you next run `ingest` or `configure-context`, the preamble shows
the entity index and your hand-created stubs appear in it.
