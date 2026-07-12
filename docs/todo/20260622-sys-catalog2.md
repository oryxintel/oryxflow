# Data-trust / semantic catalog for task outputs — v2 (rebased on the event stream)

> Supersedes `20260622-sys-catalog.md` (v1). The **goal, semantics model, enrichment design,
> capture/profile logic, MCP/plugin surface, and docs/tests** of v1 stand unchanged — read v1
> for those (it stays in `docs/todo/` as the design record; §§2–4, 7–13 there remain the
> executable spec for everything not overridden below). What changes here is the **storage
> substrate**: the catalog no longer owns a private SQLAlchemy database — it rides the
> unified append-only event stream built by `20260712-engine-code-invalidation.md`.
> Execute v4 first; this plan assumes `oryxflow/events.py` exists.

## Context (what changed since v1)

v1 was planned before the code-invalidation/run-history work
(`20260712-engine-code-invalidation.md`). Reviewing both exposed that they specified **two
overlapping logging subsystems**: v1 catalog wrote artifact descriptors to a SQLAlchemy
SQLite DB in `data/`, with its own `run_id` plumbing, duration stamping, and lineage tables;
the invalidation plan wrote run records to its own store in `.oryxflow/`. Same events, two
stores, two schemas, conflicting locations, two migration stories. v2 unifies them: **one
event stream, one index, one `run_id`** — the catalog becomes the artifact-level event
producer + a reader/enrichment layer on the shared substrate.

### Design decisions (deltas from v1 — v1 decisions not listed here still hold)

1. **Storage: events, not a private DB.** *(Partially reverses v1 decision 4, which rejected
   JSON-lines — that rejection predated the shared substrate.)* `record_save`/`record_lineage`
   emit `artifact_saved` / `lineage` events via `events.append(...)` into
   `.oryxflow/events-YYYYMM.jsonl`, sharing the build's `run_id` (v4 generates it; the v1
   `run_begin`/`run_end`/`_current` context-stack machinery is **deleted** — v4's build()
   owns run context). Benefits inherited from the substrate: append-only immutability,
   sync-ready envelope (id/ts/project_id/machine/user), no-migrations index rebuild,
   `tail`/`grep`/`jq` agent access.

2. **Drop the SQLAlchemy dependency; extend the v4 stdlib-sqlite index instead.** v1 chose
   SQLAlchemy for "any DB via URL"; with the event stream as source of truth, any-DB
   flexibility is the hosted tier's concern, not the local library's. The catalog adds
   `artifacts`, `lineage`, and `semantics` tables to `oryxflow/events.py`'s rebuildable
   index (same `PRAGMA user_version` stamp — schema change → rebuild from JSONL, no
   migrations). The `oryxflow[catalog]` extra shrinks or disappears (pandas is already
   required; nothing else is needed). v1's readers (`catalog.list/describe/history/lineage/
   export`) reimplement against the index with the same signatures.

3. **Location: `.oryxflow/`, not `data/`.** *(Reverses v1's `data/.oryxflow-catalog.db`.)*
   Descriptors of artifacts are provenance — they must survive `rm -rf data/` (the
   correctness-critical state JSON stays in `data/` per v4 decision 4; the catalog holds no
   correctness state).

4. **Enrichment writes events too (corrections-as-events).** v1 UPDATEd
   `field_semantics`/`review_status` in place. v2: `enrich()` emits a `semantics_generated`
   event keyed by `(code_hash, schema_hash)` carrying `field_semantics`, `description`,
   `model`, `review_status`; the index derives current semantics (latest event per key).
   The determinism cache (`_cached_semantics`) becomes a lookup of the latest
   `semantics_generated` for the key — identical code+schema reuse prior enrichment
   unchanged. Nothing in the log is ever mutated.

5. **Opt-in stays, and the split is principled** (v4 decision 9): run-level events are
   always on (microseconds); artifact capture (profile/sample of possibly-large DataFrames)
   costs real time → stays behind `enable_catalog()` / `disable_catalog()` exactly as v1
   specified (never breaks a run; every path try/except-wrapped). `enable_catalog()` no
   longer takes a URL — it just flips capture on (`settings.catalog_enabled`); a passed
   `url` is accepted-and-ignored with a deprecation debug for plan continuity.

6. **`code_hash` aligns with v4's hashing.** v1 hashed raw `inspect.getsource(run)`. v2 uses
   the AST-normalized hash of the task's defining module from `oryxflow/codehash.py`
   (comments/docstrings don't churn the semantics cache), recorded alongside `schema_hash`
   in `artifact_saved` and used as the semantics cache key.

### Event payloads added to the v4 stream

| type | payload (v1) |
|---|---|
| `artifact_saved` | task_id, family, persist_key, path, format, params (significant), code_hash, schema_hash, shape_rows/cols, size_bytes, columns, dtypes, profile, sample, run_source, oryxflow_version |
| `lineage` | downstream_task_id, upstream_task_id, upstream_family, source_ref (raw-source citation at external/root deps) |
| `semantics_generated` | code_hash, schema_hash, description, field_semantics, model, review_status |

(Field content and the `_profile` evidence-bundle logic: exactly v1 §2–3 — structural
profile, bounded sample, nulls/ranges, `schema_hash` over columns+dtypes.)

## Implementation

1. **`oryxflow/settings.py`** — keep v1's `catalog_enabled` / `catalog_sample_rows` /
   `catalog_profile_max_rows`; drop `catalog_url`.
2. **`oryxflow/catalog.py`** — as v1 §2–3 minus the engine/schema/context machinery:
   `enable_catalog()`/`disable_catalog()` flip the flag; `_profile` unchanged;
   `record_artifact`/`record_save` build the same row dict but end in
   `events.append('artifact_saved', row, run_id=<current build's>)` (v4's build exposes the
   active run_id — simplest: `events.current_run_id()` set/cleared by build's try/finally).
   `record_lineage` likewise emits `lineage` events (v1 §4 logic, minus run stack).
   Delete `run_begin/run_end/set_current_task/stamp_duration` (duration now lives on v4's
   `task_ran` event).
3. **`oryxflow/events.py`** — add the three tables to the index schema + their rebuild
   handlers; bump `SCHEMA_V`.
4. **`oryxflow/catalog.py` readers** — v1 §7 signatures (`catalog.list/describe/history/
   lineage/export`) querying the shared index (`describe` joins latest `semantics_generated`
   by the artifact's `(code_hash, schema_hash)`).
5. **Enrichment seam** — v1 §8 (`set_enricher`/`enrich`), emitting `semantics_generated`
   events per decision 4.
6. **Capture hook** — v1 §5 unchanged: one call in `tasks/__init__.py:save()` after the
   debug log (`_catalog.record_save(self, data, from_list)`), no-op when disabled.
7. **MCP tools, plugin skill, docs, tests** — the MCP surface is specified in
   `20260712-mcp.md` (an `oryxflow/mcp.py` module in this repo — v1 §10's separate
   `oryxflow-mcp` repo never existed); plugin skill per v1 §11; docs/tests per v1 §§12–13,
   with tests
   adjusted: assert events in the JSONL + rows in the shared index instead of a private DB;
   add: index rebuild reproduces catalog tables from events; `enable_catalog` off → no
   `artifact_saved` events while run-level events still flow.

## Files modified

- `oryxflow/catalog.py` — **new** (capture + readers + enrichment seam, on the event
  substrate).
- `oryxflow/events.py` — catalog tables in the index; `current_run_id()`.
- `oryxflow/settings.py` — catalog flags (no URL).
- `oryxflow/tasks/__init__.py` — the one-line `save()` hook.
- `oryxflow/__init__.py` — re-export `enable_catalog`/`disable_catalog`/`catalog`.
- `oryxflow-mcp`, `oryxflow-claude-plugin` — v1 §§10–11 unchanged (separate repos).
- `docs/source/catalog.rst`, `tests/test_catalog.py` — per v1 §§12–13, adjusted as above.

## Verification

v1's verification stands (73-baseline, enable-and-run assertions, lineage, determinism
cache, never-breaks-a-run, readers, no-DB-when-disabled) with the storage swapped: the
smoke test asserts `artifact_saved`/`lineage` lines in `.oryxflow/events-*.jsonl`, catalog
readers answering from the shared index, and a deleted `index.db` rebuilding the catalog
tables from the stream.
