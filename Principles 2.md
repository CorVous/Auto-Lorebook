# Coding Principles

## Correctness
- **Fail loud, never silent.** On stale input, missing required data, or unknown schema versions, refuse and name the specific problem *and its remedy*. Never default past corruption.
- **Mechanical checks over LLM/human judgment where possible.** Use deterministic verification (hashes, substring checks, range bounds) for anything verifiable. Reserve humans/LLMs for genuine judgment calls.
- **Preserve raw inputs.** Never mutate source data. Layer transformations on top with an audit trail.
- **One source of truth, regenerable views.** Designate a canonical artifact; treat rendered/derived forms as disposable. Hand-edits to views are lost on regen — intentionally.

## Pipeline design
- **Narrow each stage's contract.** A stage that does one thing can offer guarantees a multi-purpose stage can't. Don't combine concerns when narrowness buys a verifiable property.
- **Minimize approval gates; place them deliberately.** Every human gate has a cost. Let intermediate stages run through if their failures surface at a later gate anyway. Provide an escape hatch for systemic upstream problems detected late.
- **No side effects before approval.** Intermediate work goes to a pending/scratch area. Committed state reflects only approved work, so aborts leave no residue — cleanup falls out of the design.
- **Tilt toward over-inclusion when omissions cost more than false positives.** If missing an item is permanent but rejecting one is cheap, bias toward surfacing too much.

## Staleness & dependencies
- **Every generated artifact records its inputs** (hashes of source files, model identity, parameters). Basis for both staleness detection and audit.
- **Hash raw bytes.** Don't build canonicalization cleverness you don't need — spurious regen is cheap; canonicalization bugs are expensive.
- **Snapshot for approved work; live-check for pending.** Past a gate, recorded inputs are audit-only, not a re-work trigger. Don't retroactively invalidate human decisions.
- **Tier staleness responses.** Pending + stale: refuse, name the fix. Approved + upstream changed: warn, don't block. Fully approved: audit only.

## Schema versioning
- **Version every structured file from day one** with a monotonic integer. No semver, no dates.
- **Each file type versions independently.**
- **Refuse unknown futures ("upgrade the tool"); migrate older versions lazily, one step at a time,** writing the upgraded form back.
- **Schema bumps don't cascade as staleness** through the system.

## UX & errors
- **Every error names the exact command to fix it,** including flags. "Run `X --from=Y`" beats "stale."
- **Every decision point gets a decision.** No skip, no defer. Deferral accumulates bugs silently.
- **Degrade gracefully on missing optional input.** Don't demand what isn't load-bearing. Quality drops, pipeline doesn't break.
- **Interactive by default, flags to skip, auto-detect non-TTY.** Partial aborts save progress, not discard it.
- **Defaults chain from specific to general:** flags → local config → global config → last-used.

## Compounding value
- **Corrections accumulate across sessions** with provenance. The tool gets better as it's used.
- **Promote local→global on explicit command,** tracking first-seen and subsequent sightings.

## Audit trail
- **Data files ARE the audit trail.** No external system (git, etc.) required to answer "when/who/what changed." Every record carries created-at, created-by, edited-at, edited-by, and status history where applicable.
- **Append-only history for anything mutable.** Log events with actor, timestamp, reason — don't overwrite.
- **Make bulk-undo trivial** by tagging everything with the session/batch ID that produced it.

## Scope discipline
- **Defer features until a real trigger fires,** not a hypothetical one. Name the trigger so future-you knows when to revisit.
- **Explicit non-goals** are as valuable as goals. Write them down.