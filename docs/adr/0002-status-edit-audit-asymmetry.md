# No `status_source` parallel to `text_source`

Approved facts record `text_source` (the planner's original text) and `edited_by_human` whenever the reviewer edits `text`. Status edits get no equivalent — `status_history` records only the reviewer's final value; the planner's `proposed_status` is dropped.

We keep this asymmetric. Text edits track corrections to a literal quotation that can be objectively wrong (mishearings, typos, ASR errors); preserving the original is part of the claim's truthfulness story. Status is a reviewer judgment call — the planner's guess is a model artefact with no lasting truth value, the same reason `plan.yaml` itself isn't archived after extraction.

If we ever need "how often does the reviewer override the planner's status?" for model-quality analysis, add it then. Until then, recording it durably suggests it has lasting value, which it doesn't.
