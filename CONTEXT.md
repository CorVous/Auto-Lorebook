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
Row in the `proposals` table representing one route awaiting review.
_Avoid_: pending fact.

**Fact**:
Approved entry in the `facts` table, linked to one or more entities via `fact_targets`. Created only by Review; never produced by earlier stages.
_Avoid_: claim (a claim is pre-approval; a fact is post-approval).

**Fact target**:
One row in `fact_targets` linking a fact to an entity. A fact with N targets replaces the former N-YAML-copies-with-shared-`claim_group_id` pattern.

**Fact ref**:
Typed directed edge in `fact_refs` between two facts. Types: `supersedes | contradicts | corroborates | qualifies`. A `supersedes` edge automatically sets the target fact's status to `disproven`; removing it restores the prior status from `status_history`.

**Bundle-level edit**:
Edit applied at the bundle screen; propagates to every checked route. Scope is `{text, status, status_reason}` — fields that describe the claim itself, not the routing.

**Per-target override**:
Edit applied to one route only via `[t]argets`. Scope is `{section, speaker}` — fields that are inherently route-shaped (different entities have different sections; speaker can vary per attribution).

**Alias confirmation**:
Per-route sub-prompt that fires after approve/edit, before any writes, asking whether a planner-suggested mention should become a permanent alias for the target entity.

**Ingest**:
One end-to-end run from source to (possibly partial) approved facts. Identified by `ingest_id`; recorded on every entity stub and alias as `created_by_ingest` / `added_by_ingest`.

### Summarizer-stage terms

**Linked entity**:
Entity sharing at least one fact with a given entity via `fact_targets`. Symmetric relation. A summary regeneration of an entity propagates to its linked entities.
_Avoid_: connected entity, related entity, neighbour.

## Relationships

- A **Reading** produces zero or more **Plans** (one per replan).
- A **Plan** contains many **Claim groups**; each claim group contains one or more **Routes**.
- Extraction emits one **Proposal** per **Route**.
- A **Bundle** is the runtime view of one **Claim group** during review.
- Approving a **Bundle** creates one **Fact** plus N **Fact targets** (one per checked route) in a single DB transaction.
- Two entities are **linked** when a **Fact** targets both; regenerating one entity's summary propagates to its **linked entities**.

## Invariants

- **Plan/proposal correspondence**: at the start of `review`, the set of proposal rows must correspond 1:1 to `(claim_group_id, target_entity)` keys in the plan. Missing keys (Ctrl-C resume after partial approval) are allowed — proposals are a subset of plan routes. Extra keys (orphans) raise `ReviewError` and direct the user to `replan`.
- **Alias decline memory**: declined aliases are remembered in-memory for the duration of one `run()` call only. Ctrl-C resume re-asks for declined aliases (only accepted aliases survive on disk via `added_by_ingest`).
- **Idempotent re-approval**: encountering a proposal whose `proposed_id` already exists in `facts` is a silent skip — the proposal row is deleted and the run continues. The proposal row is deleted in the same transaction that commits the fact row, so there is no window.
- **Status audit asymmetry**: edits to a fact's `text` preserve the original in `text_source`; edits to `status` / `status_reason` do not preserve the planner's original. `status_history` records only the reviewer's final value. Rationale: text is a literal claim that can be objectively wrong, status is a reviewer judgment call.

## Flagged ambiguities

- "Target" appears in code (`target_entity`, `targets:`) and the doc uses both "target" and "route". Resolution: **route** is the preferred domain term for the row; **target** is acceptable when emphasising the destination entity.
- "Claim" vs "fact": a claim is a pre-approval assertion travelling through reading/plan/proposal; a **fact** only exists once Review has appended it to an entity.
