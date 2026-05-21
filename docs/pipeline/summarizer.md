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
[Theron](../characters/theron.md). Its ruling bloodline has remained unbroken
since its founding.[^f-n01] Some tavern rumors suggest the founding king was
cursed by an elven sorceress, though this is unconfirmed.

## Facts

### Authoritative

[^fact-001]: "Theron's grandfather founded Aldara in the Second Age."
[^fact-002]: "Aldara's kings have always come from the Theron bloodline."

### Hearsay

[^fact-003]: "The founding king was cursed by an elven sorceress."

### Disproven

[^fact-004]: ~~The founding king was mortal.~~ — Later shown to be half-fae.

## References

1. Worldbuilding Session 3 — https://youtube.com/watch?v=abc123

[^fact-001]: "Theron's grandfather founded Aldara in the Second Age."  — DM, [0:04:32-0:04:41](https://youtube.com/watch?v=abc123&t=272) (session: 2026-01-15)
[^fact-002]: "Aldara's kings have always come from the Theron bloodline."  — DM, 0:06:02 (session: 2026-01-20)
[^fact-003]: "The founding king was cursed by an elven sorceress."  — Innkeeper NPC, [1:23:40-1:24:15](https://youtube.com/watch?v=abc123&t=5020) (session: 2026-02-03)
[^fact-004]: "The founding king was mortal."  — DM, 0:08:00 (session: 2026-02-03)
  *Later shown to be half-fae.*

[^f-n01]: "Theron's bloodline has remained unbroken." — [Theron](../characters/theron.md#fn:f-n01)
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
  rendered view are stable anchors derived from the fact's ID
  (e.g. `[^fact-001]`), not positional counters.

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

`run_page_step` accepts an optional `removed_entities` list for
reject-ingest reconciliation. Removed entities' pages are deleted before
regeneration begins. Any entity in both `removed_entities` and
`touched_entities` is treated as removed only — it is not re-summarized.
If only `removed_entities` is supplied (no `touched_entities`), the page
step still runs to delete those pages.

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

## Linked-context token budget

A hub entity with many linked entities could overflow the model's context window.
The budgeter (`linked_budget.budget_linked_context`) caps the linked-entities block
to a configurable fraction of the context window (`summarizer.linked_context_budget_fraction`,
default 0.25).

**Priority ordering** (highest to lowest):

1. Shared facts (fact targets both the subject entity and the linked entity).
2. Non-shared authoritative facts.
3. Non-shared trustworthy facts.
4. Non-shared hearsay facts (dropped after disproven).
5. Non-shared disproven facts (dropped first when over budget).

**Entity ranking**: entities with more shared facts appear first (nearest-first);
ties broken alphabetically by `(category, slug)`. The entity-count cap
(`max_linked_entities`) is applied before token counting.

**Token estimate**: `len(rendered_block) // 4`, consistent with the preamble budget heuristic.

**Degrade gracefully**: if no linked entity fits within the budget (its minimal
single-fact block is larger than the budget), the budgeter raises
`LinkedContextTooLargeError`. The page step catches this, logs a warning, and
summarizes the entity from its own facts only.

Configure the budget fraction in `config.yaml`:

```yaml
summarizer:
  linked_context_budget_fraction: 0.25
```

## Cross-references and entity links

The LLM may embed two kinds of inline markers in its prose output, which
the renderer resolves before writing the page.

**Entity links** — `[[category/slug]]` markers become clickable markdown
links using the entity's canonical name. Same-category links resolve to a
bare `slug.md` path; cross-category links resolve to `../category/slug.md`.
If a marker cannot be found in the entity index, the renderer degrades it
to plain `category/slug` text and logs a warning — the page is always
written.

**Cross-reference citations** — `[[fact:<id>]]` markers indicate that the
preceding prose sentence draws on a linked entity's fact. The renderer
replaces each marker with a footnote reference `[^<id>]` and appends a
cross-reference footnote definition that quotes the source fact text and
links to the linked entity's page anchored at `#fn:<id>` (Python-Markdown
footnote convention). Only facts actually cited in prose generate crossref
footnotes; the full linked-entity fact set is available to the LLM for
context but does not appear unless cited. Unresolvable markers degrade to
plain text and log a warning.

Example crossref footnote in rendered output:

```markdown
[^f-n01]: "Theron's bloodline has remained unbroken." — [Theron](../characters/theron.md#fn:f-n01)
```

## Rebuild

Regenerate summaries from scratch for all entities:

```bash
auto-lorebook wiki rebuild
```
