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
   ┌─────────────────────────────────────────────────────┐
   │  STAGE 1: Pre-processor (cheap, large-context model)│
   │  e.g. Gemini 2.0 Flash                             │
   │                                                     │
   │  Input: full transcript + existing wiki pages       │
   │  Output: relevant wiki excerpts matched to          │
   │          transcript sections                        │
   └──────────────────────┬──────────────────────────────┘
                          |
                          v
   ┌─────────────────────────────────────────────────────┐
   │  STAGE 2: Planner (mid-tier model)                  │
   │  e.g. Claude 3.5 Haiku                              │
   │                                                     │
   │  Input: pre-processed transcript + relevant wiki    │
   │  Output: plan of what entities to create/update,    │
   │          what info goes where, what's new vs. known │
   └──────────────────────┬──────────────────────────────┘
                          |
                          v
   ┌─────────────────────────────────────────────────────┐
   │  STAGE 3: Writer (smart model)                      │
   │  e.g. Claude 3.5 Sonnet / GPT-4o                   │
   │                                                     │
   │  Input: unedited transcript + plan + relevant wiki  │
   │  Output: final wiki markdown with citations         │
   └──────────────────────┬──────────────────────────────┘
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
- Three configurable model slots, each with a different role and default:
  - **Pre-processor**: Cheap, fast, large-context for bulk text matching (default: `anthropic/claude-3.5-haiku`)
  - **Planner**: Strong reasoning to decide what goes where (default: `anthropic/claude-opus-4`)
  - **Writer**: Excellent prose output with citations (default: `anthropic/claude-sonnet-4`)
- Structured output parsing (JSON responses from the LLM)

### 2. Source Parsers
- **SRT parser**: Parses `.srt` subtitle files, extracts dialogue/narration with timestamps. Groups consecutive subtitle blocks into logical chunks for LLM context.
- **Plain text parser**: Handles `.txt` and `.md` files as raw lore input.
- **Source metadata**: Each input carries a source descriptor:
  - For YouTube SRTs: `--source-url https://youtube.com/watch?v=VIDEO_ID` — timestamps become clickable `&t=` links
  - For web pages: `--source-url https://example.com/lore-page`
  - For local notes: source is recorded as the filename

### 3. Lore Ingestion Pipeline (Three-Stage)

**Stage 1 — Pre-processor** (cheap, large-context):
- **Input**: Full transcript/text + all existing wiki pages
- **Job**: Identify which parts of the transcript are relevant to which existing wiki entities, and flag sections that mention potentially new entities
- **Output**: A mapping of transcript sections → relevant existing wiki excerpts, plus a list of unrecognized entity mentions

**Stage 2 — Planner** (mid-tier):
- **Input**: The pre-processor's mapping + transcript sections
- **Job**: Decide what actions to take — which entities to create, which to update, what new information exists vs. what's already known
- **Output**: An action plan — a structured list of `{ entity, action: create|update, category, info_to_add, source_refs }`

**Stage 3 — Writer** (smart):
- **Input**: The unedited transcript, the plan, and the current wiki pages being modified
- **Job**: Produce final wiki markdown with proper prose, cross-links, and inline citations
- **Output**: Complete `.md` file content for each entity, ready to write to disk

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

### Phase 2: OpenRouter Client & Three-Stage Pipeline
- [ ] Add `httpx` dependency
- [ ] Build async OpenRouter client supporting multiple model slots
- [ ] Design Stage 1 prompt: pre-processor matches transcript to existing wiki
- [ ] Design Stage 2 prompt: planner decides create/update actions
- [ ] Design Stage 3 prompt: writer produces final wiki markdown with citations
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
3. **Model pipeline**: Three-stage pipeline with configurable models per stage. Defaults (all via OpenRouter):
   - **Pre-processor**: `anthropic/claude-3.5-haiku` — cheap, fast, large-context for bulk matching
   - **Planner**: `anthropic/claude-opus-4` — strong reasoning to decide what goes where
   - **Writer**: `anthropic/claude-sonnet-4` — excellent prose output with citations
