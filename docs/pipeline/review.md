# Human fact review

Fact review is the per-fact approval gate. It is the only point where
new facts — and new entities — enter the wiki.

## New entity creation during review

When a proposal targets an entity the planner marked `new`, nothing in
the wiki has been created yet. The entity stub is created atomically
with the first approval of a fact targeting it:

1. On the first approval, the tool creates `<category>/<slug>.yaml`
   with canonical name, aliases confirmed so far, `created_at`, and
   `created_by_ingest` set to the current ingest ID. It then appends
   the approved fact.
2. Subsequent approvals for the same entity in the same review
   session just append to the now-existing file.
3. Aliases confirmed during any approval for that entity are merged
   into its `aliases` list (on stub creation for the first approval;
   on update for later ones).
4. The in-memory entity index is refreshed after each approval, so a
   proposal reviewed later in the same session that references an
   entity created earlier in the session sees it as existing.

If every proposal for a proposed new entity is rejected, no stub is
ever written. This falls out of the design rather than requiring
cleanup logic.

## MVP: terminal review

```bash
auto-lorebook review <ingest_id>
```

Walks through proposals one at a time. Each proposal must be approved,
edited, or rejected before the next is shown — there is no skip or
defer. If the user exits (Ctrl-C or closes the terminal), untouched
proposal files remain in `pending/<ingest_id>/proposals/`, and the
next invocation of `review` resumes with the first remaining file.

### Bundled multi-destination claims

Proposals sharing a `claim_group_id` are shown together as one bundle:
one screen, one decision. The reviewer reads the claim once and chooses
to approve, edit, or reject the whole bundle; on approval, a fact is
appended to each target entity's YAML (each preserving its own
`section`). Bundles are listed in transcript order across the run.

```
─── Bundle 1 of 8  ·  Claim group cg-001 (3 targets) ────────

Proposed text:
  "Theron's grandfather founded Aldara in the Second Age."

Raw transcript:
  "Fair-on's grandfather founded all-dara in the Second Age."

Corrections applied:
  • "Fair-on" → "Theron"       (global)
  • "all-dara" → "Aldara"      (reading)

Source: Worldbuilding Session 3
Locator: 0:04:32-0:04:41  → https://youtube.com/watch?v=abc123&t=272
Speaker: DM
Status: authoritative
Session date: 2026-01-15

Context:
  Before: "So let's talk about the founding of Aldara."
  After:  "And that's why the Theron name matters so much now."

Targets (3):
  → Aldara (existing)
      Section: founding
      Matched via: alias "the Aldaran Realm"
  → Theron (existing)
      Section: lineage
  → Second Age (NEW — events, will be created on approval)
      Section: events-in-era

[a]pprove  [e]dit  [r]eject  [p]lay (open URL)
>
```

#### Edit propagation

Choosing `edit` opens prompts for `text`, `speaker`, `status`, and
`status_reason`; whatever the reviewer types replaces the value on
**every** target in the bundle. Blank input keeps the original. The
per-target `section` is intentionally not editable in multi-target
bundles — each entity owns its own section heading; if the reviewer
needs to change one, they can edit the entity YAML afterwards. For
single-target bundles, `section` is editable as before.

Alias suggestions remain per-target: after approval, the prompt walks
each target's suggested aliases in turn, and a `y` confirmation merges
the alias into that specific entity (with the per-session dedup of
`docs/architecture/entity-model.md` still in effect).

### New-entity proposals

For proposals targeting a proposed new entity that does not yet exist
on disk:

```
Target entity: War of the Dusk (NEW — events, will be created on approval)
  Proposed aliases: (none)
```

For proposals targeting an entity created earlier in the same review
session:

```
Target entity: War of the Dusk (events)
  Created earlier in this review session
```

This note is derived at display time by comparing the entity's
`created_by_ingest` to the current ingest ID — no extra state
required.

### Alias confirmation

For proposals where the planner matched via a mention that isn't yet
an alias, the display offers alias confirmation:

```
Target entity: Aldara (existing)
  Matched via: "the Realm" (not currently an alias)
  Add "the Realm" as alias? [y/n]
```

Alias confirmation is a sub-prompt, not a main action. It applies
immediately on `y` and is recorded in the entity YAML as an alias
record with `source: alias-confirmation` and `added_by_ingest` set to
the current ingest — or merged into the stub at creation time if the
entity is new and this is its first approval, in which case the source
on the first-approval aliases is `stub-creation`. Aliases added via
alias confirmation are cleanly removable by `reject-ingest`.

### Actions

- **Approve** — proposal becomes a fact, appended to the entity's YAML
  (creating the YAML if this is the first approval for a new entity),
  summary regenerated. Proposal file deleted.
- **Edit** — opens `text` (and optionally other fields) for inline
  editing. Tracks original as `text_source` on the approved fact.
  Then approves.
- **Reject** — proposal discarded.
- **Play** — prints the URL; user clicks through to verify against
  audio. Play is not a decision — after playing, the user still must
  approve, edit, or reject the current proposal.

Status, speaker, and section can all be overridden during review.
Text edits are supported but optional — approving as-is is fine since
the summarizer produces readable prose downstream.

## No skip, no defer

A "come back to it later" action would accumulate stale state that
drifts out of sync with the entity index and planner output. If review
surfaces systematic problems, the correct move is
[`replan`](planner.md#replan-escape-hatch), not deferral.

## Post-MVP: web review UI

Same actions, richer presentation. Side-by-side views, inline editing,
one-click playback. See [roadmap](../roadmap/index.md).

Next stage: [Stage 4 summarizer](summarizer.md).
