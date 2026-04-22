# Plan 02 — Source ingestion

**Prerequisite:** Plan 01.

## Goal

Given a YouTube URL, SRT path, or plain text path, produce
`sources/<source_id>/` containing `transcript.en.srt` (or raw text)
and a minimum `info.yaml` (no context block yet). `yt-dlp` runs as a
subprocess and is mocked in tests.

## In scope

- `yt-dlp` subprocess wrapper that captures: transcript (SRT), title,
  duration, caption type (manual vs auto). Prefers manual captions.
- Caption-type user messaging (the ✓ / ⚠ blocks from
  `docs/pipeline/context.md`).
- SRT parser sufficient for downstream timestamp lookup (start/end +
  cue text; tolerates blank lines and BOM).
- Source ID derivation:
  - `yt-<video_id>` from YouTube URL.
  - `srt-<short_content_hash>` (first 10 hex of SHA-256) for SRT files.
  - `txt-<short_content_hash>` for text/markdown files.
  - `--source-id` CLI override.
- `sources/<source_id>/` layout writer:
  - `transcript.en.srt` (or raw `.txt` / `.md` passthrough).
  - `info.yaml` with: `schema_version`, `source_id`, `source_type`,
    `source_url`, `title`, `duration_seconds`, `caption_type`,
    `fetched_at`, `session_date: null`. **No `context` block yet.**
- Duplicate-source detection: re-ingesting an existing `source_id`
  refuses with a clear message.
- `auto-lorebook ingest <url-or-path>` wiring.

## Out of scope

- Context block (`info.yaml.context`) — Plan 03.
- Interactive prompts — Plan 03.
- `--session-date`, `--perspective`, etc. — Plan 03.
- Reading generation — Plans 04–06.

## TDD plan

### Red tests to write first

- `test_srt_parser_basic` — minimal 3-cue SRT parses into expected
  structured form.
- `test_srt_parser_tolerates_bom_and_blank_lines`.
- `test_source_id_youtube_from_url` — `https://youtube.com/watch?v=abc123` →
  `yt-abc123`; short URL + shorts URL variants cover the same ID.
- `test_source_id_srt_hashes_content` — two copies of the same bytes
  at different paths → same ID; a whitespace change → different ID.
- `test_source_id_override_flag`.
- `test_ingest_duplicate_refuses` — second ingest of the same URL
  exits non-zero with a named reason.
- `test_info_yaml_shape_after_ingest` — only base fields present; no
  `context` key yet (Plan 03 adds it).
- `test_caption_type_messaging` — fake `yt-dlp` output for manual vs
  auto-generated → correct user-facing line.

### Implementation sketch

- `auto_lorebook/sources/ytdlp.py` — thin subprocess wrapper. Inject
  the subprocess runner so tests can swap it.
- `auto_lorebook/sources/srt.py` — parser; returns a list of cues
  with `start`, `end`, `text`.
- `auto_lorebook/sources/ids.py` — ID derivation + hashing helpers.
- `auto_lorebook/sources/store.py` — `ingest_source()` orchestrator
  that writes the directory and `info.yaml`.
- `auto_lorebook/commands/ingest.py` — CLI wiring (flags limited to
  `--source-url`, `--source-id` in this plan).

### Docs touched

- `docs/pipeline/context.md` — verify source ID and yt-dlp behavior
  sections match implementation; adjust if wording drifted.
- `docs/reference/file-formats.md` — `info.yaml` minimum-shape
  snippet; note `context:` is added in Plan 03.

## Integration test (plan exit gate)

`tests/integration/test_plan_02_ingest.py`:

1. **YouTube path (mocked):** patch the yt-dlp runner to return a
   canned payload (recorded fixture under
   `tests/support/fixtures/ytdlp/aether_s3e14.json` + matching
   `.srt`). Run `auto-lorebook ingest https://youtube.com/watch?v=abc123`
   pointed at `tmp_path` as wiki root.
   Assert: `sources/yt-abc123/transcript.en.srt` and
   `sources/yt-abc123/info.yaml` exist; `info.yaml` has the expected
   base fields; no `context` key; caption-type message printed.
2. **SRT path:** ingest a local `.srt` with `--source-url` supplied.
   Assert: `sources/srt-<hash>/` shape correct.
3. **Duplicate:** re-run case 1; assert exits non-zero with the
   duplicate message.

Gate: integration test green, no real network used, `mkdocs build
--strict` green.
