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

## Rebuild

Regenerate summaries from scratch for all entities:

```bash
auto-lorebook wiki rebuild
```
