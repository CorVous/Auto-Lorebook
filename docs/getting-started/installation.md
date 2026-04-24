# Installation

Auto-Lorebook is a Python CLI managed with [uv](https://docs.astral.sh/uv/).

## Prerequisites

- Python 3.13.
- `uv` installed. On macOS: `brew install uv`.
- `yt-dlp` on `PATH` for YouTube ingestion.
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

On first invocation of `auto-lorebook ingest`, the tool detects a
missing `~/.auto-lorebook/config.yaml` and prompts for the values
needed to write one (wiki repo path, API-key env var name, and primary
model). It also seeds the wiki with the entity directories and the
two convention files (`.wiki-context.yaml`,
`.transcription-corrections.yaml`). Pass `--no-interactive` to suppress
the prompt and require a pre-existing config instead.

To write the file by hand, the minimal layout is:

```yaml
schema_version: 1
wiki_repo_path: /path/to/your/wiki
openrouter:
  api_key_env: OPENROUTER_API_KEY
models:
  primary: openrouter/anthropic/claude-sonnet-4-5
  extractor: openrouter/anthropic/claude-sonnet-4-5   # accepted but unused in Phase 1
  primary_context_window: 200000
preamble:
  budget_fraction: 0.8
```

Set the API key in your shell:

```bash
export OPENROUTER_API_KEY=sk-or-...
```

The tool expects two locations:

- **Wiki repo** — any directory you point the tool at, where sources,
  entity YAMLs, and rendered markdown live. See
  [repository layout](../architecture/repository-layout.md).
- **State directory** — `~/.auto-lorebook/`, holding `config.yaml` and
  pending (unapproved) ingest artifacts.

## Next

Run your first ingest: [first ingest walkthrough](first-ingest.md).
