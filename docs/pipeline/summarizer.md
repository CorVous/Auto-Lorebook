# Stage 4: Summarizer

The summarizer regenerates readable summary prose for an entity from
its approved facts. It runs after fact approvals and may batch
regeneration at session end rather than per-fact for efficiency.

## Purpose

Produce the entity markdown file — the view readers consume. The YAML
is the source of truth; the markdown is a regenerable view. If the two
disagree (because someone hand-edited the markdown), the YAML wins on
next regeneration.

## Input

- Entity YAML (all approved facts). See
  [entity model](../architecture/entity-model.md).
- Entity index (for alias-aware rendering of cross-references).

## Output

Entity markdown file, overwritten in full on each regeneration:

```markdown
# Aldara

## Summary

Aldara is a kingdom founded in the Second Age [^1][^2] by the grandfather
of King Theron [^1]. Its ruling bloodline has remained unbroken since
its founding [^3]. Some tavern rumors suggest the founding king was
cursed by an elven sorceress [^4], though this is unconfirmed.

## Facts

### Authoritative

**Founding**

[^1]: "Theron's grandfather founded Aldara in the Second Age."
  — DM, [Worldbuilding Session 3, 0:04:32-0:04:41](https://youtube.com/watch?v=abc123&t=272)
  (session: 2026-01-15)

[^2]: "Scholars dispute the exact year, but the Second Age attribution is well-attested."
  — DM, [Worldbuilding Session 3, 0:06:02-0:06:14](https://youtube.com/watch?v=abc123&t=362)
  (session: 2026-01-15)

**Government**

[^3]: "Aldara's kings have always come from the Theron bloodline."
  — DM, Campaign Notes, lines 47-48 (session: 2026-01-20)

### Hearsay

[^4]: "The founding king was cursed by an elven sorceress."
  — Innkeeper NPC, [Worldbuilding Session 3, 1:23:40-1:24:15](https://youtube.com/watch?v=abc123&t=5020)
  (session: 2026-02-03)
  *Told to the party by a tavern NPC, not confirmed.*

### Disproven

_(none)_

## References

1. Worldbuilding Session 3 — https://youtube.com/watch?v=abc123
2. Campaign Notes — sources/txt-campaign-notes/notes.txt
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

## Section ordering and normalization

The summarizer reads the free-text `section` field on each fact and
groups facts by normalized section name — case-insensitive, trimmed.
If two facts have sections "founding" and "Founding", they group
together under the canonical casing of whichever appears more often,
with a tie broken by first-seen. This papers over drift without
forcing a controlled vocabulary.

A future enhancement may allow per-category section vocabularies in
`.wiki-context.yaml` — see [roadmap](../roadmap/index.md).

## Rebuild

Regenerate all summaries from scratch:

```bash
auto-lorebook wiki rebuild
```

By default, `wiki rebuild` skips regeneration for entity `.md` files
whose recorded inputs match the current entity YAML and index.
`wiki rebuild --force` regenerates unconditionally. See
[staleness](../architecture/staleness.md#integration-with-commands).
