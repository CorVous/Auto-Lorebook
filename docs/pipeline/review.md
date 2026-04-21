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

### Claim-group ordering

Proposals sharing a `claim_group_id` are shown in a contiguous block,
so the human reads the claim once and then decides per-target. The
header names the group's position and size. Review order within a
group is arbitrary; across groups, order follows transcript position.

```
─── Proposal 1 of 12  ·  Claim group cg-001 (1 of 3 targets) ────────

Target entity: Aldara (existing)
  Matched via: alias "the Aldaran Realm"
Section: founding

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

Also routes to:
  → Theron (lineage)              — next
  → Second Age (events-in-era)    — then

[a]pprove  [e]dit  [r]eject  [p]lay (open URL)
>
```

After approval or rejection, the next claim-group sibling shows with
an abbreviated header — the claim text and locator are unchanged; only
the target and section differ:

```
─── Proposal 2 of 12  ·  Claim group cg-001 (2 of 3 targets) ────────

Target entity: Theron (existing)
Section: lineage

(Same claim text, locator, and context as previous proposal.)

[a]pprove  [e]dit  [r]eject  [p]lay (open URL)
>
```

An edit to `text` inside a claim group applies only to the proposal
being edited — sibling proposals keep the original text. This is
deliberate: the human might want to phrase the founding claim
differently on Theron's page than on Aldara's, and forcing edits to
propagate across siblings would undercut that. If propagation _is_
wanted, the user edits each sibling in turn.

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
