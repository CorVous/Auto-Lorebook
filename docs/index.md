# Auto-Lorebook

Auto-Lorebook is a CLI tool that ingests fantasy worldbuilding content —
primarily YouTube transcripts of actual-play sessions and lore videos —
and produces a citation-backed markdown wiki. Every claim in the wiki
links to a specific, verifiable moment in a source, reviewed and
approved by a human.

## What it does

The tool runs a staged pipeline that separates mechanical work (parsing,
locating, routing) from human judgment (what was said, what status a
claim has, whether an entity exists). Nothing enters the wiki without
explicit human approval, and every name correction or entity alias
accumulates so the tool gets better as the wiki grows.

## Pipeline at a glance

```
YouTube URL / SRT / text file
        │
        ▼
  Fetch & store source
        │
        ▼
  [Human context step]   fill in info.yaml
        │
        ▼
  Assemble prompt preamble
        │
        ▼
  Stage 1a: Structure (LLM)    segment + attribute
        │
        ▼
  Mechanical gap check         warning only
        │
        ▼
  Stage 1b: Summarize (LLM)    per-segment claim bullets
        │
        ▼
  [Human review gate]   approve reading
        │
        ▼
  Stage 2: Planner (LLM)       route claims → entities
        │                      (new entities exist only on the plan)
        ▼
  Stage 3: Extractor (LLM)     locate verbatim spans
        │                      (no gate between planner and extractor)
        ▼
  [Human review gate]   per-fact approve / edit / reject
        │              (first approval creates the entity stub;
        │               `replan` is the escape hatch)
        ▼
  Stage 4: Summarizer (LLM)    regenerate entity prose
```

Two review gates, not three: reading approval and fact approval. The
planner runs between them without a gate; its failure modes surface
during fact review, with `replan` as the escape hatch.

## Non-goals

- Automatic transcription of audio — relies on existing YouTube
  transcripts.
- Real-time or collaborative editing.
- Cross-source synthesis beyond what a human approves.

## Start here

- [Installation](getting-started/installation.md) — set up the tool.
- [First ingest](getting-started/first-ingest.md) — walk through a
  single source end to end.
- [Architecture overview](architecture/overview.md) — how the pieces
  fit together.
- [Roadmap](roadmap/index.md) — what's built, what's planned.
- [Contributing](contributing.md) — dev workflow and style.
