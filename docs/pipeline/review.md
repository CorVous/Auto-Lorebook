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

Walks through claim bundles one at a time. Each bundle must be
approved, edited, or rejected before the next is shown — there is no
skip or defer. If the user exits (Ctrl-C or closes the terminal),
untouched proposal files remain in `pending/<ingest_id>/proposals/`,
and the next invocation of `review` resumes with the first remaining
bundle.

### Claim-group bundling

Proposals sharing a `claim_group_id` are bundled into a single review
screen with a single decision. The human reads the claim once and
approves, edits, or rejects the whole bundle at once; all targets ride
on that decision. The header names the bundle's position and the
number of targets it covers. Review order across bundles follows
transcript position.

```
─── Bundle 1 of 6  ·  Claim group cg-001 (3 targets) ────────────────

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

Routes to:
  [x] Aldara (existing) — founding
        Matched via: alias "the Aldaran Realm"
  [x] Theron (existing) — lineage
  [x] Second Age (existing) — events-in-era

[a]pprove  [e]dit  [t]argets  [r]eject  [p]lay (open URL)
>
```

`[a]pprove` writes the claim to every checked target. `[r]eject` drops
the whole bundle. `[t]argets` opens an inline checklist where the user
can uncheck individual destinations before approving — the unchecked
targets are dropped on approval, the same as if they had been rejected
individually. A bundle with zero remaining targets cannot be approved;
it must be rejected.

An edit to `text` propagates across all targets in the bundle by
default, since one approval covers the whole group. If the user wants
the claim phrased differently per target, they reject the bundle and
re-enter the targets as separate facts during a follow-up pass.

### New-entity rows

A target row in the checklist that points at a proposed new entity not
yet on disk renders as:

```
  [x] War of the Dusk (NEW — events, will be created on approval)
        Proposed aliases: (none)
```

A row pointing at an entity created earlier in the same review session
renders as:

```
  [x] War of the Dusk (events)
        Created earlier in this review session
```

The "created earlier" note is derived at display time by comparing the
entity's `created_by_ingest` to the current ingest ID — no extra state
required. Unchecking a NEW row before approval is equivalent to
rejecting just that target; if every approved bundle ends up
unchecking a given proposed entity, no stub is ever written.

### Alias confirmation

When any target in the bundle was matched via a mention that isn't yet
an alias, the row carries an alias-confirmation sub-prompt resolved
before the bundle decision is taken:

```
  [x] Aldara (existing) — founding
        Matched via: "the Realm" (not currently an alias)
        Add "the Realm" as alias? [y/n]
```

Alias confirmation is a per-row sub-prompt, not a main action. It
applies immediately on `y` and is recorded in the entity YAML as an
alias record with `source: alias-confirmation` and `added_by_ingest`
set to the current ingest — or merged into the stub at creation time
if the entity is new and this is its first approval, in which case the
source on the first-approval aliases is `stub-creation`. If the user
later unchecks that target via `[t]argets`, or rejects the bundle
entirely, the alias write is rolled back along with the rest. Aliases
added via alias confirmation are cleanly removable by `reject-ingest`.

### Actions

- **Approve** — for each checked target, the claim becomes a fact
  appended to that entity's YAML (creating the YAML if this is the
  first approval for a new entity), summary regenerated. Sibling
  proposal files for the bundle are deleted; unchecked targets are
  treated as rejected and their proposal files are also deleted.
- **Edit** — opens `text` for inline editing; the edit applies to
  every checked target in the bundle. Original is tracked as
  `text_source` on each approved fact. Then approves.
- **Targets** — opens the route checklist so individual destinations
  can be unchecked (or re-checked) before the bundle decision. Status,
  speaker, and section overrides are taken per row from this
  sub-prompt; defaults come from the planner.
- **Reject** — entire bundle discarded; every sibling proposal file
  deleted.
- **Play** — prints the URL; user clicks through to verify against
  audio. Play is not a decision — after playing, the user still must
  approve, edit, or reject the current bundle.

Per-target status, speaker, and section overrides live behind
`[t]argets`. Text edits are supported but optional — approving as-is
is fine since the summarizer produces readable prose downstream.

## No skip, no defer

A "come back to it later" action would accumulate stale state that
drifts out of sync with the entity index and planner output. If review
surfaces systematic problems, the correct move is
[`replan`](planner.md#replan-escape-hatch), not deferral.

## Post-MVP: web review UI

Same actions, richer presentation. Side-by-side views, inline editing,
one-click playback. See [roadmap](../roadmap/index.md).

Next stage: [Stage 4 summarizer](summarizer.md).
