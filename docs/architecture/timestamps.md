# Timestamps

Two distinct kinds of timestamps appear in the system. They do not get
confused.

## Source locators

Positions inside a transcript. Used for locators in fact YAMLs and in
reading sections.

Canonical format: `h:mm:ss`. Ranges: `h:mm:ss-h:mm:ss`.

For sources under an hour, the leading `0:` is still written for
consistency (`0:04:32`, not `4:32`), except in user-facing display
where the leading zero hour may be elided for readability. Parsers
accept either form; writers produce the canonical form.

Examples:

```yaml
locator: "0:04:32-0:04:41"
```

```markdown
## [[0:04:30-0:08:00]](https://youtube.com/watch?v=abc123&t=270) Founding of Aldara
```

## Wall-clock event timestamps

When something happened in the real world: the tool wrote a file, the
human approved a fact, an ingest was rejected.

Format: RFC 3339 with explicit timezone offset — either `Z` for UTC or
`±HH:MM` for a local offset.

Examples: `2026-01-16T18:22:47Z`, `2026-04-20T09:15:42-07:00`.

Fields using this format: `fetched_at`, `ingested_at`, `generated_at`,
`planned_at`, `approved_at`, `created_at`, `updated_at`, `edited_at`,
`promoted_at`, `added_at`, and `at` inside `status_history` entries.

The tool writes UTC by default. A future flag may let users opt into
local-offset writes. Parsers accept any valid RFC 3339 string and
normalize to UTC for comparison.

## `session_date` is exempt

`session_date` represents an in-world or calendar-day concept — which
session did this claim first enter canon — not a wall-clock event. It
stays as a plain `YYYY-MM-DD` date and is the only date-only field in
the system.

Example:

```yaml
session_date: 2026-01-15
```

## Clickable timestamps in readings

All source-locator timestamps in rendered `reading.md` become markdown
links. Clicking opens the source at that moment (e.g., YouTube with
`&t=` query param). The LLM emits plain `[4:32]` text; the tool
post-processes to add link URLs. Post-processing applies to the final
assembled reading only, not to intermediate YAML artifacts.

On save, the tool re-syncs display-vs-URL seconds if the human edited
a timestamp.
