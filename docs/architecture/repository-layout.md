# Repository layout

Auto-Lorebook is multi-wiki: one tool installation manages a registry
of wikis, with one marked active at a time. Each wiki is
self-contained — its in-flight pending state lives inside the wiki
folder under a hidden `.wiki-state/` dir, alongside the human-approved
canon. The tool's own per-user state (registry, models, API key) lives
under `~/.auto-lorebook/`.

The split between hidden tool churn and visible human-approved canon
is preserved by the leading dot, not by putting them on different
volumes — see [ADR-0003](../adr/0003-wiki-state-lives-inside-the-wiki.md)
for the rationale shift from the previous "split state across
locations" model.

## Wiki repo

Any directory you point the tool at, registered with a nickname:

```
<wiki-repo>/
  .transcription-corrections.yaml   # global phonetic / mishearing fixes (legacy; lazy-backfills wiki.db)
  .wiki-context.yaml                # setting info, conventions, defaults (legacy; lazy-backfills wiki.db)
  .wiki-state/                      # per-wiki tool state (hidden)
    .gitignore                      # auto-managed: ignores pending/
    wiki.db                         # canonical store for sources, corrections, wiki context, entities, …
    last-context.yaml               # per-setting defaults: perspective, source_nature
    pending/
      <source_id>/
        plan.yaml                   # planner output
        proposals/
          <proposal_id>.yaml        # one per proposed fact
  sources/
    <source_id>/
      transcript.en.srt             # raw transcript, untouched
      info.yaml                     # url, title, duration, caption_type, context (legacy; lazy-backfills wiki.db)
      reading.md                    # corrected reading (after approval)
  characters/
    <slug>.yaml                     # canonical name, slug, aliases, facts
    <slug>.md                       # summary (regenerated view)
  locations/
  factions/
  events/
  items/
  concepts/
  index.md                          # auto-generated table of contents
```

Entity identity lives entirely in entity YAMLs. An entity exists iff
`<category>/<slug>.yaml` exists. No separate registry file — see
[entity model](entity-model.md).

Visible-vs-hidden line: hand-edited canon and source material stay at
the wiki root (browsable, diffable, hand-editable). Tool-managed
churn — pending LLM outputs, last-used defaults — lives under
`.wiki-state/`. Future per-wiki tool artifacts (entity-index cache,
migration markers) land there too instead of polluting the top level.

## Tool state directory

`~/.auto-lorebook/` holds per-user, cross-wiki state:

```
~/.auto-lorebook/
  config.yaml                       # registry, active wiki, models, preamble budget
  credentials                       # OpenRouter API key (mode 0600)
```

`config.yaml` shape:

```yaml
schema_version: 2
active_wiki: home-game
wikis:
  - nickname: home-game
    path: /home/user/wikis/home-game
  - nickname: scifi
    path: /home/user/wikis/scifi-setting
openrouter:
  api_key_env: OPENROUTER_API_KEY
models:
  primary: anthropic/claude-sonnet-4-5
  primary_context_window: 200000
preamble:
  budget_fraction: 0.8
```

The registry is the single source of truth for which wikis the tool
knows about. `active_wiki` holds a nickname; lookups resolve nickname
→ path. Per-invocation override: `auto-lorebook --wiki <nickname>
<subcommand> ...`. There is intentionally no CWD-walk-up discovery —
the registry is always consulted.

The API key is per-user, not per-wiki: putting credentials inside a
wiki that might be shared, pushed, or backed up to another machine is
a leak risk we don't want to invite.

### Active-wiki resolution

In order of precedence:

1. `--wiki <nickname>` (explicit per-invocation flag, never mutates
   state)
2. `active_wiki` from `config.yaml`

Failure modes are loud, not silent:

- **No `~/.auto-lorebook/config.yaml`** — first-run interactive setup
  prompts for `(nickname, path)` and API key, writes the registry's
  first entry, sets `active_wiki`.
- **`active_wiki` unset or pointing at unknown nickname** — hard error
  directing the user to `auto-lorebook wiki list` then
  `auto-lorebook wiki use <nickname>`. No auto-pick of the first
  entry: silent choice would mask real corruption.
- **`active_wiki` resolves to a path that no longer exists on disk**
  (deleted, unmounted, renamed) — hard error with remediation hints:
  restore the directory, switch active, or remove the entry. No
  auto-deregister: the volume might just be unmounted.

### Schema versioning

`config.yaml` jumps directly to `schema_version: 2`. Encountering a
v1 file (single `wiki_repo_path` field, no registry) is a hard error:
the file has no history worth preserving, so the loader refuses with
a message asking the user to delete it and re-run setup. This
deliberately diverges from the lazy-migration pattern in
[schema versioning](schema-versioning.md), which applies to *artifact*
YAMLs whose history matters.

## Lifecycle

- **Wiki registration.** `auto-lorebook wiki use <path>` registers
  the path (nickname defaults to basename, override with `--name`),
  sets it active, and bootstraps the wiki skeleton (entity dirs,
  `.wiki-context.yaml`, `.transcription-corrections.yaml`,
  `.wiki-state/`) if not already present. `auto-lorebook wiki use
  <nickname>` switches active to an existing entry.
- **Ingest start.** Transcript and `info.yaml` land under
  `<wiki>/sources/<source_id>/`. Reading pipeline state (segments,
  bullets, sidecar) is stored in `wiki.db` (`segments`,
  `segment_bullets`, and `ingests` tables), not as YAML files.
- **Reading approval.** The wiki-side `reading.md` is written by the
  reading-review engine when every segment is decided (`accepted` or
  `skipped`). The presence of the file is the gate — there is no
  top-level frontmatter flag.
- **Planning + extraction.** Produces
  `<wiki>/.wiki-state/pending/<source_id>/plan.yaml` and one YAML per
  proposed fact under `<wiki>/.wiki-state/pending/<source_id>/proposals/`.
- **Fact review.** Approvals append to (or create) entity YAMLs
  under `<category>/<slug>.yaml`. Rejected proposals are discarded.
  If every proposal for a new entity is rejected, no stub is ever
  written.
- **Ingest complete.** When all proposals are decided, the ingest's
  pending directory is discarded. The audit trail lives on in
  `created_by_ingest` and `approved_at` fields on the resulting
  facts.

## QA fixtures

Synthetic stage-input artifacts ship with the package under
`src/auto_lorebook/_qa_fixtures/<name>/` and are loaded via
`importlib.resources` by the `seed-ingest` command. See
[QA seeding](../contributing.md#qa-seeding).

## Why state lives inside the wiki

The previous layout split pending state into `~/.auto-lorebook/pending/`
specifically to keep the wiki repo "canon-only." That split was the
right call when there was exactly one wiki per machine; multi-wiki
inverts the trade-offs:

- **Portability.** A wiki dir is now self-contained — move it, share
  it, clone it across machines, and any in-flight ingest moves with
  it. The previous layout left pending work stranded on the original
  machine.
- **Isolation.** Two wikis with the same `source_id` no longer
  collide in a single shared `pending/` namespace. `reject-ingest`'s
  blast radius is automatically wiki-scoped.
- **Backup coherence.** Backing up a wiki backs up its in-flight
  reviews. Previously, "back up the wiki" silently excluded any work
  not yet through the fact-review gate.
- **Per-setting defaults.** `last-context.yaml` (perspective,
  source_nature) describes the *setting*, not "the last thing the
  user did." Storing it per-wiki means switching wikis no longer
  re-prompts with stale defaults from another setting.

The two properties the old split bought us are preserved by other
mechanisms:

- **Clean wiki `git log`** — the auto-written
  `<wiki>/.wiki-state/.gitignore` excludes `pending/`, so LLM-retry
  churn never reaches commits.
- **Browsing shows only canon** — `.wiki-state/` is hidden by the
  leading dot; `ls`, `tree`, and a casual file-browser pass over it.
