# Coding Principles

Follow these principles when designing and building systems.

## Trust & Verifiability
- Every derived output must trace back to a specific, addressable location in its source.
- Preserve raw inputs untouched; store corrections as separate layers that reference the original.
- Build the audit trail into the data itself (timestamps, actor, provenance on records) — not as a parallel log.
- On every generated artifact, snapshot hashes of its inputs plus the identity of the process that produced it.

## Human/Machine Division of Labor
- Machines do mechanical work (parsing, locating, routing, formatting). Humans make judgment calls (semantic correctness, classification, existence).
- Mechanically enforce anything a deterministic check can verify. For everything else, surface it for review — don't fake certainty.
- No silent decisions: state changes that matter pass through explicit approval. No skip, no defer.
- Review surfaces should be small: show the extracted decisions with links back to the raw, not the raw itself.

## Bias Under Uncertainty
- Decide which error is more expensive; tilt toward the cheaper one. Missing something irrecoverable usually beats surfacing something spurious.
- Over-flag uncertainty. A dismissed flag costs seconds; a swallowed ambiguity pollutes downstream.
- Explicit over implicit: emit empty records rather than omitting them. Visible emptiness is reviewable; absence is invisible.
- Fail loudly rather than defaulting on missing required metadata.

## State Boundaries
- One source of truth per concept. Derive caches freely; never let them become authoritative.
- Separate proposed from committed state by location. Only approved state reaches the canonical store.
- Identity lives in the data. If the index can be rebuilt from the data, it's a cache.
- Structured form is truth; rendered form is a view. Hand-edits to views get overwritten — edits belong in the source.

## Pipeline Design
- Stages have narrow contracts with output shapes tight enough to mechanically validate.
- Gate sparingly. Put human approvals only where judgment is required; let intermediate stages run automatically.
- Provide escape hatches ("discard and re-run from stage N"), not branching recovery logic.
- Parallelize independent units of work.
- Upstream stages give advisory hints; downstream stages produce authoritative results.

## Staleness & Change Detection
- Detect staleness by input hashes, not timestamps. Content equality is what matters.
- Tier the response by pipeline position: refuse on pending artifacts, warn on approved ones, audit-only on committed data. A human approval is a commitment — don't let upstream drift silently revoke it.
- When refusing, name the specific remedy command.
- Distinguish metadata that changes meaning from metadata that only changes provenance; only the former invalidates downstream.

## Schema & Versioning
- Version every persisted file format from day one. One monotonic integer per file type, checked on read.
- Refuse to read versions newer than known; name the upgrade remedy.
- Migrations run lazily on read, write back the upgraded form, log the change. No version skipping.
- Each file type versions independently.

## Identifiers
- Hash content, not paths. Moves and renames should not create duplicates.
- Stable IDs enable idempotent re-runs — detect "seen before" and refuse to duplicate.
- Provide a manual override for the rare collision.

## Defaults & Configuration
- Cascade defaults in a documented priority order. Show defaults inline.
- Every prompt is skippable. Don't force answers the user doesn't have.
- Detect environment (TTY vs. piped) and degrade gracefully.
- Save partial progress on interrupt.

## Atomicity
- Create side-effectful records atomically with the decision that justifies them — not speculatively.
- If every decision is rejected, no trace should remain — by construction, not cleanup.
- Tag every created record with the operation that produced it, so bulk-undo is a filter.

## Format Discipline
- Canonical format on write, lenient on read.
- Give structurally different kinds of the same primitive distinct formats so they can't be confused.
- Post-process presentation concerns at render boundaries, not inside the core pipeline.

## Incremental Delivery
- Phase the build around end-to-end slices, each with a demonstrable exit criterion on real input.
- MVP tolerates hand-editing. Don't automate a task until the manual path hurts.
- Keep a deferred list with the specific trigger that would promote each item.
- Prefer simpler-and-higher-quality over cleverer-and-more-scalable until you hit the actual wall.