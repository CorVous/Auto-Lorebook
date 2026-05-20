# Entity summaries synthesize across linked entities

Stage 4 generates an entity's summary prose with an LLM that receives,
alongside the entity's own facts, the facts of every *linked* entity —
any entity co-targeted by a shared fact. The summary prose may
therefore state and cite a claim drawn from a linked entity's facts
even though that fact does not target this entity, so a new fact on one
entity propagates to re-summarize its linked entities (one hop).

## Considered Options

- **Self-only.** Each summary uses only its own entity's facts.
  Rejected: cross-entity context is impossible, and a neighbour's page
  goes silently stale once a relevant fact lands on a linked entity.
- **Pulled-in facts.** An entity page renders the facts of its linked
  entities directly. Rejected: redefines "entity page = its own
  approved facts", double-counts facts across pages, and bloats every
  page with its neighbourhood.

## Consequences

- The `## Facts` section still lists only the entity's own
  `fact_targets`. A summary sentence drawn from a linked fact renders a
  cross-reference footnote pointing at the linked entity's page
  (anchored to that fact), not a duplicated citation.
- The entity-page staleness hash must include linked entities' facts,
  not just the entity's own — see `docs/architecture/staleness.md`.
- One new fact on a well-connected hub entity can force LLM
  re-summarization of every linked entity. Propagation is capped at one
  hop (no transitive cascade) to bound this cost.
