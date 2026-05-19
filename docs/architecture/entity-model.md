# Entity model

Entity identity lives in the `entities` and `aliases` tables of
`wiki.db`. See [ADR-0004](../adr/0004-sqlite-replaces-yaml-wiki-store.md) for the
rationale and the full transition timeline.

## `entities` table

| Column | Type | Notes |
|---|---|---|
| `category` | TEXT | One of `characters`, `locations`, `factions`, `events`, `items`, `concepts`. Part of PK. |
| `slug` | TEXT | URL-safe identifier, lowercase, hyphen-separated. Part of PK. |
| `canonical_name` | TEXT | Display name. Rename only touches this column — `slug` never changes. |
| `superseded_by_category` | TEXT | Nullable FK to `entities(category, slug)`. Set on merge. |
| `superseded_by_slug` | TEXT | Nullable FK to `entities(category, slug)`. Set on merge. |
| `created_at` | TEXT | ISO-8601 timestamp. |
| `created_by_ingest` | TEXT | Ingest ID that first created this entity. |
| `updated_at` | TEXT | ISO-8601 timestamp, updated on rename or supersession. |

**Rules:**

- `(category, slug)` is the primary key and the stable identity. It
  never changes after creation, even through renames.
- Renaming an entity updates only `canonical_name` and `updated_at`.
- Supersession records that this entity was merged into another.
  The file (and DB row) persist as a historical record. Most queries
  filter out superseded entities by default.

## `aliases` table

| Column | Type | Notes |
|---|---|---|
| `entity_category` | TEXT | FK to `entities(category)`. |
| `entity_slug` | TEXT | FK to `entities(slug)`. |
| `name` | TEXT | Original casing preserved. |
| `name_normalized` | TEXT | Result of `normalize_name(name)` — used for lookup. |
| `added_by_ingest` | TEXT | Ingest ID that added the alias. |
| `added_at` | TEXT | ISO-8601 timestamp. |
| `source` | TEXT | One of five values (see below). |

**Primary key:** `(entity_category, entity_slug, name_normalized)`.

This gives per-entity uniqueness: the same normalized name may appear
as an alias on multiple entities (cross-entity collision). That
ambiguity is surfaced at lookup time — `get_by_alias` returns `None`
when ambiguous and `category=None`; pass `category=` to disambiguate.

**`source` values:**

- `hand-edited` — user added directly to YAML or CLI.
- `alias-confirmation` — approved during a review alias sub-prompt.
- `stub-creation` — accompanied the entity's first approved fact.
- `promoted-from-merge` — carried over when a superseded entity was
  merged in. `added_by_ingest` is copied from the source entity's
  record to preserve provenance.
- `cli-edit` — added or edited via the `entities` CLI subcommand.

## Normalization

`normalize_name(name)` applies:

1. **NFKC** — decomposes compatibility forms (fullwidth, ligatures, etc.)
2. **casefold** — locale-agnostic lowercasing.
3. **strip** — leading/trailing whitespace removed.
4. **collapse whitespace** — runs of internal whitespace → single space.

Worked examples:

| Input | Output |
|---|---|
| `"  King Theron  "` | `"king theron"` |
| `"Ａｌｄａｒａ"` (fullwidth) | `"aldara"` |

The stored `name_normalized` is what all alias lookups compare against.
The original `name` field preserves the user's casing.

## Supersession resolution

`resolve(conn, category, slug)` follows the `superseded_by_*` chain
until it reaches an entity with no successor, returning that leaf. The
`max_hops` parameter (default 16) guards against cycles introduced by
direct SQL manipulation — `supersede()` prevents cycles at the Python
layer but the guard covers edge cases.

`list_entities` and `render_for_preamble` exclude superseded entities
by default (pass `include_superseded=True` to override).

## Visibility

One SQLite connection per command invocation, opened in autocommit mode
(`isolation_level=None`). Writes are immediately visible to subsequent
reads on the same connection within the session. The review loop
exploits this: after approving a new entity, `lookup_by_planner_name`
finds it on the next proposal without reopening the DB.

## Transitional dual-write (issues #71–#74)

During this window YAML files and the DB are both written on entity
creation and alias confirmation. The DB is the live source of truth for
preamble generation and entity lookup. YAMLs are still written because
the facts module and Stage 4 summary regen still read them. Issue #74
will cut the YAML write path and make the DB the sole store for entity
identity data. ADR-0004 is the long arc.

## Global transcription corrections

`.transcription-corrections.yaml` at the wiki root:

```yaml
schema_version: 1
corrections:
  - from: "Fair-on"
    to: "Theron"
    first_seen_in: yt-abc123     # source where this was first caught
    also_seen_in:
      - yt-def456
      - yt-ghi789
    promoted_at: 2026-01-18T10:04:21Z
    notes: "YouTube auto-captions consistently mishear this."
```

These are phonetic mishearings that apply across all sources. Distinct
from entity aliases (semantic, in-world) and from per-source
`name_corrections` in reading frontmatter (local to one source).

### Application

- **Reading stage** — corrections applied as literal substitutions to
  the transcript before the LLM sees it in 1a, and included in every
  substage's preamble as an explicit instruction. Per-source
  `name_corrections` stack on top; per-source wins on conflict.
- **Extractor stage** — applies the union of global corrections and
  approved reading's `name_corrections` when producing `text` from
  `raw_transcript_span`. Each substitution is logged in
  `corrections_applied`.
- **Planner stage** — works from the corrected reading, so transcript
  corrections don't apply directly. Entity index matching handles
  aliases separately.

### Promotion

Corrections that recur across readings can be promoted from per-source
frontmatter to the global file:

```bash
auto-lorebook promote-correction "<from>" "<to>"
```

Per-source entries remain in reading frontmatter after promotion as
historical record; application code uses the union, so no duplication
issue. When a correction is promoted, `first_seen_in` is set to the
earliest source containing it; subsequent promotions of the same pair
append to `also_seen_in`.
