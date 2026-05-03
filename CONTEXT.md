# Auto-Lorebook

Pipeline that turns recorded sessions into a structured wiki of facts about
in-world entities. This file captures the domain language used inside the
review stage and its immediate neighbours; broader pipeline structure lives
in `docs/architecture/overview.md`.

## Language

### Pipeline stages

**Reading**:
LLM-produced summary of one source, organised into sections of bullets, gated by human approval.
_Avoid_: transcript summary, notes.

**Plan**:
Routing layer mapping each reading bullet to one or more entities. Intermediate; no approval gate, no filesystem writes.
_Avoid_: routing pass.

**Extraction**:
Per-claim-group step that locates the raw transcript span and emits one proposal file per route.
_Avoid_: claim extraction.

**Review**:
Per-bundle approval gate; the only point at which entities and facts enter the wiki.
_Avoid_: approval, fact review (legacy).

### Review-stage terms

**Claim group**:
Set of routes that share `claim_group_id`. Locator and raw-transcript span are computed once and copied across the group.

**Bundle**:
The on-screen unit during review — one claim group rendered as a single decision screen.
_Avoid_: batch, group screen.

**Route**:
One row in a bundle: a `(claim_group_id, target_entity)` pair. Carries a `proposed_section` and inherits the bundle's claim text.
_Avoid_: target row, destination (target is fine in code).

**Proposal**:
On-disk YAML in `pending/<ingest_id>/proposals/` representing one route awaiting review.
_Avoid_: pending fact.

**Fact**:
Approved entry inside an entity YAML's `facts` list. Created only by Review; never produced by earlier stages.
_Avoid_: claim (a claim is pre-approval; a fact is post-approval).

**Bundle-level edit**:
Edit applied at the bundle screen; propagates to every checked route. Scope is `{text, status, status_reason}` — fields that describe the claim itself, not the routing.

**Per-target override**:
Edit applied to one route only via `[t]argets`. Scope is `{section, speaker}` — fields that are inherently route-shaped (different entities have different sections; speaker can vary per attribution).

**Alias confirmation**:
Per-route sub-prompt that fires after approve/edit, before any writes, asking whether a planner-suggested mention should become a permanent alias for the target entity.

**Ingest**:
One end-to-end run from source to (possibly partial) approved facts. Identified by `ingest_id`; recorded on every entity stub and alias as `created_by_ingest` / `added_by_ingest`.

## Relationships

- A **Reading** produces zero or more **Plans** (one per replan).
- A **Plan** contains many **Claim groups**; each claim group contains one or more **Routes**.
- Extraction emits one **Proposal** per **Route**.
- A **Bundle** is the runtime view of one **Claim group** during review.
- Approving a **Bundle** appends one **Fact** per checked **Route** to its target entity's YAML.

## Flagged ambiguities

- "Target" appears in code (`target_entity`, `targets:`) and the doc uses both "target" and "route". Resolution: **route** is the preferred domain term for the row; **target** is acceptable when emphasising the destination entity.
- "Claim" vs "fact": a claim is a pre-approval assertion travelling through reading/plan/proposal; a **fact** only exists once Review has appended it to an entity.
