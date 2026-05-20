# Plan is canonical for review; orphan proposals are an error

The Stage-3 extractor writes one proposal file per claim group, with N
targets inside. Review walks the plan to look up each proposal by
`claim_group_id`. Today `sorted_proposals` silently appended any
proposal whose plan key didn't match — covering up drift between
`plan.yaml` and `pending/<ingest_id>/proposals/`.

We pick the plan as canonical: at the start of `run()`, every target
within each DB proposal must correspond to a `(claim_group_id, entity)`
pair in the plan. Missing pairs are fine (Ctrl-C resume after partial
approval). Extra pairs (orphans) raise `ReviewError` and direct the
user to `replan`.

Rationale: orphans only realistically arise from manual proposal edits or a partial replan that didn't clean up. Silent recovery hides those bugs and reviews bundles the planner never sanctioned, which `replan` then can't reroute (it discards based on plan membership). Treating drift as an error makes the invariant visible and `replan` the single recovery path.
