# TUI Architecture

The `process` command runs the full ingest → reading → review pipeline inside a
[Textual](https://textual.textualize.io/) TUI. This page covers the screen graph,
concurrency model, reviewer wiring, and the gate-2 keymap contract.

## Entry point

```
auto-lorebook process <url-or-path>
auto-lorebook process --source-id <id>
```

`commands/process.py` derives the source ID, detects the resume stage via
`tui.resume.detect_stage`, builds a `PipelineState`, and hands off to
`tui.app.ProcessApp`.

## Source module layout

```
src/auto_lorebook/tui/
  __init__.py
  app.py          ProcessApp(App): top-level app; owns the stage state machine
  state.py        PipelineState dataclass + Stage enum
  resume.py       detect_stage(source_id, wiki_repo) → Stage
  reviewer.py     TuiReviewer: review.Reviewer impl driven by the review screen
  screens/
    welcome.py    URL/path entry (skipped when --url/--source-id given)
    context.py    info.yaml editor (wraps interactive.gather_context)
    progress.py   log/spinner panel for non-interactive stages
    reading.py    gate-1: reading.md viewer + a/e/r/u/q controls
    review.py     gate-2: bundle viewer + a/e/r/p/t/u controls (TuiReviewer host)
  widgets/
    diff_view.py  syntax-highlighted markdown viewer
    bundle_view.py render BundleView (proposals + route checklist)
```

## Stage state machine

`tui/resume.py::detect_stage` returns the first unfinished stage by probing
on-disk artifacts:

| Stage | Done-check |
|---|---|
| `INGEST` | `wiki/sources/<id>/transcript.*` exists |
| `CONTEXT` | `pending/<id>/context.set` tombstone exists |
| `READING_GEN` | `pending_reading_path(id)` exists |
| `READING_GATE` | pending reading frontmatter `reading_status == "approved"` |
| `PLAN` | `pending_plan_path(id)` exists |
| `EXTRACT` | `pending_proposals_dir(id).exists()` |
| `REVIEW_GATE` | `pending/<id>/review.done` tombstone exists |
| `DONE` | same tombstone |

Two tombstones (`context.set`, `review.done`) are written by the orchestrator, not
by the reused engines, so existing per-stage commands are unaffected.

## Concurrency model

The pipeline (`reading_pipeline.*`, `review.run`, `ytdlp.*`, `openrouter.*`) is
purely synchronous and blocking — `openrouter.py` uses stdlib `urllib.request`
and there is no async code in `src/auto_lorebook/` outside of `tui/`.

Textual is asyncio-native and must keep its event loop responsive. All blocking
pipeline calls run inside `App.run_worker(thread=True)`.

Screen ↔ `TuiReviewer` rendezvous (the only place the loop and the worker need
to communicate mid-call) uses `queue.Queue` + `threading.Event`:

- `pending: queue.Queue` — single-slot queue; worker blocks on `get()`.
- `cancel_event: threading.Event` — set by the app on quit/Ctrl-C.

The queue and event are created on the loop side; the worker reaches them via
`App.call_from_thread(screen.show_bundle, view)` to schedule work on the loop.

## Reviewer wiring (gate 2)

`TuiReviewer` implements `review.Reviewer` and runs inside the worker thread
hosting `review.run(...)`.

```
Worker thread              |  Asyncio loop (Textual)
                           |
decide_bundle(view):       |
  call_from_thread(        |
    screen.show_bundle,    |  → show_bundle(view) runs
    view                   |     populates ReviewScreen
  )                        |
  pending.get()   ←--------+--  user presses [a] → pending.put(decision)
  check cancel_event       |
  return decision          |
```

`by_label = "human-review"` — same label as the CLI reviewer, so
`status_history` entries are interchangeable between the two interfaces.

**Cancel protocol.** Textual intercepts SIGINT on the main loop and does NOT
propagate it into worker threads as a Python exception. `TuiReviewer` checks
`cancel_event.is_set()` after every `pending.get()` and raises
`KeyboardInterrupt` itself, so `review.run`'s existing cancel path records
`ReviewResult.remaining` before re-raising. The KI never escapes the worker
thread (Textual's `Worker.error` would tear down the app).

## Gate-2 keymap

The review screen mirrors `commands/review.py:148-184`.

| Key | Action |
|---|---|
| `a` | Approve → emit `BundleDecision` with `ApproveDecision` (or `bundle_edits`). Toast if zero routes selected. |
| `r` | Reject → emit `BundleDecision(RejectDecision, selected_indices=())`. |
| `e` | Edit → modal for text/status/status_reason; result merged into `bundle_edits`. |
| `t` | Targets → toggle/per-target-edit; mutates `selected` and `overrides`. |
| `p` | Play → display URL or open via `App.suspend()`. |
| `u` | **Undo** → `bundle_edits = None`; `overrides.clear()`; all `selected[i] = True`; re-render. |
| `q` / Ctrl-C | Set `cancel_event`; dismiss screen. |

### Undo scope

`[u]ndo` resets state for the **on-screen bundle only**. Once a `BundleDecision`
is emitted to the queue, undo cannot recall it. This matches the CLI undo
behaviour (`commands/review.py:173-183`).

## Context screen (gate 0.5)

Pre-fills each `Input` from the existing `info.yaml`, falling back through the
same chain that `gather_context` uses (existing → `last_context` → wiki
default). On submit calls `interactive.gather_context(..., interactive=False)`
with the form values as flags, then writes `info.yaml` and drops the
`context.set` tombstone.

Per-field validators reference `interactive.DATE_RE` and `interactive.SOURCE_NATURES`
(promoted from private names) — single source of truth, no copy.
