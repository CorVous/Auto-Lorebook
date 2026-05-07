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

### Claim-group bundling

Proposals sharing a `claim_group_id` are shown as **one bundle
screen**: the claim text appears once, followed by a numbered
checklist of routes — one row per target. A single
approve / edit / reject decision covers every checked route. The
`[t]argets` action toggles individual routes on or off and is also
the home for per-target `section` / `speaker` overrides. Singletons
(claim groups with only one target) render the same screen with one
checked row and no `[t]` clutter; bundles span across groups in
transcript order.

```
─── Bundle 1 of 8  ·  Claim group cg-001 (3 of 3 routes selected) ────────

Proposed text:
  "Theron's grandfather founded Aldara in the Second Age."

Raw transcript:
  "Fair-on's grandfather founded all-dara in the Second Age."

Corrections applied:
  • "Fair-on" → "Theron"       (global)
  • "all-dara" → "Aldara"      (reading)

Source: Worldbuilding Session 3
Locator: 0:04:32-0:04:41  → https://youtube.com/watch?v=abc123&t=272
Status: authoritative
Session date: 2026-01-15

Context:
  Before: "So let's talk about the founding of Aldara."
  After:  "And that's why the Theron name matters so much now."

Routes:
  1. [x] Aldara (existing)            section=founding         speaker=DM
        Matched via: alias "the Aldaran Realm"
  2. [x] Theron (existing)            section=lineage          speaker=DM
  3. [x] Second Age (NEW — events, will be created on approval)
                                       section=events-in-era    speaker=DM

[a]pprove  [e]dit  [r]eject  [p]lay  [t]argets  [u]ndo
>
```

**Bundle-level edits** carry only `text`, `status`, and `status_reason`
— those are claim-level facts about the world, so they propagate to
every checked route and should agree across siblings.
**Per-target overrides** carry only `section` and `speaker` and live
in the `[t]argets` sub-prompt: different routes point at different
entities, so section is inherently route-shaped, and speaker
attribution can vary route-by-route. The two field sets are disjoint
by design — a reviewer cannot set a bundle-wide `section`.

### New-entity routes

A route targeting a proposed new entity that does not yet exist on
disk renders inline in the checklist:

```
  3. [x] War of the Dusk (NEW — events, will be created on approval)
        Suggested aliases: "the Dusk War"
```

A route whose entity was created earlier in the same review session:

```
  2. [x] War of the Dusk (events) — created earlier this session
```

This note is derived at display time by comparing the entity's
`created_by_ingest` to the current ingest ID — no extra state
required.

### Alias confirmation

For checked routes whose planner matched via a mention that isn't yet
an alias, alias confirmation fires as a per-route sub-prompt **after**
the user picks approve / edit, **before** any writes:

```
  Add "the Realm" as alias for Aldara? [y/n]
```

Alias confirmation only fires for routes that survived the bundle
selection — dropping a route via `[t]argets` skips its alias prompt.
Confirmed aliases are recorded with `source: alias-confirmation` and
`added_by_ingest` set to the current ingest — or merged into the
stub at creation time if the entity is new and this is its first
approval, in which case the source on the first-approval aliases is
`stub-creation`. Aliases added via alias confirmation are cleanly
removable by `reject-ingest`. On Ctrl-C resume, prompts already
answered earlier in the same ingest are not re-asked: the engine
seeds its dedup set from on-disk alias records whose
`added_by_ingest` matches the current source.

### Actions

- **Approve** — every checked route becomes a fact, appended to its
  target entity's YAML (creating the YAML if this is the first
  approval for a new entity). Unchecked routes are dropped — their
  proposal files are deleted.
- **Edit** — bundle-level edits to `text`, `status`, and
  `status_reason` propagate to every checked route. Per-target
  `section` / `speaker` overrides are set in `[t]argets`. Tracks
  original text as `text_source` on each affected fact.
- **Reject** — discards the whole bundle: every route's proposal
  file is removed, no entity is touched.
- **Play** — prints the URL; user clicks through to verify against
  audio. Play is not a decision.
- **Targets** — sub-prompt to toggle individual routes on / off and
  to set per-target `section` / `speaker` overrides on kept rows.
  Returns to the main prompt; the bundle then re-renders.
- **Undo** — resets the current bundle's accumulated state back to
  defaults: clears bundle-level edits, drops every per-target
  override, and re-checks every route (un-rejecting any routes
  toggled off via `[t]argets`). Scope is the bundle currently on
  screen — once a bundle has been approved or rejected and the next
  one is shown, undo cannot bring it back.

## No skip, no defer

A "come back to it later" action would accumulate stale state that
drifts out of sync with the entity index and planner output. If review
surfaces systematic problems, the correct move is
[`replan`](planner.md#replan-escape-hatch), not deferral.

## Post-MVP: web review UI

Same actions, richer presentation. Side-by-side views, inline editing,
one-click playback. See [roadmap](../roadmap/index.md).

Next stage: [Stage 4 summarizer](summarizer.md).
