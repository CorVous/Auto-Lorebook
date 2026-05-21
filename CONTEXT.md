# Auto-Lorebook

Domain language for the **review** stage and its immediate neighbours — the
part of the Auto-Lorebook pipeline where extracted claims become approved
facts about in-world entities. Broader pipeline structure lives in
`docs/architecture/overview.md`.

## Language

### Pipeline stages

**Reading**:
LLM-produced summary of one source, organised into sections of bullets, gated by human approval.
_Avoid_: transcript summary, notes.

**Plan**:
Routing layer mapping each reading bullet to one or more entities. Intermediate — no approval gate.
_Avoid_: routing pass.

**Extraction**:
Per-claim-group step that locates the verbatim transcript span behind each claim and produces one proposal per route.
_Avoid_: claim extraction.

**Review**:
Per-bundle approval gate; the only point at which entities and facts enter the wiki.
_Avoid_: approval, fact review (legacy).

### Review-stage terms

**Claim group**:
Set of routes derived from the same reading bullet. Its transcript locator and verbatim span are computed once and shared across the group.

**Bundle**:
The on-screen unit during review — one claim group rendered as a single decision screen.
_Avoid_: batch, group screen.

**Route**:
One line in a bundle: a single claim paired with a single target entity. Carries a proposed section and inherits the bundle's claim text.
_Avoid_: target row, destination (target is fine when emphasising the entity).

**Proposal**:
One route awaiting review — a claim routed to an entity, not yet approved.
_Avoid_: pending fact.

**Fact**:
An approved claim recorded in the wiki, attached to one or more entities. Created only by Review; never produced by earlier stages.
_Avoid_: claim (a claim is pre-approval; a fact is post-approval).

**Fact target**:
The link between one fact and one entity it describes. A fact may have several targets — the same claim attached to multiple entities.

**Fact ref**:
A typed, directed relationship between two facts: one of `supersedes`, `contradicts`, `corroborates`, or `qualifies`. A `supersedes` edge marks the older fact as disproven.

**Bundle-level edit**:
An edit made at the bundle screen that propagates to every route in the bundle. Scoped to fields describing the claim itself — its text and epistemic status — not the routing.

**Per-target override**:
An edit made to a single route. Scoped to fields that are inherently route-shaped — section and speaker — since different entities take different sections and attribution can vary.

**Alias confirmation**:
A per-route prompt, shown after a route is approved, asking whether a planner-suggested mention should become a permanent alias for the target entity.

**Ingest**:
One end-to-end run from source to approved facts, possibly partial. Every entity and alias records the ingest that created it.

### Summarizer-stage terms

**Linked entity**:
Entity sharing at least one fact with a given entity via `fact_targets`. Symmetric relation. A summary regeneration of an entity propagates to its linked entities.
_Avoid_: connected entity, related entity, neighbour.

## Relationships

- A **Reading** produces zero or more **Plans** (one per replan).
- A **Plan** contains many **Claim groups**; each claim group contains one or more **Routes**.
- Extraction emits one **Proposal** per **Route**.
- A **Bundle** is one **Claim group** as presented during review.
- Approving a **Bundle** creates one **Fact** plus one **Fact target** per approved route.
- Two entities are **linked** when a **Fact** targets both; regenerating one entity's summary propagates to its **linked entities**.

## Example dialogue

> **Dev:** "When the reviewer approves a **Bundle**, are they approving one claim or several?"
> **Reviewer:** "One claim — the **Bundle** is a single **Claim group** on screen. But that claim can have several **Routes**, one per entity it's attached to. Approving creates one **Fact** and one **Fact target** per route."
> **Dev:** "If I fix a typo in the claim text, does that touch every route?"
> **Reviewer:** "Yes — text is a **bundle-level edit**, so it propagates to all routes. Changing which section the claim lands in for just one entity is a **per-target override** instead."
> **Dev:** "And before approval the claim isn't a fact yet?"
> **Reviewer:** "Right. Pre-approval it travels as a **Proposal**. It only becomes a **Fact** once Review writes it to the wiki."

## Flagged ambiguities

- **Target** vs **route**: _route_ is the preferred term for the line in a bundle; _target_ is acceptable when emphasising the destination entity.
- **Claim** vs **fact**: a claim is a pre-approval assertion travelling through reading, plan, and proposal; a **fact** only exists once Review has appended it to an entity.
