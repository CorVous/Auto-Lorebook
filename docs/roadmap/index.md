# Roadmap

This page is the single source of truth for what's built and what's
planned. Other pages describe the system as specified; check here for
implementation status.

!!! info "Current status"

    Phase 1 substantially landed; the reading pipeline is runnable end
    to end against a real OpenRouter backend (Stage 1a + 1b, gap
    check, approve, targeted re-runs). Remaining Phase 1 polish: live
    end-to-end validation against a real source and tuning the 1a/1b
    prompts based on that run.

    Phase 2 (entity scaffolding) landed: schema-validated entity YAML
    read/write (`auto_lorebook.entity_yaml`), the existing in-memory
    `EntityIndex` migrated onto it, and an `entities` subcommand
    group (`list`, `show`, `new`, `rebuild-index`). Hand-creating
    entities is documented at
    [hand-creating entities](../getting-started/entities.md);
    `entities rebuild-index` is a placeholder until a disk cache
    materialises.

    Phase 3 (planner) landed: Stage 2 LLM planner runs automatically
    after `approve-reading`, writes `pending/<source_id>/plan.yaml`
    with no filesystem side effects (new entities and aliases are
    proposals only), supports multi-target routing per claim, and
    exposes `plans list` / `plans show` for inspection. `replan` is
    deferred to Phase 4.

    Phase 4 extractor landed: Stage 3 runs automatically after the
    planner, parallelised per `PlannedClaim`, with reduced preamble,
    windowed transcript per claim driven by `locator_hint`, mechanical
    fallback to the parent-segment window with `hint_widened` logged,
    claim-group dedup (one LLM call per claim, span copied to every
    sibling target), and post-extraction substring verification.
    Proposals land at `pending/<source_id>/proposals/<proposed_id>.yaml`
    with provisional `proposed_id`s allocated single-threaded before
    fan-out. The terminal review loop, atomic stub creation on first
    approval, alias confirmation, in-memory index refresh,
    per-fact append, edited-text handling, `replan`, and
    `reject-ingest` remain deferred within Phase 4.

## Phase 1: Reading stage

**Goal** — ingest a YouTube URL, gather context, produce a reviewable,
correctable reading via the two-substage pipeline.

**Scope**

- CLI skeleton + config file handling.
- `yt-dlp` subprocess wrapper: transcript + title + duration.
- SRT parser.
- OpenRouter client with configurable model slots (both reading
  substages default to the primary model).
- Source metadata → `sources/<source_id>/info.yaml` including
  `context` block.
- `.wiki-context.yaml` reader (tolerates missing or empty file).
- `.transcription-corrections.yaml` reader (tolerates missing or
  empty file).
- Ingest halt + `generate-reading` command for the context-gathering
  step.
- CLI flags for common context fields (`--session-date`,
  `--perspective`, etc.).
- Prompt preamble assembly from `info.yaml`, `.wiki-context.yaml`,
  corrections, and entity index, with per-substage variation.
- Token-budget check on preamble with component-specific error
  messages.
- Stage 1a (structure): full-coverage segments, per-segment speaker
  assignment, sub-segment overrides, uncertainty flags; mechanical
  validation that timestamps are real, that segments cover the
  transcript without gaps, and that override ranges fall within
  parent segments.
- Mechanical gap check: heuristic pass over `structure.yaml` that
  surfaces long stretches covered only by low-yield-looking segments
  as warnings during review.
- Stage 1b (summarize): per-segment claim extraction with empty
  bullet lists permitted; parallelized across segments; emits
  per-bullet `locator_hint` windows as internal pipeline metadata;
  produces draft `reading.md` by interleaving 1a's segment headers
  with 1b's bullets; post-processes for clickable timestamps.
- Canonical timestamp format (`h:mm:ss`) enforced in writers, lenient
  in readers.
- `name_corrections` frontmatter + rendering.
- `regenerate-reading` with `--from` flag, including per-segment 1b
  regeneration via `--segments`.
- `approve-reading` command.

**Exit criterion** — ingest a real actual-play VOD, fill in context,
run both substages, review the combined reading, approve. The reading
covers the full transcript (no gaps); segments with no claims are
visibly empty rather than absent; at least one off-topic segment is
correctly rendered with an empty bullet list; the mechanical gap check
fires at least once on real content and the warning is actionable.
Total human review time for a two-hour source fits inside the
10–20 min/hour target on a representative session.

## Phase 2: Entity scaffolding (landed)

**Scope**

- Entity YAML schema + directory structure.
- In-memory entity index, built from filesystem scan.
- Commands to list/show entities.
- Ability to hand-create entity stubs that the tool recognizes
  (bootstrapping aid; hand-creation is not the normal path — entities
  are normally created via approved facts).

**Exit criterion** — hand-create a few entities; tool lists them and
exposes them to the next stage as an entity index. ✓

`entities rename` and `wiki list` / `wiki show` were spec'd alongside
this phase but are deferred — `rename` to whenever a real need surfaces,
`wiki *` to Phase 5/6 where they overlap with summary rendering.

## Phase 3: Planner (landed)

**Scope**

- Stage 2 planner with trimmed MVP schema.
- Plan written to pending state as intermediate artifact.
- **No filesystem side effects from the planner.** New entities
  recorded on the plan only; no stub creation.
- Alias suggestions recorded on the plan for per-proposal
  confirmation in fact review.
- Multi-target routing: planned claims carry a `targets` list,
  allowing a single claim to route to multiple entities with
  per-target section and rationale.
- `plans list` and `plans show` inspection commands.

**Exit criterion** — ingest a source, review reading, planner runs
automatically and produces a plan. New entities are _proposals on the
plan_, not files in the wiki. Aliases are proposed, not yet written.
At least one planned claim in a representative session routes to
multiple targets. ✓

`replan` was spec'd alongside this phase but is deferred to Phase 4
where the extractor + review loop give it something to discard.

## Phase 4: Extractor + review

**Scope**

- Stage 3 extractor with parallelized proposals, runs automatically
  after planner.
- Reduced preamble for extractor.
- Windowed transcript input per proposal, driven by `locator_hint`
  from the plan; fallback to parent-segment window on
  substring-verification miss, with `hint_widened` logged on the
  proposal.
- Claim-group deduplication: extractor runs once per
  `claim_group_id` and copies the located span to all sibling
  proposals.
- Post-extraction mechanical verification that `raw_transcript_span`
  is a real substring.
- Terminal review loop with approve / edit / reject actions, with
  claim-group siblings shown contiguously.
- **Atomic entity stub creation on first approval for a proposed
  new entity**, with `created_by_ingest` set to the current ingest.
- Routing metadata surfaced per-proposal (matched_via,
  new-vs-existing, proposed aliases, "created earlier this session"
  for in-review creations, sibling targets for multi-target claims).
- Inline alias-confirmation sub-prompt; aliases merged into stub at
  creation or appended on update.
- In-memory entity index refresh after each approval.
- Per-fact approval → fact appended to entity YAML, carrying
  `claim_group_id` when applicable.
- Handling for edited text (edits scoped to the proposal being
  edited, not propagated to claim-group siblings).
- `replan` command (discard unreviewed proposals, re-run planner +
  extractor; already-approved entities visible to the new plan as
  existing).
- `reject-ingest` command (removes facts and any entity stubs left
  empty as a result).

**Exit criterion** — end-to-end ingest produces approved facts in
entity YAMLs, with confirmed aliases and routing decisions visible at
review time. Rejecting all proposals for a proposed new entity leaves
no trace in the wiki. A multi-target claim produces sibling facts on
multiple entities sharing a `claim_group_id`; independently rejecting
one sibling leaves the others intact.

## Phase 5: Summarizer

**Scope**

- Stage 4 summarizer with status-aware rendering.
- Section normalization (case-insensitive grouping of free-text
  section names).
- Integration with fact approval flow (batched regeneration at
  session end).
- `wiki rebuild` command to regenerate all summaries from scratch.
- `promote-correction` command (with `first_seen_in` / `also_seen_in`
  tracking).

**Exit criterion** — entity `.md` files render cleanly, citations
link to sources, status sections correctly grouped.

## Phase 6: Web UI (reader)

**Scope**

- Minimal HTTP server.
- Markdown rendering with working footnote links.
- Sidebar navigation by category.
- Clickable cross-links and citations.

**Exit criterion** — wiki browsable in a browser.

## Phase 7: Web UI (reviewer)

**Scope**

- Reading review with optional substage views (structure and claims
  surfaced separately when helpful; unified markdown view as default;
  coverage-gap warnings highlighted).
- Fact review (side-by-side raw vs. proposed, inline edit, one-click
  playback, inline alias confirmation, new-entity-on-approval
  behavior preserved).
- Plan inspection view (read-only; shows routing decisions and their
  rationale for debugging).

**Exit criterion** — terminal review no longer needed for daily use.

## Deferred / out of scope

Not in MVP. Add only when proven necessary:

- Plain-text and markdown source types as first-class. (MVP: only
  YouTube; plain text via file path works but with minimal
  handling.)
- **Retrieval-based entity context.** When a wiki grows large enough
  that the full entity index exceeds the preamble token budget, the
  replacement is embedding-based retrieval of entities plausibly
  relevant to the current source (matched against transcript text),
  not truncation. Trigger: token-budget failure in preamble
  assembly. Until that trigger fires on a real wiki, the full-index
  approach is simpler and higher-quality.
- Fuzzy / phonetic entity matching beyond entity-index lookup.
- Status-change proposals (contradiction detection).
- Automatic alias promotion from mention frequency.
- Dedicated merge/split commands. Handle by editing YAML by hand and
  setting `superseded_by` if needed.
- Per-category controlled section vocabularies in
  `.wiki-context.yaml`. MVP uses case-insensitive normalization of
  free-text sections.
- Configuration UI beyond a config file.
- Multi-user collaboration / locking.
- Non-English content.
- Sources beyond YouTube + text files.
