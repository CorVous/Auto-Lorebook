# PRD: SQLite replaces YAML as the wiki store

**Triage label:** `needs-triage`

## Problem Statement

Today, an approved **Fact** that routes to N entities lives as N copies — one inside each target entity YAML — sharing a `claim_group_id`. This is the only way to express "a fact touches multiple entities" in the current file-per-entity model, and it has four costs the wiki owner feels directly:

1. **Fact identity is implicit.** There's no single thing called "fact aldara-f001-and-friends"; there are N rows with the same `claim_group_id`. Editing one copy's `text` or `status` does not update the others.
2. **Relational queries are O(scan everything).** Questions like "which facts mention both Theron and Aldara?" or "which entities does this fact touch?" require loading every entity YAML and grouping by `claim_group_id`.
3. **Multi-target approval is not atomic.** Approving a bundle that touches N entities writes N YAML files; a Ctrl-C in the middle leaves the wiki half-canon.
4. **Typed fact-to-fact links don't exist.** The current model can mark a fact `disproven` with free-text `status_reason`, but the link is not machine-traversable. You can't ask "what supersedes fact X?" or "what contradicts what?" without grep.

## Solution

Replace all wiki YAML storage with a single SQLite database per wiki at `<wiki>/.wiki-state/wiki.db`, git-ignored. **Facts** become first-class rows; a **Fact** with N targets is one row in `facts` plus N rows in `fact_targets`. Cross-fact relationships become typed edges in `fact_refs` (`supersedes | contradicts | corroborates | qualifies`). Entity↔entity relationships are not a first-class table — they fall out as queries over shared `fact_targets`.

**Bundle** approval becomes a single transaction: insert one fact, insert N fact_targets, append a status_history row, optionally apply alias confirmations, and delete the corresponding proposal rows.

Hand-editing canon moves from "open YAML in vim" to CLI verbs (`auto-lorebook entity rename`, `fact edit`, `fact-ref add`, etc.). The Stage-4-regenerated `<category>/<slug>.md` summary files remain at the wiki root as the browsable canonical surface and are still git-trackable; only the binary DB is excluded from git. An `auto-lorebook export <dir>` command produces a YAML dump for inspection or backup.

`~/.auto-lorebook/config.yaml` stays as YAML — it's per-user, cross-wiki, and must not depend on any wiki's DB existing.

The "filesystem is the registry, YAML is truth" principles are retired; the visible/hidden line from ADR-0003 is preserved in spirit (root stays human-browsable, tool state lives under `.wiki-state/`).

## User Stories

1. As a wiki-owner, I want a **Fact** with multiple target entities to exist as a single canonical row, so that editing its text or status updates the canon in one place rather than drifting across N entity files.
2. As a wiki-owner, I want to query "all facts mentioning both X and Y," so that I can surface cross-cutting connections without scanning every entity file.
3. As a wiki-owner, I want to query "all facts that touch entity X," so that I can see X's full evidence trail without grep.
4. As a wiki-owner, I want to assert that fact B supersedes fact A, so that the system automatically marks A as `disproven` and records the edge, without me having to update two places.
5. As a wiki-owner, I want removing a `supersedes` edge to restore the prior status of the previously-disproven fact, so that mistaken edges can be cleanly undone.
6. As a wiki-owner, I want to express that fact A contradicts fact B without either being disproven, so that the wiki records open conflicts in the source material.
7. As a wiki-owner, I want fact B to corroborate fact A from a different source, so that I can see which facts are independently confirmed.
8. As a wiki-owner, I want a fact to qualify another fact, so that "this is true only in context X" is recorded without contradicting the parent.
9. As a wiki-owner approving a multi-target **Bundle**, I want the write to be atomic, so that no Ctrl-C can leave the wiki with half a fact committed.
10. As a wiki-owner, I want approving the same **Proposal** twice (Ctrl-C resume) to be a silent no-op, so that resuming a review is safe regardless of where it was interrupted.
11. As a wiki-owner, I want my wiki's DB to live under `.wiki-state/`, so that the wiki root stays human-browsable — only `.md` summaries, transcripts, and source metadata are visible at the top level.
12. As a wiki-owner, I want the `<category>/<slug>.md` summary files to keep regenerating from canon, so that the human-facing browsable wiki is unchanged.
13. As a wiki-owner, I want CLI verbs to hand-edit canon (entity rename, alias add/remove, fact text edit, fact status change, fact-ref add/remove), so that I can edit without touching files.
14. As a wiki-owner, I want `auto-lorebook export <dir>` to produce a directory of YAML files mirroring the DB contents, so that I can inspect or back up my wiki in a diffable form.
15. As a wiki-owner, I want the DB file to be excluded from git automatically, so that I never accidentally commit a binary blob.
16. As a wiki-owner, I want the `.md` summary files to remain trackable in git, so that I can keep a narrative history of how the wiki evolved.
17. As a wiki-owner registering a new wiki, I want the DB to be initialized automatically by `wiki use`, so that there's no separate setup step.
18. As a wiki-owner upgrading the tool, I want pending DB schema migrations to apply automatically on the next DB open, so that I don't need to run a migration command manually.
19. As a wiki-owner, I want a hard, named error when the DB's `schema_version` is newer than what the tool understands, so that I'm prompted to upgrade rather than silently misreading data.
20. As a wiki-owner, I want my `~/.auto-lorebook/config.yaml` (registry, models, API key env var, preamble budget) to stay as YAML, so that first-run setup doesn't depend on any wiki's DB existing and the API key never enters a shareable wiki.
21. As a wiki-owner, I want the planner stage to write into DB tables (not YAML files), so that the review approval can join across plan, proposals, and facts in one transaction.
22. As a wiki-owner during review, I want entities created earlier in the same session to be visible to later proposals, so that "Theron" added in route 1 doesn't get re-suggested as a new entity in route 3.
23. As a wiki-owner, I want every existing CLI subcommand and slash command to keep working with the same flags and behavior, so that my muscle memory doesn't break.
24. As a developer extending the schema, I want each migration to be a discrete, numbered step applied on DB open, so that the upgrade path is explicit and predictable.
25. As a developer writing tests, I want the database layer to expose a connection/transaction interface that accepts a path (or `:memory:`), so that tests can run against an isolated DB per test without filesystem coupling.
26. As a developer touching the **Facts** module, I want `supersedes` edge creation to atomically flip the target fact's status to `disproven` and append to `status_history`, so that I cannot introduce data inconsistency by forgetting the second update.
27. As a developer touching the **Approval** module, I want re-approving a proposal whose `proposed_id` is already in `facts` to be a silent skip, so that Ctrl-C-then-resume during review remains idempotent.
28. As a developer reading the codebase, I want `claim_group_id` to disappear from canon **Facts** entirely (surviving only in **Proposals** during ingest), so that the M:N **Fact**↔entity model isn't confused by a legacy single-table workaround.
29. As a developer, I want entity↔entity relationships to be derivable from shared `fact_targets` rather than a dedicated table, so that the schema stays small and a single source of truth.
30. As a developer, I want the in-memory entity index (used to render the planner preamble) to come from a simple SQL query instead of a YAML scan, so that startup is fast and "entities created earlier in this session" are visible without re-loading files.

## Implementation Decisions

### Storage doctrine
- One SQLite database per wiki, lives at `<wiki>/.wiki-state/wiki.db`, git-ignored via the auto-managed `.wiki-state/.gitignore`.
- DB is "truth"; `<category>/<slug>.md` summary files are the browsable view, regenerated by Stage 4.
- Hand-editing happens via CLI verbs; opening the DB directly with a SQLite client is not the supported edit path.
- `~/.auto-lorebook/config.yaml` stays YAML.

### Canon schema (high-level)
- **`entities`** — one row per entity. Holds canonical name, slug, category, supersession link, and timestamps.
- **`aliases`** — one row per alias, FK to entity. Stores original-cased name + normalized-cased name for dedup, plus `added_by_ingest`, `added_at`, `source`. New `source` value `cli-edit` joins the existing enum.
- **`facts`** — one row per **Fact**. No `claim_group_id`. Holds text, raw_transcript_span, locator, speaker, status, status_reason, section, source_id (FK), edit metadata, and the JSON blob `corrections_applied` (audit-only, not queried relationally).
- **`fact_targets`** — M:N join `(fact_id, entity_id)`. Replaces the N-YAML-copies pattern.
- **`fact_refs`** — typed directed edges `(from_fact_id, to_fact_id, ref_type, created_at, created_by_ingest, note)` with `ref_type ∈ {supersedes, contradicts, corroborates, qualifies}`. `supersedes` edges trigger the auto-`disproven` invariant.
- **`fact_status_history`** — append-only log `(fact_id, status, at, by, reason)`. Includes the `system-ref-creation` and `system-ref-deletion` values for supersedes auto-flips.
- **`sources`** — replaces `sources/<id>/info.yaml`. Per-source metadata.
- **`transcription_corrections`** + **`correction_also_seen_in`** — replaces `.transcription-corrections.yaml`.
- **`wiki_context`** — single-row table replacing `.wiki-context.yaml`.

### Pending pipeline schema (high-level)
- **`ingests`** — one row per ingest, tracks current stage.
- **`segments`** + **`segment_bullets`** — replaces structure / bullets / segment markdown frontmatter.
- **`reading_sidecar`** fields land on `ingests` (default_speaker, name_corrections JSON, session_date) — small enough to fold.
- **`plan_routes`** — replaces `plan.yaml`. Holds `claim_group_id` as a field (still meaningful at planning time).
- **`proposals`** — replaces `proposals/*.yaml`. Holds `claim_group_id` and `target_entity` so approval joins straight to canon.

### Invariants the schema enforces
- A `supersedes` edge auto-sets the target fact's `status` to `disproven` and appends to `fact_status_history` with `by: system-ref-creation`. Removing the edge restores the prior status from `fact_status_history` and appends a `by: system-ref-deletion` entry.
- A fact's `status = disproven` iff at least one `supersedes` edge points at it.
- `contradicts`, `corroborates`, `qualifies` edges do not affect status.
- Approving a bundle is a single transaction: insert fact + N fact_targets + status_history row + alias rows (if any), delete the matching proposal rows.
- Re-approving a proposal whose `id` already exists in `facts` is a silent skip (idempotent resume).
- `claim_group_id` exists only in `plan_routes` and `proposals`; never in `facts`.

### Schema versioning
- Numbered migration steps applied lazily on DB open. A `schema_version` table tracks the current version with one row.
- The tool refuses to open a DB whose `schema_version` exceeds what it knows about, and names the remedy.
- Mirrors the existing per-YAML `schema_version` pattern, just applied DB-wide.

### Module decomposition
- **New deep modules:** `db` (connection + migrations + transactions), `facts` (facts/fact_targets/fact_refs/status_history + invariants), `entities` (entities + aliases + lookups + supersession), `approval` (bundle-approval transaction), `exporter` (DB → YAML dump).
- **Replaced modules** (DB-backed but same role): `proposals`, `plans`, `sources`, `wiki_context`, `corrections`, `reading_state`, `wiki_bootstrap`.
- **`entity_index` is folded into `entities`** — the in-memory index becomes a query.
- **Unchanged:** `srt`, `ytdlp`, `transcript`, `openrouter`, `llm_helpers`, `config`, `wiki_registry`, the LLM stages (1a/1b/2/3/4), `review`, `reading_review`, `reading_pipeline` — same logic, swap I/O calls.

### CLI surface
- New verbs for hand-editing: `entity rename`, `entity alias add/remove`, `entity supersede`, `fact edit`, `fact status`, `fact-ref add/remove`.
- New verb for inspection: `export <dest>`.
- Existing verbs keep their flags and behavior.

### Git story
- `wiki.db` and `pending/` excluded by the auto-managed `.wiki-state/.gitignore`.
- `<category>/<slug>.md` summary files remain trackable.
- No two-format coexistence; no migration of pre-existing YAML wikis (user has none).

## Testing Decisions

### What makes a good test here
A good test asserts external behavior — what callers observe — not internal structure. For the deep modules:

- **`facts` tests** assert "after creating a supersedes edge from B to A, `facts.get(A).status == 'disproven'` and the status_history has a `system-ref-creation` entry"; they do **not** assert which SQL statements ran or in what order.
- **`approval` tests** assert "after `approve_bundle(...)`, the proposal row is gone and a fact exists with N targets"; they assert idempotency by calling twice and expecting no change on the second call; they assert partial-failure rollback by simulating a DB error mid-transaction and expecting no row visible afterward.
- **`entities` tests** assert alias dedup, normalize-name lookups, slug normalization, and supersession resolution behave correctly across edge cases (whitespace, case, unicode).
- **`db_migrations` tests** assert that a fresh DB opens at the latest schema_version; that an artificially-stamped-old DB upgrades to current; that an artificially-stamped-future DB refuses to open with a named error.

### Module coverage
- **Full unit-test coverage:** `facts`, `approval`, `entities`, `db_migrations`. These are the deep modules where correctness invariants live.
- **Integration-test coverage only:** `proposals`, `plans`, `sources`, `wiki_context`, `corrections`, `reading_state`, `exporter`. These are mostly CRUD and are exercised end-to-end by the stage tests.
- **Live integration test** (`tests/test_live_integration.py`, opt-in via `--run-live`) updated to use the DB-backed wiki on the real OpenRouter path.

### Prior art
Existing tests under `tests/` already use a temporary wiki directory for end-to-end runs. New tests follow the same pattern, plus a per-test `:memory:` SQLite for the unit suites where filesystem coupling adds no value. `tests/test_docs.py` continues to validate doc-drift; documented env vars and config keys remain owned by `config.yaml`, so its surface doesn't change here.

## Out of Scope

- **Migrating any existing YAML wiki.** User has none. If someone needs this later, a separate `auto-lorebook migrate` PR can be opened.
- **Two-format coexistence.** No code paths for "this wiki uses YAML; this one uses SQLite." The DB is the only format going forward.
- **Entity↔entity typed relationships as a dedicated table.** Intentionally derived from `fact_targets`; not a schema element.
- **A GUI for hand-editing canon.** CLI verbs are the v1 edit surface.
- **User-facing SQL passthrough** (`auto-lorebook query 'SELECT ...'`). All queries are wrapped by tool commands.
- **Moving `~/.auto-lorebook/config.yaml` into a DB.** It stays YAML — per-user, cross-wiki, must not depend on any wiki's DB.
- **Multi-process / multi-writer concurrency beyond SQLite defaults.** WAL mode is enabled; the tool assumes one writing process per wiki at a time, as today.
- **Backwards-compatibility shims.** No `--use-yaml` flag, no "legacy mode."

## Further Notes

- **ADR-0004** (`docs/adr/0004-sqlite-replaces-yaml-wiki-store.md`) records the architectural decision and the rejected alternatives.
- **CONTEXT.md** has been updated with the new terms **Fact target** and **Fact ref**, and revised definitions for **Proposal**, **Fact**, the bundle-approval relationship, and the plan/proposal and idempotent-re-approval invariants.
- **Implementation can land in multiple PRs.** The deep modules (`db`, `facts`, `entities`, `approval`) can be built and tested standalone before the integration points (stages 1–4, review, bootstrap) are switched over. A staged landing might look like: (1) `db` + schema + migrations + tests, (2) `entities` + `facts` + tests, (3) `approval` + tests, (4) replaced YAML modules, (5) stage and review integration, (6) CLI hand-edit verbs and exporter.
- **The "filesystem is the registry" doctrine is retired** in this change. ADR-0004 should be cross-referenced from ADR-0003 (`docs/adr/0003-wiki-state-lives-inside-the-wiki.md`) in a follow-up so future readers see that the visible/hidden line survived but the YAML-as-truth principle did not.
- **Docs-drift impact.** `docs/architecture/entity-model.md`, `docs/architecture/repository-layout.md`, and `docs/architecture/schema-versioning.md` all describe the YAML model and will need substantial rewrites in the same PR(s) that land the schema. `tests/test_docs.py` will flag any drift.
