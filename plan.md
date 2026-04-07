# Auto-Lorebook Plan

## Overview

A CLI + web tool that uses OpenRouter LLMs to ingest fantasy world lore (raw text, notes, SRT subtitle files, prose) and automatically generates/updates a structured markdown wiki. Every claim in the wiki is backed by a citation to either a source URL or a timestamped YouTube video link.

## Architecture

```
User provides lore (raw text, SRT files, URLs)
        |
        v
   CLI command: `auto-lorebook ingest <file> --source-url <url>`
        |
        v
   SRT parser extracts text + timestamps
   (or plain text parser for .txt/.md input)
        |
        v
   OpenRouter LLM extracts entities & relationships
   with source references (timestamps, URLs)
        |
        v
   Wiki .md files created/updated in `wiki/` directory
   (every fact includes a citation)
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

### 2. Source Parsers
- **SRT parser**: Parses `.srt` subtitle files, extracts dialogue/narration with timestamps. Groups consecutive subtitle blocks into logical chunks for LLM context.
- **Plain text parser**: Handles `.txt` and `.md` files as raw lore input.
- **Source metadata**: Each input carries a source descriptor:
  - For YouTube SRTs: `--source-url https://youtube.com/watch?v=VIDEO_ID` — timestamps become clickable `&t=` links
  - For web pages: `--source-url https://example.com/lore-page`
  - For local notes: source is recorded as the filename

### 3. Lore Ingestion Pipeline
- **Input**: Parsed text chunks with source + timestamp metadata
- **Processing**: Send lore text to LLM with a prompt that extracts:
  - Entity type (character, location, faction, event, item, concept)
  - Entity name
  - Summary/description
  - Relationships to other entities
  - Key attributes (varies by type)
  - **Source citations** — for each fact, the LLM must output which source and timestamp/section it came from
- **Output**: Structured entity data with citations, ready for wiki generation

### 4. Wiki Generator
- Each entity gets its own `.md` file under `wiki/<category>/<entity-name>.md`
- Files use a consistent template per entity type
- Cross-links between related entities via markdown links (`[Character Name](../characters/character-name.md)`)
- **Citations rendered inline** — each fact gets a superscript reference linking to either:
  - A timestamped YouTube URL: `[1](https://youtube.com/watch?v=VIDEO_ID&t=123)` (clickable, jumps to the moment)
  - A web page URL: `[2](https://example.com/lore-page)`
- A **References** section at the bottom of each wiki page lists all sources
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
sources/                          # Raw ingested files preserved for re-processing
  2024-01-15_worldbuilding-vid.srt
  2024-01-20_campaign-notes.txt
  2024-02-01_magic-system.md
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
auto-lorebook ingest <path> --source-url <url>   # Ingest lore with a source reference
auto-lorebook ingest <path>                      # Ingest lore (source = filename)
auto-lorebook ingest -                           # Ingest lore from stdin
auto-lorebook wiki list                          # List all wiki entries
auto-lorebook wiki show <name>                   # Print a wiki entry to stdout
auto-lorebook wiki rebuild                       # Re-generate all wiki files from stored lore
auto-lorebook serve                              # Start the web UI (default: localhost:8080)
```

### Example: YouTube SRT Workflow

```bash
# Download subtitles for a worldbuilding video
yt-dlp --write-subs --sub-lang en --skip-download -o subs "https://youtube.com/watch?v=abc123"

# Ingest the SRT with the video URL as source
auto-lorebook ingest subs.en.srt --source-url "https://youtube.com/watch?v=abc123"

# Wiki entries now cite specific timestamps:
#   The Kingdom of Aldara was founded in the Second Age. [1]
#   ...
#   ## References
#   1. [Worldbuilding Video @ 4:32](https://youtube.com/watch?v=abc123&t=272)
```

## Implementation Phases

### Phase 1: SRT Parser & Source Metadata
- [ ] Build SRT parser (timestamps + text extraction)
- [ ] Define source metadata model (URL, type, timestamp mapping)
- [ ] Plain text parser with source tracking

### Phase 2: OpenRouter Client
- [ ] Add `httpx` dependency
- [ ] Build async OpenRouter client with structured output support
- [ ] Design LLM prompt that extracts entities with citations back to source timestamps/sections
- [ ] Add `ingest` CLI command with `--source-url` flag

### Phase 3: Wiki File Generation
- [ ] Define markdown templates per entity type (with References section)
- [ ] Write file manager that creates/updates wiki `.md` files
- [ ] Generate cross-links between entities
- [ ] Render inline citation superscripts and References footer
- [ ] Generate `index.md`

### Phase 4: Update/Merge Logic
- [ ] When an entity already exists, send existing + new content to LLM
- [ ] LLM produces merged/updated entry, preserving existing citations and adding new ones
- [ ] Show diff to user and prompt for approval before writing conflicting changes
- [ ] Write updated file only after user confirms

### Phase 5: Web Interface
- [ ] Minimal HTTP server serving wiki directory
- [ ] Markdown-to-HTML rendering (using `markdown` or `mistune` library)
- [ ] Simple CSS for readability
- [ ] Sidebar/index navigation
- [ ] Citation links render as clickable (YouTube timestamps open at the right moment)

### Phase 6: Polish
- [ ] Error handling for API failures, rate limits
- [ ] Progress output during ingestion
- [ ] Configuration file support (model, wiki path, etc.)

## Key Dependencies to Add

- `httpx` — async HTTP client for OpenRouter API
- `mistune` — fast markdown parser for the web UI

## Decisions

1. **Storage**: Raw ingested files are stored in a `sources/` directory alongside the wiki, preserving the original `.txt`, `.md`, or `.srt` files for re-processing.
2. **Conflict resolution**: When an entity update conflicts with existing content, the user is prompted to review and approve/reject changes before they're written.

## Open Questions

1. **Model choice**: Any preference for default OpenRouter model? (e.g., `mistralai/mistral-small`, `google/gemini-2.0-flash-001`, `meta-llama/llama-3-70b-instruct`)
