# Stage 2: Planner

The planner routes claims from an approved reading to entities,
resolving entity identity (existing vs. new) and flagging ambiguity.
It is an intermediate stage with no approval gate and no filesystem
side effects.

## Purpose

- Route each claim bullet to one or more entities.
- Resolve entity identity: matches an existing entity by canonical
  name or alias, proposes a new entity, or flags as ambiguous.
- Suggest aliases for existing entities when the mention text isn't
  yet registered.

## Input

- Approved reading (frontmatter plus body).
- Entity index (in-memory, built from entity YAMLs).
- Existing entity YAML files for richer context where needed.
- Full prompt preamble — same assembly as the reading stage. See
  [context pipeline](context.md).

## Output

Single file at `pending/<ingest_id>/plan.yaml`:

```yaml
schema_version: 1
reading_id: yt-abc123
planned_at: 2026-04-20T14:58:33Z

entity_resolutions:
  - mention: "the Aldaran Realm"
    mention_locations: ["[4:30-8:00] founding"]
    resolution: existing          # existing | new | ambiguous
    matched_entity: Aldara
    rationale: "Listed in Aldara's aliases."
    suggested_aliases_to_add: []
  - mention: "the War of the Dusk"
    mention_locations: ["[8:00-12:00] war"]
    resolution: new
    proposed_entity_name: War of the Dusk
    proposed_category: events
    rationale: "No existing entity matches."
  - mention: "the elven sorceress"
    mention_locations: ["[1:23:40-1:24:15] hearsay"]
    resolution: ambiguous
    rationale: "Unnamed referent. Attach hearsay to Aldara with note."
    human_review_needed: true

new_entities:
  - name: War of the Dusk
    category: events
    aliases_suggested: []

planned_claims:
  - claim_group_id: cg-001
    reading_section: "[4:30-8:00] Founding of Aldara"
    reading_bullet_index: 0
    locator: "0:04:32"
    locator_hint: "0:04:25-0:04:50"
    proposed_speaker: DM
    proposed_status: authoritative
    proposed_status_reason: null
    targets:
      - entity: Aldara
        entity_state: existing    # existing | new
        proposed_section: founding
        rationale: "Claim concerns Aldara's founding."
      - entity: Theron
        entity_state: existing
        proposed_section: lineage
        rationale: "Claim establishes Theron's grandfather as founder."
      - entity: Second Age
        entity_state: new
        proposed_category: events
        proposed_section: events-in-era
        rationale: "Claim dates founding to the Second Age."

unresolved:
  - reading_section: "[8:00-12:00] The War of the Dusk"
    locator: "0:09:12"
    issue: "Reading flagged uncertain place name here; unresolved."
```

## Multi-target routing

A single claim can route to multiple entities when it carries
information about more than one. The example above routes one claim to
Aldara (founding), Theron (lineage), and the Second Age
(events-in-era). Each target gets its own proposal file on disk, but
[review](review.md) bundles siblings into a single decision: the
claim is shown once with all destinations as a checklist, and approve
writes to every checked target. The `[t]argets` action lets the human
uncheck individual destinations — e.g. accept onto Aldara and Theron
but drop the Second Age page — before approving the bundle.

Targets sharing a `claim_group_id` share the same
`raw_transcript_span`, `locator`, and extracted `text` — the
[extractor](extractor.md) locates once per claim group and copies the
result across the group's proposals.

The planner is instructed to route to a target only when the claim
directly concerns that entity, not merely mentions it. "Theron met
Aelindra at the Festival of Masks" routes to Theron and Aelindra, not
to the Festival (which is just the setting of the meeting, not the
subject of the claim). This is a soft constraint — systematic
over-routing surfaces during fact review as reject-heavy proposals for
incidental targets, and is a signal to `replan` with a hint.

Single-target routing remains the common case; the schema permits a
`targets` list of length one, and most planned claims will have
exactly that.

## No approval gate, no filesystem side effects

The planner is an intermediate stage, not a gate. Its output
(`plan.yaml` plus alias suggestions) is an audit artifact and input to
the extractor, which runs automatically after the planner completes.
The planner writes nothing to the wiki — new entities are proposals on
the plan, not stub YAMLs. Stub creation happens at fact review,
atomically with the first approved fact targeting each new entity.
See [fact review](review.md).

This is deliberate. The plan gate's main job — catching duplicate
entities and bad routing — is work the fact-review gate does equally
well: routing metadata (`matched_via`, resolution rationale,
new-vs-existing status) travels with each proposal, so a human
reviewing proposal #7 sees the same "wait, this should match Aldara"
signal they'd see in a plan review, with the added benefit of reading
the actual claim. And because the planner never touches the
filesystem, a hallucinated or poorly-routed new entity never pollutes
the entity index, and `replan` can discard unreviewed proposals with
no residue to clean up.

Inspection is still available:

```bash
auto-lorebook plans show <ingest_id>
```

## Replan escape hatch

If fact review reveals systematic routing errors (same misroute across
many proposals, an entity the planner missed entirely), bail out of
review and re-run the planner:

```bash
auto-lorebook replan <ingest_id>
```

This discards unreviewed proposals from the current run, re-invokes
the planner (which sees any entity YAMLs created since the last run,
including stubs created by approvals earlier in this ingest), and
re-runs the extractor. Proposals already approved are unaffected —
their facts are in entity YAMLs and the planner's new-entity detection
will see those entities as existing.

Next stage: [Stage 3 extractor](extractor.md).
