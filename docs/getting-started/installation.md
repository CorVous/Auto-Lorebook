# Installation

Auto-Lorebook is a Python CLI managed with [uv](https://docs.astral.sh/uv/).

## Prerequisites

- Python 3.13.
- `uv` installed. On macOS: `brew install uv`.
- An OpenRouter API key for LLM access.

## Install

Clone the repo and sync dependencies:

```bash
git clone https://github.com/corvous/Auto-Lorebook.git
cd Auto-Lorebook
uv sync --dev
```

Verify the CLI is reachable:

```bash
uv run auto-lorebook --help
```

## Configure

Auto-Lorebook is multi-wiki: the tool keeps a registry of wiki paths
under `~/.auto-lorebook/config.yaml` and one is marked active at a
time. Per-wiki state (in-flight ingests, per-setting defaults) lives
inside each wiki under `<wiki>/.wiki-state/`. See
[repository layout](../architecture/repository-layout.md) for the
full split.

On first invocation of `auto-lorebook ingest`, the tool detects a
missing `~/.auto-lorebook/config.yaml` and prompts for:

- A nickname for your first wiki.
- The wiki repo path (registered with that nickname and marked active).
- Your OpenRouter API key (input is hidden; stored at
  `~/.auto-lorebook/credentials` with mode 0600).
- Primary model slug (default: Claude Sonnet 4.5).

It also seeds the wiki with the entity directories, the two
convention files (`.wiki-context.yaml`,
`.transcription-corrections.yaml`), and the hidden `.wiki-state/`
directory (with an auto-managed `.gitignore` for `pending/`). Pass
`--no-interactive` to suppress the prompt and require a pre-existing
config instead.

If you'd rather use an environment variable, leave the API-key prompt
blank and `export OPENROUTER_API_KEY=sk-or-...` in your shell. The env
var takes precedence over the credentials file when both are present.

To write the config by hand, the minimal layout is:

```yaml
schema_version: 2
active_wiki: home-game
wikis:
  - nickname: home-game
    path: /path/to/your/wiki
openrouter:
  api_key_env: OPENROUTER_API_KEY
models:
  primary: anthropic/claude-sonnet-4-5
  extractor: anthropic/claude-sonnet-4-5   # accepted but unused in Phase 1
  primary_context_window: 200000
preamble:
  budget_fraction: 0.8
```

## Switching and adding wikis

```bash
auto-lorebook wiki list                 # show registered wikis; * marks active
auto-lorebook wiki use <nickname>       # switch active to an existing entry
auto-lorebook wiki use <path>           # register new wiki + switch active
auto-lorebook wiki use <path> --name <nickname>   # register with explicit nickname
auto-lorebook wiki add <nickname> <path>          # register without switching
auto-lorebook wiki remove <nickname>    # deregister; refuses if active
auto-lorebook wiki rename <old> <new>
```

Per-invocation override on any subcommand:

```bash
auto-lorebook --wiki <nickname> plan <source_id>
```

`--wiki` accepts nicknames only — never raw paths — and never mutates
the registry.

## Where things live

- **Wiki repo** — any directory you point the tool at. Holds sources,
  entity YAMLs, rendered markdown, and a hidden `.wiki-state/` for
  this wiki's in-flight tool state. See
  [repository layout](../architecture/repository-layout.md).
- **Tool state directory** — `~/.auto-lorebook/`, holding
  `config.yaml` (the wiki registry, active pointer, models, preamble
  budget) and the optional `credentials` file (mode 0600). Per-user,
  shared across all registered wikis.

## Next

Run your first ingest: [first ingest walkthrough](first-ingest.md).
