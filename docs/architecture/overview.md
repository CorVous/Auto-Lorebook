# Architecture overview

Auto-Lorebook is a four-stage pipeline with two human review gates,
built around one guiding principle: mechanical guarantees where cheap,
human verification where not. Verbatim span extraction is mechanically
verifiable; epistemic status is not.

## Pipeline

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
  [Human review gate]   approve / edit / reject reading
        │
        ▼  (invoke `auto-lorebook plan <id>`)
  Stage 2: Planner (LLM)       route claims → entities
        │                      (new entities exist only on the plan)
        ▼  (invoke `auto-lorebook extract <id>`)
  Stage 3: Extractor (LLM)     locate verbatim spans
        │                      (no gate between planner and extractor)
        ▼
  [Human review gate]   per-fact approve / edit / reject
        │              (first approval creates the entity stub;
        │               `replan` is the escape hatch)
        ▼
  Stage 4: Summarizer (LLM)    regenerate entity prose
```

## Stages in brief

- **[Context](../pipeline/context.md)** — per-source metadata in
  `info.yaml`, wiki-level setting in `.wiki-context.yaml`, global
  mishearing fixes in `.transcription-corrections.yaml`. Combined
  deterministically into a preamble fed to every LLM stage.
- **[Stage 1: Reading](../pipeline/reading.md)** — two LLM substages.
  1a segments and attributes speakers; 1b produces per-segment claim
  bullets. Human reviews the combined `reading.md` as one unit.
- **[Stage 2: Planner](../pipeline/planner.md)** — routes claims to
  entities. Resolves existing vs. new. Writes `plan.yaml` as an
  intermediate artifact; no filesystem side effects, no approval gate.
- **[Stage 3: Extractor](../pipeline/extractor.md)** — locates each
  planned claim's verbatim span in the raw transcript. Mechanically
  verified as a literal substring. Produces one proposal per target
  entity.
- **[Human fact review](../pipeline/review.md)** — the only gate where
  facts and entities enter the wiki. First approval of a claim
  targeting a new entity creates the entity stub atomically.
- **[Stage 4: Summarizer](../pipeline/summarizer.md)** — regenerates
  readable entity prose from approved facts. The YAML is truth; the
  markdown is a view.

## Two gates, not three

The reading gate catches "what was actually said"; the fact gate
catches "what should enter the wiki." The planner runs between them
without its own gate because its failure modes — duplicate entities,
bad routing — are equally visible at fact review, where routing
metadata travels with each proposal. If fact review reveals systematic
routing errors, `replan` is the escape hatch.

Because the planner has no filesystem side effects, hallucinated
entities never pollute the entity index, and `replan` can discard
unreviewed proposals with no cleanup needed.

## Cross-cutting invariants

- **[Repository layout](repository-layout.md)** — where things live:
  wiki repo vs. tool state directory.
- **[Schema versioning](schema-versioning.md)** — every YAML artifact
  carries a monotonic `schema_version`; migrations run lazily on read.
- **[Artifact staleness](staleness.md)** — every generated artifact
  records input SHA-256 hashes so stale artifacts can be detected and
  regenerated. Three tiers of response: refuse, warn, audit-only.
- **[Entity model](entity-model.md)** — entity identity lives in entity
  YAMLs. The filesystem is the registry; no separate index file.
- **[Timestamps](timestamps.md)** — two distinct kinds:
  source locators (`h:mm:ss`) and wall-clock events (RFC 3339).

## Key design principles

- **Missed claims are worse than spurious ones.** The pipeline tilts
  toward over-inclusion at every stage; the human filters.
- **Review is over claims, not transcript.** Segment titles are the
  scope-audit layer; bullets are the claim-review layer. A two-hour
  session review fits inside a 10–20 min/hour budget.
- **Raw evidence preserved at every stage.** Transcripts untouched;
  facts store their raw transcript span; audit trails everywhere.
- **The wiki filesystem only ever reflects human-approved state.**
  Pending work lives in `~/.auto-lorebook/pending/`.
- **Compounding corrections.** Name corrections accumulate in
  `.transcription-corrections.yaml`; aliases accumulate on entity
  YAMLs. The tool gets better as the wiki grows.
- **The YAML is truth; the markdown is a view.** Hand-edits to entity
  markdown are overwritten on regeneration.
- **Every proposal gets a decision.** No skip, no defer — approve,
  edit, or reject.
- **Filesystem is the registry.** Entity identity lives in entity
  YAMLs; no separate index file.
