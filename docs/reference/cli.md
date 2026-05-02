# CLI reference

All commands are invoked as `auto-lorebook <command>`. Each command
links to the page that explains its semantics.

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

```bash
auto-lorebook wiki list [--category <cat>]
auto-lorebook wiki show <entity>
auto-lorebook wiki rebuild
```

`wiki rebuild` regenerates all summaries from YAML. Skips files whose
recorded inputs haven't changed; add `--force` to regenerate
unconditionally. See
[staleness](../architecture/staleness.md#integration-with-commands).

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
