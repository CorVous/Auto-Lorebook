# CLI reference

All commands are invoked as `auto-lorebook <command>`. Each command
links to the page that explains its semantics.

## Run (primary entry point)

```bash
auto-lorebook run <url-or-source-id> \
  [--yes] \
  [--auto-approve]
```

Drive a source through the full pipeline from its current state to
completion. Skips stages already done; stops at each human gate. Accepts
a YouTube URL (ingest included if not done) or an existing source ID.

`--yes` forwards to the reading-review gate (auto-approves all reading
segments). `--auto-approve` forwards to the fact-review gate
(auto-approves all proposals). Both flags are required in a
non-interactive shell. See [first ingest](../getting-started/first-ingest.md).

## Ingest

```bash
auto-lorebook ingest <url-or-path> \
  [--source-url <url>] \
  [--source-id <id>] \
  [--session-date <YYYY-MM-DD>] \
  [--perspective <text>] \
  [--source-nature <kind>] \
  [--setting <name>] \
  [--no-interactive]
```

Fetch a source, gather context, and (by default) run the reading
pipeline. Interactive prompts by default; flags skip their
corresponding prompts. See [context pipeline](../pipeline/context.md).

```bash
auto-lorebook configure-context <source_id>
```

Re-run context prompts for an existing source — fills in skipped
fields or corrects mistakes.

## Reading

```bash
auto-lorebook generate-reading <source_id>
```

Run Stage 1a → 1b on a source whose reading wasn't generated at ingest
time. See [Stage 1](../pipeline/reading.md).

```bash
auto-lorebook regenerate-reading <source_id> --from={structure|summarize} \
  [--segments <id1,id2,...>]
```

Re-run from a given substage. `--segments` is valid only with
`--from=summarize`.

```bash
auto-lorebook approve-reading <source_id>
auto-lorebook readings list
auto-lorebook readings show <source_id>
```

```bash
auto-lorebook plan <source_id>
auto-lorebook extract <source_id>
```

`plan` runs Stage 2 (planner): routes claim bullets to entities and
writes `pending/<id>/plan.yaml`. Refuses to run unless the wiki-side
`reading.md` exists (run `approve-reading` first). See
[Stage 2](../pipeline/planner.md).

`extract` runs Stage 3 (extractor): locates verbatim transcript spans
and writes proposal YAMLs under `pending/<id>/proposals/`. Refuses to
run unless `pending/<id>/plan.yaml` exists (run `plan` first). See
[Stage 3](../pipeline/extractor.md).

## Plans

```bash
auto-lorebook plans list
auto-lorebook plans show <ingest_id>
auto-lorebook replan <ingest_id>
```

Plans are intermediate — no approval gate. `replan` re-runs the
planner and extractor on unreviewed proposals; approved proposals are
unaffected. See [Stage 2](../pipeline/planner.md).

## Review

```bash
auto-lorebook review <ingest_id>
```

Walk through proposals one at a time. Approve, edit, reject, or play
each. See [fact review](../pipeline/review.md).

## Corrections

```bash
auto-lorebook promote-correction "<from>" "<to>"
```

Promote a per-source name correction to the global
`.transcription-corrections.yaml`. See
[entity model](../architecture/entity-model.md#promotion).

## Entities

```bash
auto-lorebook entities list [--category <cat>] [--created-by <ingest_id>]
auto-lorebook entities show <slug-or-name>
auto-lorebook entities new --category <cat> --name <name> [--slug <slug>]
auto-lorebook entities rebuild-index
```

`list` prints a category/name/slug/alias-count table; filters compose.
`show` resolves the query against slugs first, then canonical names
(case-insensitive), then aliases. `new` writes a minimum-viable stub;
slug defaults to a slugified `--name`. `rebuild-index` is a placeholder
until an on-disk cache exists — today the in-memory index is rebuilt on
every command anyway. See
[hand-creating entities](../getting-started/entities.md).

```bash
auto-lorebook reject-ingest <ingest_id>
```

Remove all facts, alias additions, and empty entity stubs created by a
given ingest. See [audit trail](audit.md#rejecting-an-ingest).

## Wiki

Registry management commands for registered wiki directories.

```bash
auto-lorebook wiki list
auto-lorebook wiki use <nickname-or-path> [--name <nickname>]
auto-lorebook wiki add <nickname> <path>
auto-lorebook wiki remove <nickname>
auto-lorebook wiki rename <old> <new>
```

`wiki list` prints all registered wikis with `*` next to the active entry.

`wiki use` switches the active wiki. If the argument matches a known nickname,
it switches immediately. If it is a filesystem path to an existing directory,
the path is auto-registered (nickname defaults to the directory basename;
override with `--name`) and bootstrapped if needed, then set active.

`wiki add` registers a wiki without switching the active pointer. The path
must already exist on disk.

`wiki remove` deregisters a wiki. Refuses if the nickname is the active entry
— switch to another wiki first.

`wiki rename` renames an entry in place. If the renamed entry is active,
`active_wiki` is updated to the new nickname.

```bash
auto-lorebook wiki rebuild
```

Regenerates every entity page from scratch using the page step (prose +
linked-entity propagation) and reconciles the filesystem against the DB
— deletes any `.md` file in the entity-category subdirectories with no
matching entity. Use after corruption, a crashed page step, or a prompt
change. Staleness-skip is future work.

### `--wiki` override flag

```bash
auto-lorebook --wiki <nickname> <command> [args...]
```

Overrides the active wiki for a single invocation without mutating the
registry. Accepts nicknames only — passing a path-shaped string errors.
Example: `auto-lorebook --wiki home-game plan <source_id>`.

## Sources

```bash
auto-lorebook sources list          # flags sources with missing session_date
auto-lorebook sources show <source_id>
```

## QA

```bash
auto-lorebook seed-ingest --at={structure|summarize|approve|plan} \
  [--fixture <name>] \
  [--source-id <id>]
```

Mints a fresh `qa-<hex>` source_id and lays down a synthetic ingest up
through the chosen stage, so the next pipeline stage can be exercised
in isolation without re-running prior stages or hitting the LLM. The
default fixture is `tiny-aldara`; additional fixtures live under
`src/auto_lorebook/_qa_fixtures/`. Pair with `reject-ingest <id>` to
clean up. See [QA seeding](../contributing.md#qa-seeding).

## Web UI

```bash
auto-lorebook serve [--port 8080]
```

Available from Phase 6 onward. See [roadmap](../roadmap/index.md).

## Utility

```bash
auto-lorebook version
auto-lorebook --version
```

Display the installed package version. `version` is a subcommand;
`--version` is a top-level flag. Both print the same string.
