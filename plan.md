# Auto-Lorebook Plan

## Overview

A CLI + web tool that uses OpenRouter LLMs to ingest fantasy world lore (raw text, notes, prose) and automatically generates/updates a structured markdown wiki.

## Architecture

```
User writes lore (raw text/notes)
        |
        v
   CLI command: `auto-lorebook ingest <file-or-stdin>`
        |
        v
   OpenRouter LLM extracts entities & relationships
   (characters, locations, factions, events, items, etc.)
        |
        v
   Wiki .md files created/updated in `wiki/` directory
        |
        v
   Web UI serves & renders the markdown wiki
```

## Core Components

### 1. OpenRouter LLM Client
- Async HTTP client (trio + httpx) calling OpenRouter's `/chat/completions` endpoint
- API key via env var `OPENROUTER_API_KEY`
- Configurable model selection (default: a cost-effective model)
- Structured output parsing (JSON responses from the LLM)

### 2. Lore Ingestion Pipeline
- **Input**: Raw text files, stdin, or a directory of text files
- **Processing**: Send lore text to LLM with a prompt that extracts:
  - Entity type (character, location, faction, event, item, concept)
  - Entity name
  - Summary/description
  - Relationships to other entities
  - Key attributes (varies by type)
- **Output**: Structured entity data ready for wiki generation

### 3. Wiki Generator
- Each entity gets its own `.md` file under `wiki/<category>/<entity-name>.md`
- Files use a consistent template per entity type
- Cross-links between related entities via markdown links (`[Character Name](../characters/character-name.md)`)
- **Update logic**: When re-ingesting, the LLM merges new info with existing file content (doesn't just overwrite)
- Generates an `wiki/index.md` as the main table of contents

### 4. Web Interface (Barebones)
- Single-file Python HTTP server (no framework beyond stdlib or something tiny)
- Reads `.md` files from the wiki directory
- Renders markdown to HTML with a minimal CSS stylesheet
- Sidebar or index page listing all wiki entries by category
- Navigation via the cross-links already in the markdown
- No JS framework, no build step — just server-rendered HTML

## Directory Structure

```
wiki/
  index.md
  characters/
    character-name.md
  locations/
    location-name.md
  factions/
    faction-name.md
  events/
    event-name.md
  items/
    item-name.md
  concepts/
    concept-name.md
```

## CLI Commands

```
auto-lorebook ingest <path>       # Ingest lore from a file or directory
auto-lorebook ingest -            # Ingest lore from stdin
auto-lorebook wiki list           # List all wiki entries
auto-lorebook wiki show <name>    # Print a wiki entry to stdout
auto-lorebook wiki rebuild        # Re-generate all wiki files from stored lore
auto-lorebook serve               # Start the web UI (default: localhost:8080)
```

## Implementation Phases

### Phase 1: OpenRouter Client
- [ ] Add `httpx` dependency
- [ ] Build async OpenRouter client with structured output support
- [ ] Add `ingest` CLI command that sends text and prints extracted entities

### Phase 2: Wiki File Generation
- [ ] Define markdown templates per entity type
- [ ] Write file manager that creates/updates wiki `.md` files
- [ ] Generate cross-links between entities
- [ ] Generate `index.md`

### Phase 3: Update/Merge Logic
- [ ] When an entity already exists, send existing + new content to LLM
- [ ] LLM produces merged/updated entry
- [ ] Write updated file

### Phase 4: Web Interface
- [ ] Minimal HTTP server serving wiki directory
- [ ] Markdown-to-HTML rendering (using `markdown` or `mistune` library)
- [ ] Simple CSS for readability
- [ ] Sidebar/index navigation

### Phase 5: Polish
- [ ] Error handling for API failures, rate limits
- [ ] Progress output during ingestion
- [ ] Configuration file support (model, wiki path, etc.)

## Key Dependencies to Add

- `httpx` — async HTTP client for OpenRouter API
- `mistune` — fast markdown parser for the web UI

## Open Questions

1. **Storage**: Should we store the raw ingested lore separately (for re-processing), or just keep the generated wiki files?
2. **Conflict resolution**: When updating, should the user be prompted to review changes, or fully automatic?
3. **Model choice**: Any preference for default OpenRouter model? (e.g., `mistralai/mistral-small`, `google/gemini-2.0-flash-001`, `meta-llama/llama-3-70b-instruct`)
