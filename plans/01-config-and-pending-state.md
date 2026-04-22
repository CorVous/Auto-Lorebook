# Plan 01 — Config & pending state scaffolding

**Prerequisite:** none. This is the foundation.

## Goal

Land the configuration model and the tool-state directory layout so
every subsequent plan has a place to read from and write to. Nothing
LLM-related yet; no network.

## In scope

- `config.yaml` schema + loader (model slots, wiki repo path,
  token-budget fraction, pending root).
- Resolver that finds `~/.auto-lorebook/config.yaml` with an env var
  override (`AUTO_LOREBOOK_HOME`).
- `config.yaml` bootstrap: if absent, write a sensible default on
  first run and print the path.
- `pending/<ingest_id>/` directory helpers (create, list, resolve,
  discard) — no subdirectory contents yet.
- Ingest ID minting: `ingest-YYYY-MM-DD-<short>` where `<short>` is a
  short deterministic suffix within a day (e.g. `a`, `b`, ...).
- Wiki repo root resolver (from config, with CLI `--wiki` override).

## Out of scope

- Any `sources/`, `info.yaml`, or SRT handling (Plan 02).
- Any LLM client or preamble (Plan 04).
- Schema versioning migrations — record `schema_version: 1` only.

## TDD plan

### Red tests to write first

- `test_config_defaults_written_on_first_run` — fresh `AUTO_LOREBOOK_HOME`
  → loader writes default `config.yaml`; subsequent load returns the
  same values.
- `test_config_round_trip` — write a `config.yaml` with custom model
  slots + token budget → loader returns the same structured config.
- `test_config_rejects_unknown_keys` — unknown top-level key raises a
  clear error naming the key.
- `test_wiki_path_override_from_cli` — `--wiki <path>` wins over
  config value.
- `test_ingest_id_uniqueness_within_day` — mint two IDs on the same
  day → suffixes differ; both sort lexicographically by creation
  order.
- `test_pending_create_and_discard` — create ingest dir → it exists;
  discard → it is gone and siblings untouched.

### Implementation sketch

- `auto_lorebook/config.py`
  - `@dataclass(frozen=True) Config` with fields: `wiki_repo: Path`,
    `pending_root: Path`, `models: ModelSlots`, `token_budget_fraction: float`.
  - `load_config(home: Path | None = None) -> Config`.
  - `write_default_config(path: Path) -> None`.
- `auto_lorebook/state.py`
  - `pending_dir_for(ingest_id: str) -> Path`.
  - `mint_ingest_id(today: date, existing: Iterable[str]) -> str`.
  - `discard_ingest(ingest_id: str) -> None` (safe no-op if missing).

### Docs touched

- `docs/reference/file-formats.md` — add `config.yaml` schema snippet
  (minimum fields, defaults).
- `docs/architecture/repository-layout.md` — no change expected, but
  verify the `pending/` layout matches what Plan 01 creates.

## Integration test (plan exit gate)

`tests/integration/test_plan_01_config.py`:

1. Point `AUTO_LOREBOOK_HOME` at `tmp_path`.
2. Invoke the CLI entry point (`auto-lorebook version` is fine — any
   command that triggers config load).
3. Assert: `tmp_path/config.yaml` exists, parses, and has the default
   model slot values documented in `docs/reference/file-formats.md`.
4. Mint three ingest IDs on the same simulated day; assert all are
   unique and all pending dirs exist.
5. Discard one; assert only it is gone.

Gate: integration test green **and** `uv run mkdocs build --strict`
green.
