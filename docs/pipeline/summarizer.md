# Stage 4: Summarizer

The summarizer generates LLM-written prose for an entity from its
approved facts. It runs once per review session after all fact
decisions are made, batching regeneration for all touched entities.

## Purpose

Produce the entity markdown file — the view readers consume. The YAML
is the source of truth; the markdown is a regenerable view. If the two
disagree (because someone hand-edited the markdown), the YAML wins on
next regeneration.

## Input

- Approved facts for the entity (from the DB).
- Entity index (for cross-reference awareness in prose).
- Wiki setting description (from `.wiki-context.yaml`).

## Output

Entity markdown file, overwritten in full on each regeneration:

```markdown
# Aldara

## Summary

Aldara is a kingdom founded in the Second Age by the grandfather of King
Theron. Its ruling bloodline has remained unbroken since its founding.
Some tavern rumors suggest the founding king was cursed by an elven
sorceress, though this is unconfirmed.

## Facts

### Authoritative

[^1]: "Theron's grandfather founded Aldara in the Second Age."
[^2]: "Aldara's kings have always come from the Theron bloodline."

### Hearsay

[^3]: "The founding king was cursed by an elven sorceress."

### Disproven

[^4]: ~~The founding king was mortal.~~ — Later shown to be half-fae.

## References

1. Worldbuilding Session 3 — https://youtube.com/watch?v=abc123

[^1]: "Theron's grandfather founded Aldara in the Second Age."  — DM, [0:04:32-0:04:41](https://youtube.com/watch?v=abc123&t=272) (session: 2026-01-15)
[^2]: "Aldara's kings have always come from the Theron bloodline."  — DM, 0:06:02 (session: 2026-01-20)
[^3]: "The founding king was cursed by an elven sorceress."  — Innkeeper NPC, [1:23:40-1:24:15](https://youtube.com/watch?v=abc123&t=5020) (session: 2026-02-03)
[^4]: "The founding king was mortal."  — DM, 0:08:00 (session: 2026-02-03)
  *Later shown to be half-fae.*
```

## Summarizer rules

- **Authoritative facts** stated as plain fact in summary prose.
- **Trustworthy facts** stated as fact but with the source surfaced in
  prose ("According to Maester Aemon, …", "Guild records attest
  that …"). Grouped in their own `### Trustworthy` section under
  `## Facts`, below Authoritative and above Hearsay. The domain
  warrant from `status_reason` is rendered as an italicized note
  under the footnote, parallel to the hearsay treatment.
- **Hearsay facts** attributed and hedged ("tavern rumors suggest…",
  "one account holds…").
- **Disproven facts** excluded from summary by default; rendered
  struck-through in their own section with the reason.
- Every summary sentence cites fact IDs; citation labels in the
  rendered view are footnote numbers.

## Model slot

The summarizer uses the `models.summarizer` config key if set, falling
back to `models.primary`. Override per-session by passing a model name
to `review`.

## Zero-fact entities

Entities with no approved facts get a mechanical stub (heading + aliases
only) with no LLM call.

## Batched page step

Regeneration runs once per `review` session after all fact decisions are
made, not per approval. Interrupted review (Ctrl-C) writes no pages;
resuming and completing the session triggers the batch.

## Linked-entity propagation

When a fact is approved for entity A that co-targets entity B (via `fact_targets`),
the page step also regenerates B's page. This is **one-hop, non-transitive**:
only entities directly sharing a fact with a touched entity are included.

The regeneration set is `touched ∪ linked(touched)`, where `linked` is the
symmetric co-target relation. Touched entities are regenerated first, then linked
entities (sorted by category and slug).

Each entity's LLM prompt includes a **linked-entities context block** listing the
facts of its one-hop linked entities (grouped by epistemic status). The LLM may
synthesize a claim drawn from a linked entity's fact, applying the same epistemic-status
hedging rules as for the entity's own facts. The rendered `## Facts` section on the
page lists only the entity's own facts.

## Rebuild

Regenerate all entity pages from scratch and reconcile the filesystem
against the DB:

```bash
auto-lorebook wiki rebuild
```

Every non-superseded entity page is regenerated using the page step
(prose + linked-entity propagation logic). After regeneration, the
command scans each entity-category subdirectory (`characters`,
`locations`, `factions`, `events`, `items`, `concepts`) and deletes
any `.md` file that has no matching entity in the DB — recovering from
corruption, a crashed page step, or a renamed entity.

The wiki root and `.wiki-state/` directory are not scanned; only the
six category subdirectories are touched.

Staleness-skip (regenerate only entities whose facts changed since the
last build) is future work.
