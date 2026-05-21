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

    Phase 3 (planner) landed: Stage 2 LLM planner (`auto-lorebook plan
    <id>`) writes `pending/<source_id>/plan.yaml` with no filesystem
    side effects (new entities and aliases are proposals only), supports
    multi-target routing per claim, and exposes `plans list` /
    `plans show` for inspection.

    Phase 4 extractor landed: Stage 3 runs automatically after the
    planner, parallelised per `PlannedClaim`, with reduced preamble,
    windowed transcript per claim driven by `locator_hint`, mechanical
    fallback to the parent-segment window with `hint_widened` logged,
    claim-group dedup (one LLM call per claim, span copied to every
    sibling target), and post-extraction substring verification.
    Proposals land at `pending/<source_id>/proposals/<proposed_id>.yaml`
    with provisional `proposed_id`s allocated single-threaded before
    fan-out.

    Phase 4 review loop also landed: `auto-lorebook review <id>` walks
    each pending claim group as **one bundle** with a route checklist,
    prompts approve / edit / reject / play / targets, and on approval
    atomically creates the entity stub (for proposed-new entities) or
    appends a fact to the existing entity YAML for every checked
    route. Bundle-level text edits propagate to all checked siblings;
    per-target `section` / `speaker` overrides live in the `[t]argets`
    sub-prompt. Inline alias-confirmation sub-prompts merge
    planner-suggested aliases as `stub-creation` (first-approval
    batch) or `alias-confirmation` (later additions). The in-memory
    entity index refreshes after each approval so siblings created
    earlier in the same session resolve as existing. On resume, the
    engine seeds its alias-dedup set from on-disk aliases tagged with
    the current ingest, so already-confirmed aliases are not
    re-prompted after Ctrl-C. `--auto-approve` provides non-interactive
    bulk approval (and explicitly *declines* alias suggestions) for
    CI. Ctrl-C leaves untouched proposal files in place so the next
    invocation resumes.

    Phase 4 closeout landed: `auto-lorebook replan <id>` discards
    unreviewed proposals and re-runs planner + extractor against the
    wiki's current entity state (so stubs created by earlier
    approvals appear as existing); already-approved facts are
    untouched. `auto-lorebook reject-ingest <id>` removes every fact
    and alias tagged with the ingest, deletes empty stubs the ingest
    itself created, and clears `pending/<id>/plan.yaml` and
    `proposals/`. `<wiki>/sources/<id>/` and `pending/<id>/reading/`
    are left untouched so a follow-up `regenerate-reading` /
    `approve-reading` cleanly redoes the pipeline. **Phase 4 is
    fully landed.**

    Pipeline ergonomics (#59) landed: `auto-lorebook run <URL-or-id>`
    is the single entry point for the full pipeline. It detects which
    stage is next, drives the source forward to completion, and stops
    at each human gate (reading review, fact review). Stages already
    complete are skipped with a notice. In a non-interactive shell,
    `--yes` passes the reading gate and `--auto-approve` passes the
    fact-review gate; without both flags, `run` refuses to proceed
    past a gate unattended. The planner runs between the two gates
    without its own gate; its failure modes surface during fact review,
    with `replan` as the escape hatch.

    Phase 5 slices 1 and 5 landed: Stage 4 LLM-prose summarizer and
    `auto-lorebook wiki rebuild`. Entity `.md` files are now generated
    with LLM prose, status-grouped facts, and per-fact footnote
    citations. Review sessions batch page generation for all touched
    entities on completion. `wiki rebuild` regenerates all pages from
    scratch and removes orphan `.md` files with no matching entity.

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

**Exit criterion** — ingest a source, review reading, run
`auto-lorebook plan <id>`, and confirm a plan is produced. New entities
are _proposals on the plan_, not files in the wiki. Aliases are
proposed, not yet written. At least one planned claim in a
representative session routes to multiple targets. ✓

`replan` was spec'd alongside this phase but landed in Phase 4
once the extractor + review loop gave it something to discard.

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
- Terminal review loop with approve / edit / reject / targets actions
  driven per **bundle**: targets sharing a `claim_group_id` render
  as one screen with a route checklist.
- **Atomic entity stub creation on first approval for a proposed
  new entity**, with `created_by_ingest` set to the current ingest.
- Routing metadata surfaced per-route inside the bundle (matched_via,
  new-vs-existing, proposed aliases, "created earlier this session"
  for in-review creations).
- Inline alias-confirmation sub-prompt for each checked route;
  aliases merged into stub at creation or appended on update; the
  engine seeds its alias-dedup set from on-disk records on resume so
  Ctrl-C resumes don't re-prompt.
- In-memory entity index refresh after each approval.
- Per-route approval → fact appended to entity YAML, carrying
  `claim_group_id`. Bundle-level text edits propagate across all
  checked siblings; per-target section / speaker overrides apply
  only to their route.
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

**Slice 1 landed:** Stage 4 LLM-prose summarizer (`stage4.py`) and
batched page-step orchestrator (`page_step.py`). Entities with approved
facts get LLM-generated prose plus `## Facts` grouped by epistemic
status, per-fact footnote citations with timestamps, and `## References`.
Zero-fact entities get a mechanical stub with no LLM call. Review
session batches regeneration for all touched entities at completion;
Ctrl-C leaves no partial writes. `models.summarizer` config slot added
(falls back to `models.primary`).

**Slice 5 landed:** `auto-lorebook wiki rebuild` regenerates every
entity page from scratch (prose + linked-entity propagation) and
reconciles the filesystem against the DB — deletes any `.md` file in
the entity-category subdirectories with no matching entity. Recovers
from corruption, crashed page steps, or prompt changes. Staleness-skip
(regenerate only entities whose facts changed since last build) remains
future work.

**Remaining scope**

- Section normalization (case-insensitive grouping of free-text
  section names).
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
