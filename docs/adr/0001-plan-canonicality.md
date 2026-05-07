# Plan is canonical for review; orphan proposals are an error

The Stage-3 extractor writes one proposal file per `(claim_group_id, target_entity)` route in the plan, and Review walks the plan to look those up. Today `sorted_proposals` silently appends any proposal whose plan key didn't match — covering up drift between `plan.yaml` and `pending/<ingest_id>/proposals/`.

We pick the plan as canonical: at the start of `run()`, the on-disk proposal set must be a subset of the plan's `(claim_group_id, target_entity)` keys. Missing keys are fine (Ctrl-C resume after partial approval). Extra keys (orphans) raise `ReviewError` and direct the user to `replan`.

Rationale: orphans only realistically arise from manual proposal edits or a partial replan that didn't clean up. Silent recovery hides those bugs and reviews bundles the planner never sanctioned, which `replan` then can't reroute (it discards based on plan membership). Treating drift as an error makes the invariant visible and `replan` the single recovery path.
