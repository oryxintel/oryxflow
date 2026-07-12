# Code-aware invalidation + event log

The final (v4) revision of this plan; the superseded v1–v3 drafts were deleted before this
was committed. v4 keeps v3's record-based
invalidation core and replaces its history subsystem with a **unified append-only event
stream** shared with the data catalog (companion plan: `20260622-sys-catalog2.md`, which
rides the substrate built here). v3→v4 deltas: AST-normalized hashing, `accept_code()`,
JSONL-primary storage with a disposable SQLite index, sync-ready event envelope,
`RunResult` enrichment, task-log capture.

## Context

oryxflow caches a task's output and skips the task when the output exists. Cache identity is
**parameters only** (`core.py:81` `task_id_str`); `complete()` is "do my output files exist?"
(`core.py:290` + dependency cascade `tasks/__init__.py:92`). Nothing about **code** enters
identity, and nothing records **what ran, when, or why**. Two problems:

1. **Correctness** — edit a task's `run()` (or a helper it calls) with unchanged params and
   the stale cached output is silently reused; on a chain, the pipeline runs green with wrong
   numbers. Users fight it with manual `reset()` — reliance on memory, the exact failure mode
   to remove (worst for AI agents, whose recall degrades across a session).
2. **Provenance / memory** — "why was this file created", "did I already run X for Houston",
   "what did the last session execute" are answered by guessing. Between sessions an agent's
   only persistence is code + hand-curated notes.

Design principles (from agent-user feedback, confirmed):

- **Correctness must not depend on an act of memory.** The author *will* forget to bump a
  version; the system's job is to catch the forgetting — a warning at the decision point and
  a log that makes the miss visible retroactively. Design against **false green**: where the
  system can't be sure code is unchanged it warns or says so, never implies "unchanged".
- **One idiom, not two:** bumping `code_version` subsumes the edit→reset→run dance for code
  changes. `reset()` remains only as "delete outputs".
- **Machine facts logged automatically and exhaustively; interpretations kept out** (curated
  notes elsewhere — an append-only DB of musings rots into a landfill).
- **Warnings must not train people to ignore them**: cosmetic changes produce no warning at
  all (normalized hashing), judged-equivalent changes have a cheap explicit acknowledgment
  (`accept_code`), and every warning names its exits.

### The public API

```python
class TrainModel(oryxflow.tasks.TaskPqPandas):
    code_version = 'v2-log-features'   # str or int; bump when you change this task's logic
    def run(self): ...
```

- Bump → this task **and everything downstream** reruns on the next `run()`, overwriting in
  place (same paths). No version set anywhere → byte-for-byte today's behavior.
- `keep_versions = True` (per-class, off by default) keeps old versions at readable paths
  (`.../TrainModel/v1-baseline/...`) — the compare-two-versions feature.
- Warning when code changed but the version didn't; `oryxflow.accept_code(TaskX)` (or
  `accept_code()` for all currently-warned tasks) acknowledges an output-equivalent change.
- Every run appends structured events to `.oryxflow/events.jsonl` (the stable head; months
  offload to `events-YYYYMM.jsonl`, see decision 7); query via `oryxflow.events.runs(...)` /
  `status()` (SQLite-index-backed) or plain `tail`/`grep`/`jq` on the files.
- `run()`/`build()` return an enriched `RunResult` carrying `run_id` and per-task rerun
  reasons — the same structures that were logged.

### Design decisions

1. **Explicit `code_version` is the correctness authority; hashing is advisory only.** Real
   `run()` bodies are thin orchestration over helper modules and constants; a run()-only
   hash misses most real edits, and any automatic hash has blind spots (data files, dynamic
   dispatch, external APIs) that would become false confidence if it drove reruns — and
   would churn expensive tasks on cosmetic edits if it did. As a warning, a blind spot means
   "no warning" — degrades to today's behavior, never below it.

2. **Record-based completeness, overwrite in place** (not v2's path-folding): completeness =
   outputs exist **and** stored code fingerprint matches current. No stale copies, no path
   churn, works regardless of `save_with_param`, and it is the storage model automatic
   hashing (Stage 2) needs. Old artifacts stay regenerable via the recipe in the event log
   (git SHA + params + code_version), so "keep bytes just in case" is replaced by "keep the
   recipe" — `keep_versions` covers the non-reproducible/expensive exceptions.

3. **Recursive fingerprint for propagation**: fingerprint = md5(family, own code_version,
   sorted dep fingerprints). Bump upstream → downstream fingerprints change transitively →
   each mismatches its record → the band reruns. **Trivial-transparency**: no `code_version`
   here or upstream → fingerprint `None` → check skipped → feature inert; adoption never
   invalidates an existing cache. **Grandfathering**: output exists, record missing → treat
   complete and stamp the current fingerprint (in `build()`'s skip branch), so the *next*
   bump has a baseline.

4. **State store: one JSON file per data directory** (`dirpath/.oryxflow.json`, the reserved
   `settings.db` slot). Latest record per `task_id`: fingerprint, code_version, per-file
   normalized source hashes. It describes those exact artifacts → **it travels with the data
   dir, always via the same channel, never versioned separately** (docs rule: move/restore
   the data dir whole; file-without-record gets grandfathered as current — same trust level
   as every file has today, but partial restores are on the user). Atomic writes
   (tmp + `os.replace`). Correctness depends only on this file — never on the event log,
   never on anything remote. It is the **only mutable store** in the design.

5. **Hashing: AST-normalized, file-level, transitive over repo-local imports.** *(v4 change,
   user-confirmed.)* The hash unit is the set of project files reachable from the task's
   defining module via `import` statements (stop at stdlib/site-packages — files under the
   project root only). Each file is hashed **after AST normalization** (parse → strip
   docstrings → dump), so comments/docstrings/formatting edits produce **no hash change and
   no warning** — the cosmetic-alarm class is eliminated mechanically, not acknowledged
   manually. File-level (not per-symbol closure) is deliberate: symbol-level tracking can
   *miss* dynamically-referenced helpers and go silently green — the one failure mode the
   advisory must never have. Cost of coarseness: editing one function in `utils.py` flags
   every task importing `utils.py` ("coarse yellow") — acceptable for an advisory, and a
   later symbol-level pass may **annotate** warnings ("change touches `yoy_diff`, which
   TaskX references" / "appears unrelated") but never suppress them.

6. **`accept_code()` for judged-equivalent changes.** *(v4 addition.)* A real code change
   the author judges output-equivalent (refactor, rename, added log line) would otherwise
   warn on every run forever — alarm fatigue. `oryxflow.accept_code(task_or_cls)` re-stamps
   the stored hashes at current without rerunning; `accept_code()` bulk-accepts everything
   currently warned. Not a memory burden: the warning text itself names it (checks that fire
   at the decision point get obeyed; rules that must be recalled get missed). Three exits
   from every warning: **bump** (recompute) / **accept** (equivalent) / **reset** (nuke).

7. **Event stream: JSONL is the log; SQLite is a disposable index.** *(v4 change,
   user-confirmed — replaces v3's SQLite-primary and unifies with the catalog plan's
   store.)* **Head + offload** *(user-proposed)*: the active log is always
   `.oryxflow/events.jsonl` — a stable name, so "which file is current" never needs
   answering (`tail -30 .oryxflow/events.jsonl` always works). Before appending, the writer
   checks whether the head's first event belongs to an earlier month than now; if so it
   **offloads** the head (rename to `events-YYYYMM.jsonl`) and starts a fresh one — built
   into `append()`, no external script or cron. Offloaded files are **immutable forever,
   nothing is deleted or rewritten**; content is never modified by the offload (rename
   only). History grows, split into bounded, individually-deletable month files; index
   rebuild tolerates gaps if a user prunes old months. The offload rename is safe because
   (a) the writer opens-appends-closes per event (no long-lived handle to break on
   Windows), and (b) the future sync cursor is **event-id-based**, not filename+offset —
   ids are unique, so a rename is invisible to sync (clients dedup by id). Plain text →
   agents/CLIs query it directly (`tail -30 .oryxflow/events.jsonl` for recent;
   `grep TaskTrain .oryxflow/events*.jsonl` for all history;
   `jq 'select(.type=="task_ran")'`). The SQLite index (stdlib `sqlite3`,
   `.oryxflow/index.db`) is derived and **rebuildable: there are no migrations** — the index
   carries a schema-version stamp; on mismatch after an OSS upgrade the library deletes and
   rebuilds it from the JSONL (sub-second at realistic volumes). Rows are self-describing
   and evolve additively (readers ignore unknown fields, tolerate missing). `.oryxflow/` is
   gitignored (run records are high-frequency exhaust; committing them dirties every diff
   until someone gitignores them and sharing dies — deliberate exports cover the
   git/LFS-snapshot cases).

8. **Sync-ready envelope, corrections-as-events.** *(v4 addition — the only obligation the
   product plan imposes on OSS.)* Every event: unique `id` (uuid4 hex), UTC `ts`, `type`,
   per-type `v`, plus reserved attribution fields `project_id` (generated once into
   `.oryxflow/config.json`), `machine` (`platform.node()`), `user` (git
   `user.email`, best-effort) — nullable, unused locally, they make rows attributable when a
   sync tier exists without touching history. One writer seam (`events.append(event)`) that
   a future sync client tees. **Events are never mutated**: acknowledgments and tags are new
   events (`code_accepted`, later `run_tagged`); the index derives current state — no
   UPDATEs in the log means sync never reconciles mutations.

9. **Run-level events are always on; artifact-level capture is opt-in.** Run events cost
   microseconds. Artifact descriptors (schema/profile/sample — the catalog) cost real time →
   they stay behind `enable_catalog()` and are specified in `20260622-sys-catalog2.md`,
   emitting `artifact_saved`/`lineage` events **into this same stream** with the same
   `run_id`. Event writes never fail a run (try/except + debug log).

10. **loguru and events: same emission points, separate channels.** Engine facts are emitted
    structurally at the `build()` seams via one helper that appends the event *and* logs the
    human loguru line — never by parsing log strings. One deliberate exception: task-authored
    `self.logger` output (already tagged with `task_id`/`task_family` by `TaskLogger`'s
    bound extra) is captured during `build()` by a dedicated loguru sink into `task_log`
    events — the scalars tasks already emit ("corr_avg=0.11") become queryable memory
    instead of lost stderr. Schema settled by field observation (below): the **formatted
    message** is what the agent consumes (it grepped for its own scalar lines and judged
    magnitudes/NaNs from them), so capture formatted message + level + the bound `extra`
    dict (size-bounded), free-text-with-provenance.

11. **`RunResult` is the in-memory view of the same events.** `build()` attaches `run_id`
    and per-task rerun `reason`s; callers (and the MCP server / plugin) get the run's story
    from the return value without querying anything.

12. **Agent-first UX decisions** *(v4 additions, from walking the agent's actual loop)*:
    a. **The staleness warning must be visible without `enable_logging()`.** oryxflow's
       loguru is disabled by default (library pattern) — a correctness advisory routed only
       through it would be silent for most users, breaking "fires at the decision point".
       The warning therefore goes out on **three channels**: `warnings.warn` (visible by
       default — precedent: `complete()` already uses it for no-output tasks), the loguru
       record (for enabled-logging runs), and the `code_warning` event (queryable later).
       It also lands on the return value (`RunResult.warnings`), so callers and agents see
       it without scraping stderr.
    b. **One session-start call**: `oryxflow.events.status()` returns
       `{pending_warnings, last_runs (per family), recent_failures}` — the single entry
       point an agent hits after `/clear` instead of composing three queries.
    c. **The grandfathering trap is guarded.** Adding `code_version` to a task for the
       *first time* does not invalidate existing output (by design). But an agent that edits
       `run()` **and** adds `code_version = 1` in the same edit would get the stale output
       stamped as current — silent staleness. Best-effort guard: at grandfather-stamp time,
       if any hashed source file's mtime is **newer than the output file's mtime**, don't
       stamp silently — emit the same three-channel warning ("output predates current code;
       can't verify — reset or accept_code to confirm"). Local-fs heuristic only (skipped on
       cloud paths); the skill rule ("first-time add after an edit → also reset once") is
       the primary defense, this catches the forgetting.

### Field validation (what a real project agent consumes today)

Asked what it actually consumed to answer "what ran, what are the results, what went
wrong", a project agent reported: (1) persisted `run.py` stdout (~124KB), **grepped** for
the 12 per-flow Execution Summary blocks (`* 1 ran successfully / * 0 failed`) to confirm
the new task computed rather than cache-hit; (2) its own `self.logger` scalar lines as
mid-flight sanity checks (magnitudes, no NaNs); (3) saved artifacts via `outputLoad` for
actual numbers (never scraped from logs); (4) **not** the `RunResult` — the script discards
the return value; (5) error path = grep the persisted stdout for the traceback. Mapping:
(1)→`events.status()`/`runs()` replaces persist-and-grep and works even though logging
gating and stdout truncation don't; (2)→`task_log` capture (formatted message is the
payload that matters); (4)→confirms events must be always-on — real scripts drop the
result object, the log persists regardless; (5)→`task_failed` events must carry the
traceback (bounded), so post-mortems don't need the stdout file. Per-flow attribution
(12 summary blocks) → the envelope carries `flow`.

### Event types (envelope: `id, ts, type, v, project_id, machine, user, run_id, flow`)

`flow` is the flow name when the build was launched via `Workflow`/`WorkflowMulti` (each
per-flow build gets its own `run_id` + `flow`), null for bare `oryxflow.run()`. With params
already on `task_ran`, this makes "did metro X's backtest run?" a query.

| type | payload (v1) |
|---|---|
| `run_started` | requested task families, flow name if any |
| `task_ran` | task_id, family, params (all, incl. insignificant), code_version, fingerprint, source_hashes {file: md5}, **reason** (`output missing` / `code change (a -> b)` / `upstream rerun`), duration_s, git_sha, git_dirty, oryxflow_version, dirpath |
| `task_failed` | task_id, family, params, error, traceback (tail-bounded, e.g. last 4KB), duration_s |
| `run_finished` | counts {ran, complete, failed}, success |
| `code_warning` | task_id, changed files, current code_version |
| `code_accepted` | task_id/family, re-stamped file hashes |
| `task_log` | task_id, level, formatted message, extra dict (size-bounded) — see decision 10 |
| `artifact_saved`, `lineage`, `semantics_generated` | reserved — defined in `20260622-sys-catalog2.md` |

Per-skip events are deliberately **not** emitted (every complete task on every run would
dominate volume for zero insight); `run_finished` carries the counts.

### Known limitations (document, do not solve here)

- The token is a lever, not a detector: forget to bump and (modulo the warning) behavior is
  today's. The oryxflow Claude plugin/skill prompts agents to bump on edit.
- Hash blind spots: data-file contents, external APIs, dynamic imports, monkeypatching —
  warning copy must never claim completeness.
- Dynamic deps: fingerprint folds `deps()` = `requires()`; `TaskAggregator` fixed via a
  `deps()` override; tasks yielded inside a generator `run()` are not folded (the
  `check_dependencies` cascade still forces correct reruns). A `requires()` fanning out from
  a data file can change its dep set at identical params; the fingerprint follows the
  current set.
- Concurrent writers on a shared data dir (bucket): state-file writes last-writer-wins;
  two processes appending events locally: appends are line-atomic on one machine, good
  enough for the sequential engine — document.
- Functional API gets no `code_version` surface yet (follow-up).
- Events are per-machine; sharing/aggregation is the product plan's territory (Oryx planning
  folder), enabled by decision 8 but not built here.
- No MCP surface exists yet: a read-only server over these events (and the catalog) is
  planned separately in `20260712-mcp.md`, sequenced after this plan and catalog2. Until
  then the agent surface is the Python API + the raw JSONL.

## Agent instructions (how an agent works with this feature)

The library ships the mechanics; this guidance ships as (a) an update to the oryxflow
Claude plugin skill (`oryxflow-claude-plugin/skills/oryxflow/SKILL.md`, step 9 below) and
(b) a copy-paste CLAUDE.md snippet in the docs (step 8). The rules, in the order an agent
encounters them:

1. **Session start / after `/clear`:** call `oryxflow.events.status()` — pending code
   warnings, last run per task family, recent failures — before assuming anything about
   cache state. No-Python fallback: `tail -30 .oryxflow/events.jsonl`.
2. **When you change a task's logic** (its `run()`, or a helper module it uses): bump that
   task's `code_version` **in the same edit**. Do not hand-chain `reset()` calls for code
   changes — the bump propagates downstream automatically.
3. **First time adding `code_version` to a task:** if you're adding it *because you just
   changed the code*, also `reset()` that task once — grandfathering treats the existing
   output as current (the mtime guard warns on this, but don't rely on it).
4. **Answer every staleness warning with one of its three exits** — bump (semantic change),
   `oryxflow.accept_code(TaskX)` (output-equivalent refactor), or `reset` (recompute
   regardless). Never ignore one and never leave one firing across runs.
5. **After a run, read the returned `RunResult`:** `result.reasons` says why each task ran;
   `result.warnings` lists unacknowledged code changes; failures carry the first exception
   (and `run(abort=True)`, the default, raises on failure — you can't miss it).
   `MultiRunResult` exposes the same aggregates (`result.ran`/`complete`/`failed` across
   flows) — **never hand-roll aggregation or add print helpers**: the per-build verdict is
   already logged durably as `run_finished` events, so in scripts just capture the result
   for in-process assertions and check `oryxflow.events.status()` afterwards. A live result
   object doesn't survive the script; the events do.
   **Verify that an invalidation took** (field-validated pattern): after a bump or reset,
   the next run must show the intended tasks in `result.ran` with the matching reason
   (`code change (1 -> 2)`) — or in `events.runs()` after the fact. `ran=0` after an
   intended invalidation means the bump/reset didn't reach the cache; treat that as a bug,
   not a convenient skip. Conversely `ran=0` on an untouched pipeline is the healthy
   "cache is trusted" signal.
6. **"The numbers changed and I don't know why":** compare the last two runs —
   `oryxflow.events.runs(task_family='TaskX', last=2)` — and diff params, code_version,
   source_hashes.
7. **Log decision-relevant scalars inside `run()`** via `self.logger.info(...)` — they're
   captured as `task_log` events and become next session's memory.
8. **Experiments you want side by side:** use a string version
   (`code_version = 'v2-log-features'`) plus `keep_versions = True`; old versions stay at
   readable paths.
9. **Raw stream convention:** current = `.oryxflow/events.jsonl` (stable head); offloaded
   months = `events-YYYYMM.jsonl`; all history = glob `events*.jsonl`. Prefer
   `events.runs()`/`status()` when Python is available.

## Implementation

**1. `core.py` — attribute + fingerprint.** `code_version = None` class attribute on `Task`
   (bump-on-logic-change comment). `task_id_str` and `Task.__init__` **unchanged**. Add to
   `Task` after `deps()` (`core.py:314`), no memoization (instances are process-long-lived
   via the `Register` cache; a cached fingerprint would go stale on runtime bumps; recompute
   is a cheap md5 over a small DAG):
   ```python
   @property
   def _code_fingerprint(self):
       """Recursive code identity; None when no code_version is set here or upstream
       (feature inert). Compared against the state store by complete()."""
       dep_fps = [d._code_fingerprint for d in self.deps()]
       if self.code_version is None and all(f is None for f in dep_fps):
           return None
       parts = [self.task_family, str(self.code_version)] + sorted(f or '' for f in dep_fps)
       return hashlib.md5('|'.join(parts).encode('utf-8')).hexdigest()[:16]
   ```

**2. New `oryxflow/state.py` — per-data-dir record store.** JSON at
   `<dirpath>/.oryxflow.json` (`dirpath` = `task.path or settings.dirpath`; per-flow dirs get
   their own store; records travel with their artifacts). API: `get_record(dirpath, task_id)`,
   `put_record(dirpath, task_id, record)` (read-modify-write, atomic tmp+`os.replace`),
   record = `{'fingerprint', 'code_version', 'source_hashes': {relpath: md5}, 'ts'}`.
   In-process cache keyed by store path, invalidated on write. fsspec-routed when
   `settings.cloud_fs_enabled` (pattern of `_make_path_cloud_compatible`,
   `tasks/__init__.py:297`). Lazy-import `settings` (cycle pattern, as `log.py`).

**3. New `oryxflow/codehash.py` — normalized transitive hashes.**
   `module_hashes(task) -> {relpath: md5}`:
   - Start at `type(task).__module__`'s file; collect `import`/`from` targets by `ast`-parse
     (no execution); keep modules whose file resolves under the project root (`Path.cwd()`);
     recurse.
   - Per file: `tree = ast.parse(src)`; strip docstring nodes (module/class/function bodies
     whose first stmt is a constant-str Expr); hash `ast.dump(tree)` — comments and
     formatting vanish in the parse, docstrings via the strip. Fallback to raw-bytes hash on
     `SyntaxError` (never fail).
   - Cache per process keyed by (path, mtime).

**4. `tasks/__init__.py` — completeness + paths + aggregator.**
   a. Factor `_resolved_dirpath()` out of `_getpath` (the `self.path or settings.dirpath`
      logic) and add the record check to `TaskData.complete` (`tasks/__init__.py:92`):
      ```python
      def _code_ok(self):
          fp = self._code_fingerprint
          if fp is None:
              return True
          rec = state.get_record(self._resolved_dirpath(), self.task_id)
          if rec is None:
              return True          # grandfathered; build() stamps it
          return rec.get('fingerprint') == fp
      ```
      `complete = complete and self._code_ok()`; skip for `external=True` (docs: don't set
      `code_version` on external tasks — their output is produced elsewhere).
   b. `keep_versions` path segment in `_getpath` (`tasks/__init__.py:117`), after `tidroot`:
      ```python
      if getattr(self, 'keep_versions', False) and self.code_version is not None:
          tidroot = '{}/v{}'.format(tidroot,
                                    core.TASK_ID_INVALID_CHAR_REGEX.sub('_', str(self.code_version)))
      ```
      (declare `keep_versions = False` on `TaskData`). Bump → new readable dir → reruns
      there; old dirs remain. No load-by-version API in v4 (old versions are at readable
      paths; add `outputLoad(code_version=...)` only when demanded).
   c. `TaskAggregator.deps()` override so bumps propagate through aggregators (its
      `complete/output/invalidate` already iterate `self.run()`; the aggregator contract is
      run() only yields tasks): `return core.flatten([t for t in self.run()])`.

**5. New `oryxflow/events.py` — the stream + index.**
   - `append(type, payload, run_id=None)`: build envelope (uuid4 hex id, UTC iso ts, type,
     v, project_id/machine/user from `_identity()`), then `_offload_if_stale()`, then one
     `json.dumps` line appended to the **head** `.oryxflow/events.jsonl` (dir from
     `settings.eventspath`, default `Path('.oryxflow')`; auto-create). Open-append-close per
     event (no held handle). Wrapped try/except: **an event write must never fail a run**
     (`logger.debug` on failure). Also mirrors into the index (best-effort).
   - `_offload_if_stale()`: read the head's first line (cache its month per process); if
     that month < current UTC month, rename head → `events-YYYYMM.jsonl` (its month) and
     let the next append create a fresh head. Rename-only — content untouched; offloaded
     files immutable thereafter.
   - `_identity()`: `project_id` generated once into `.oryxflow/config.json`; `machine` =
     `platform.node()`; `user` = `git config user.email` best-effort, cached.
   - Index: `.oryxflow/index.db`, stdlib `sqlite3`, `PRAGMA user_version = SCHEMA_V`;
     on open, mismatch → drop file → `rebuild()` (stream `events.jsonl` + all
     `events-*.jsonl`, tolerate missing months/unknown types/fields). Tables `runs`,
     `tasks`, `warnings`, `task_logs` (+ catalog tables per the companion plan).
   - Query surface: `runs(task_family=None, last=20)`, `warnings()` (pending = latest
     `code_warning` per task not followed by `code_accepted`/`task_ran`),
     `status()` → `{'pending_warnings': [...], 'last_runs': {family: row},
     'recent_failures': [...]}` (the agent's session-start call), `iter_events(...)`.
   - Settings additions (`settings.py`): `eventspath = Path('.oryxflow')`, `events = True`
     (`False` → `append()` is a complete no-op: no dir created, no index touched).
   - Import discipline: top-level imports in `events.py` (and `state.py`) are **stdlib
     only**; `settings` is lazy-imported inside functions (import-cycle-safe — settings
     imports core at module level; same pattern as `log.py`).

**6. `core.py:build()` — emit, stamp, warn, enrich.** One private helper
   `_emit(event_type, payload, run_id)` = `events.append(...)` + the matching human loguru
   line (single call site per fact; the two channels can't disagree). Seams in `_process`:
   a. Build start/end: `run_started` / `run_finished` (counts, success); generate `run_id`.
      `build()` gains an optional `flow=None` kwarg stamped into the envelope;
      `Workflow.run`/`WorkflowMulti.run` pass their flow name through `oryxflow.run()`
      (each per-flow build → own `run_id` + `flow`).
   b. **Skip branch** (`core.py:558`): fingerprint non-None + no record → grandfather-stamp
      `state.put_record(...)` (no event — state-only) — **unless** the mtime guard trips
      (decision 12c: any hashed source file newer than the output file, local fs only) →
      warn *"output predates current code; can't verify — reset or accept_code to
      confirm"* instead of stamping silently. Record exists, fingerprint matches, stored
      `source_hashes` differ from current → staleness warning naming the changed file(s)
      and the exits:
      *"task {family}: {files} changed since cached run; code_version still {v} — reusing
      cached output. Bump code_version to recompute, or oryxflow.accept_code({family}) if
      output-equivalent (best-effort check: can't see data files or dynamic calls)."*
      Both warnings go out on **all channels** (decision 12a): `warnings.warn` (visible
      without `enable_logging()`), the loguru record, the `code_warning` event, and
      `RunResult.warnings`.
   c. **Before running**: compute reason — outputs missing → `'output missing'`; outputs
      present but `_code_ok()` false → `'code change ({old} -> {new})'`; a dep reran this
      build → `'upstream rerun'`.
   d. **After success** (`core.py:603`): `state.put_record(...)` (fresh fingerprint +
      hashes) then `task_ran` event (payload per the table; git SHA/dirty via one cached
      best-effort `subprocess` call per build). On failure: `task_failed` event, no state
      record.
   e. **task-log capture**: on build start attach a loguru sink filtered to the
      `oryxflow.task` display name (see `log.py` `TaskLogger.patch`), writing `task_log`
      events with the bound `task_id`/`task_family`; detach on build end (try/finally).
   f. **`RunResult` enrichment**: constructor gains `run_id`; `ran` entries carry their
      reason (a `{task_id: reason}` dict exposed as `result.reasons`); staleness/grandfather
      warnings collected into `result.warnings` (list of the warning strings). `__str__`
      unchanged (luigi-compatible wording per CLAUDE.md — reasons/warnings are programmatic,
      not appended to summary lines the plugin parses).
      **`MultiRunResult` gets aggregate accessors** (field feedback: an agent hand-rolled
      `sum(len(r.ran) for r in result.values())` because they don't exist): properties
      `ran` / `complete` / `failed` flattening across flows, and `reasons` / `warnings`
      merged — so `result.success, len(result.ran), len(result.failed)` reads identically
      on both result types and no caller ever aggregates by hand. The one-glance verdict
      needs no `print()` in scripts either: `run_finished` (event + INFO line via `_emit`)
      already records counts + success per flow, durably.

**7. `oryxflow/__init__.py` — `accept_code(task=None)`.** With a task/class: recompute
   current hashes, `state.put_record` (fingerprint unchanged, hashes re-stamped), emit
   `code_accepted`. With no argument: iterate state records across the current
   `settings.dirpath` store, re-stamp every record whose stored hashes differ from current.
   Export `accept_code` and the `events` module; add `.oryxflow/` to the repo `.gitignore`.

**8. Docs + changelog.**
   - `docs/source/managing-workflows.rst`: the `code_version` idiom; **"bump, don't reset"**;
     downstream propagation; the warning + its three exits + blind-spot honesty;
     normalization (comments/docstrings never warn); grandfathering; `keep_versions`;
     the state-JSON-travels-with-data rule (move data dirs whole); the event stream — the
     head/offload convention stated **contractually** (current = `events.jsonl`, offloads =
     `events-YYYYMM.jsonl`, all = glob `events*.jsonl`) with `tail`/`jq` examples,
     `oryxflow.events.runs()`/`status()`; `.oryxflow/` gitignore guidance; and a
     **copy-paste CLAUDE.md snippet** containing the Agent-instructions rules above, for
     projects not using the plugin.
   - `CHANGELOG.md` entry; update `docs/blog/20260711-oryxflow-value-prop-ai.md` to
     reference this as shipped; docs/blog: honest DVC comparison ("DVC hashes files + yaml
     stages; oryxflow identity is native Python task identity — params + code_version, zero
     config files") next to the MLflow comparison.

**9. Plugin skill (cross-repo).** In
   `oryxflow-claude-plugin/skills/oryxflow/SKILL.md` (+ `reference.md`): add the Agent
   instructions section above verbatim-in-spirit — session-start `status()`, the
   bump-in-the-same-edit rule, the first-time-add-plus-reset rule, the three warning exits,
   `result.reasons`/`result.warnings`, the two-runs diff recipe, the `self.logger` scalars
   idiom, and the head/offload file convention. (MCP tools over the same surface are a
   follow-up, specified in `20260712-mcp.md` — not part of this plan.)

## Files modified

- `oryxflow/core.py` — `code_version` on `Task`; `_code_fingerprint`; `build()` emit/stamp/
  warn/reason seams; task-log sink attach; `RunResult.run_id` + reasons + warnings;
  `MultiRunResult` aggregate accessors (`ran`/`complete`/`failed`/`reasons`/`warnings`).
- `oryxflow/state.py` — **new**: per-data-dir JSON record store (atomic, fsspec-aware).
- `oryxflow/codehash.py` — **new**: AST-normalized transitive repo-local hashes.
- `oryxflow/events.py` — **new**: JSONL writer (envelope, rotation), identity, SQLite index
  (rebuildable, `user_version`-stamped), query surface.
- `oryxflow/tasks/__init__.py` — `_code_ok` + `_resolved_dirpath`; `keep_versions` segment;
  `TaskAggregator.deps()`.
- `oryxflow/settings.py` — `eventspath`, `events` (the `db` slot is now used by state).
- `oryxflow/__init__.py` — `accept_code`, expose `oryxflow.events`.
- `.gitignore` — `.oryxflow/`.
- `docs/source/managing-workflows.rst`, `CHANGELOG.md`, blog — docs (incl. the CLAUDE.md
  snippet and the head/offload file convention).
- `oryxflow-claude-plugin/skills/oryxflow/SKILL.md` (+ `reference.md`) — agent guidance
  (step 9, separate repo).
- `tests/test_code_invalidation.py`, `tests/test_events.py` — **new** (below).

NOT in v4: path-hash folding (v2), notes/insights API, load-by-version API, functional-API
surface, symbol-level AST analysis (annotation-only, later), any network/sync feature,
artifact capture/semantics (companion catalog2 plan).

## Verification

1. **Baseline holds.** No existing test sets `code_version`; event writes are additive:
   ```bash
   python -m pytest tests/test_main.py tests/test_workflow.py \
       tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
   ```
   **73 passing**, ids/paths byte-identical. Point `settings.eventspath` at tmp in
   `conftest` if `.oryxflow/` creation trips anything.

2. **`tests/test_code_invalidation.py`:**
   - *Transparency*: no `code_version` anywhere → fingerprint None, no record check, paths
     unchanged.
   - *Bump reruns*: `'1'` → complete; redefine `'2'` → incomplete → rerun at the **same
     path**, record updated.
   - *Propagation*: chain A→B→C (B, C unversioned): bump A → all rerun; bump only C → only
     C (`RunResult.did_run`).
   - *Grandfathering*: build unversioned output; add `code_version='1'` → next build zero
     reruns (record stamped); bump `'2'` → reruns.
   - *Identity stability*: `task_id`, `repr`, `==`, `hash()` unchanged by bumps.
   - *Normalization*: edit only a comment/docstring in the hashed module → **no** warning;
     edit code → warning. Assert via `pytest.warns` (the `warnings.warn` channel — proving
     visibility **without** `enable_logging()`) and via a loguru list-sink (`caplog` won't
     see loguru); same text in `result.warnings`.
   - *accept_code*: after a code-change warning, `accept_code(TaskX)` → next build silent,
     no rerun; `code_accepted` event present; `events.status()` no longer lists it.
   - *Grandfather mtime guard*: unversioned output on disk, touch the task's source file to
     a newer mtime, add `code_version='1'` → build warns "output predates current code"
     instead of silently stamping.
   - *keep_versions*: bump with `keep_versions=True` → new `v2/` dir, `v1/` output intact.
   - *External*: unversioned `external=True` unaffected; versioned deps still propagate
     around it.
   - *Aggregator*: downstream of a `TaskAggregator` reruns when a yielded task bumps.

3. **`tests/test_events.py`:**
   - Build emits `run_started`/`task_ran`/`run_finished` with shared `run_id`; envelope has
     unique ids, UTC ts, project_id.
   - Reasons correct: fresh build → `output missing`; code bump → `code change (1 -> 2)`;
     downstream → `upstream rerun`.
   - Failure → `task_failed` with error text + tail-bounded traceback; run continues
     logging.
   - `WorkflowMulti` run over 2 flows → two `run_id`s, envelopes carry the flow names;
     `runs()` filterable per flow; `MultiRunResult.ran`/`.complete`/`.failed`/`.reasons`
     aggregate across both flows and match the per-flow `run_finished` counts.
   - `RunResult.run_id` set; `result.reasons` matches the events.
   - Head/offload: appends land in `events.jsonl`; seed a head whose first event is dated
     last month (write the line directly) → next `append()` renames it to
     `events-YYYYMM.jsonl` and starts a fresh head; offloaded content byte-identical.
   - `status()` returns pending warnings + last runs + recent failures after a mixed build.
   - Index rebuild: delete `index.db` → `runs()` still answers (rebuilt); bump
     `SCHEMA_V` constant → auto-rebuild, no error (the no-migrations property).
   - `task_log` capture: a task calling `self.logger.info("corr_avg={}", 0.11)` during a
     build yields a `task_log` event tagged with its task_id.
   - Event-write failure (unwritable `eventspath`) does not fail the build.
   - Raw-stream usability: read the JSONL with plain `open()`+`json.loads` per line (the
     `tail`/`jq` contract).

4. **End-to-end smoke.** README model-comparison flow: run (`3 ran`), run (`3 complete`);
   edit a comment in the middle task → run → silent; edit its logic without bumping → run →
   warning names the file and the three exits; `accept_code` → silent; bump → exactly it +
   downstream rerun with reasons in `oryxflow.events.runs()`; `tail`/`jq` the JSONL and
   confirm the same story.

## Appendix: agent-user evaluation of v4 (2026-07-12)

*Two parts. Part 1 is the standalone statement of the problem from the perspective of an AI
coding agent working against a caching pipeline (written to be lifted into docs/help pages,
independent of any solution). Part 2 evaluates this v4 plan against it and walks the actual
usage to surface UX friction. Feeds the value-prop blog and the managing-workflows docs.*

### Part 1 - the concerns, stated on their own

*(Trust/accuracy concerns - the reasons a cached result cannot be taken at face value. No
reference to the solution; these are the requirements the design has to satisfy.)*

1. **Silent staleness is the primary failure mode.** The most common data-science error is
   not wrong logic - it is correct logic that already ran, reused after it changed. Edit a
   task, the cache still holds the old output, the pipeline runs green, and the reported
   numbers came from code already deleted. On a dependency chain this compounds: one stale
   upstream output silently poisons everything below it, with no error anywhere.
2. **The change is usually not where a naive check looks.** A task's `run()` is often thin -
   the real logic lives in a helper module or a shared constant it calls. The task body can
   be byte-for-byte identical while its behavior changed completely. Any staleness check that
   inspects only the task, not the code it transitively depends on, gives false reassurance
   exactly when it matters.
3. **Correctness must not depend on an act of memory.** The manual-reset remedy requires
   remembering, out of band, after every edit. Agent recall degrades across a long session.
   Any correctness guarantee routed through "the author remembers to do X after editing"
   fails some fraction of the time. Rules that must be recalled get missed; checks that fire
   at the moment of the decision get acted on.
4. **Given a result, there is no way to tell whether it is current.** Handed a cached
   artifact or a number, "was this produced by the current code, the current inputs, and
   which version?" has no answer - no provenance is attached (what code, what parameters,
   what run produced it), so "is this fresh?" is answered by guessing.
5. **A healthy skip and a broken skip look identical.** "0 tasks ran" cannot be distinguished
   between the good case (cache correctly trusted) and the dangerous one (the change did not
   reach the cache, so it silently no-op'd). Both present the same green output; the
   dangerous one is invisible.
6. **Cross-session amnesia.** An agent does not persist between sessions except through what
   is written down. "Did I already run this for Houston?", "what did the last session
   execute?", "why does this number differ from what I remember?" are all answered by
   guessing. Mid-session scalars and interpretations evaporate unless manually recorded.
7. **A wrong "green" is worse than an honest "I don't know."** An automatic check that
   asserts "up to date" when it cannot actually know (a data file changed, a value came from
   an external API, dispatch was dynamic) is more dangerous than no check, because it converts
   caution into false confidence. Where the system cannot be sure, it must say so, never imply
   all is fine.

*Through-line: a cache is trustworthy only if staleness is caught without relying on memory,
if the catch extends to the code a task depends on (not just the task), and if the system is
honest about the limits of what it can verify.*

### Part 2 - does v4 address these, and how does it feel to use

**Concern-by-concern.**

| # | Concern | Verdict | How |
|---|---|---|---|
| 1 | Silent staleness | Addressed | `code_version` bump propagates downstream; if forgotten, the advisory hash warns. |
| 2 | Helper blind spot | Addressed (the standout) | AST-normalized hashing transitive over repo-local imports catches edits to `utils/*` even when `run()` is unchanged. The concern that mattered most; the design targets it directly. |
| 3 | No reliance on memory | Partial - honestly so | The *catch* (warning) fires at the decision point on channels visible without `enable_logging()`. But the *primary action*, bumping `code_version`, is still an act of memory. v4 converts "remember to reset" into "remember to bump", and makes forgetting recoverable rather than impossible - a net under the mistake, not elimination of it. Should be stated plainly. |
| 4 | Is this result current? | Addressed | The per-dir record stores fingerprint + code_version + source_hashes; the event log stores the full recipe (git SHA, params, version). |
| 5 | Healthy vs broken skip | Addressed | Agent-instruction 5's field-validated pattern: after an intended invalidation `result.ran` must show the task with reason `code change (1 -> 2)`; `ran=0` after a bump is a bug signal, `ran=0` on an untouched pipeline is the trust signal. |
| 6 | Cross-session amnesia | Addressed (strong) | `events.status()` at session start, `events.runs()` for history, `task_log` capture of `self.logger` scalars = the hand-curated memory, now automatic. |
| 7 | False green | Addressed by principle | Hashing advisory only; "design against false green"; blind spots warn or say-so, never imply "unchanged". |

All seven covered; #3 and the data-file slice of #7 carry honest, documented residuals (below).

**How an agent actually uses it - and where it is confusing.** The happy path is good: a task
already carrying `code_version='1'`, edit its logic, bump to `'2'`, downstream reruns,
`events.runs()` confirms. Session-start `status()` is the strongest part (answers 5 and 6 in
one call). Four friction points surfaced, clustering on *"which verb do I use?"*:

- **Friction 1 - the first-time-add trap is the most dangerous moment, and it is the adoption
  moment.** A project with zero `code_version`s (the common starting state) hits instruction
  3 on every task: adding `code_version` *because you just edited the code* requires *also*
  `reset()` once, or grandfathering stamps the stale output as current, silently (the mtime
  guard is best-effort and git-checkout-noisy). So the first interaction with the feature, on
  every existing task, is the one case demanding both bump and reset - the dance the feature
  was sold as replacing - and the failure is silent. *Fix:* document the safe on-ramp -
  **adopt by adding `code_version` in a change that edits nothing else**, so every task
  grandfathers against its real current output; only then are bumps clean. v4 documents the
  trap but not the on-ramp.
- **Friction 2 - shared-helper edits: "which/how many tasks do I bump?"** Changing
  `query_npi_returns` correctly warns on every task that transitively imports it, but the
  action is per-task (bump each affected `code_version`). A thin-tasks-over-`utils/` project
  (this one) can need many bumps for one helper edit, with no "bump everything the changed
  file feeds" operation. Missed ones keep warning in `status()` (recoverable) but it is manual
  reconciliation. Warrants a documented pattern, possibly a helper listing tasks affected by a
  file.
- **Friction 3 - `accept_code()` is a friendly-named footgun.** The three exits are presented
  as coequal but are not symmetric in risk: bump and reset recompute (safe); `accept_code`
  blesses existing output as still-valid (trust-me). It is the one exit that can silently
  corrupt - refactor a helper, subtly change behavior, wrongly judge it output-equivalent, and
  accept re-stamps stale output with no rerun and no further warning. The warning copy and
  docs should mark it as the deliberate, higher-bar exit ("only if certain the output is
  byte-identical; when unsure, bump - recompute is cheap insurance").
- **Friction 4 - "recompute this now" has two mechanisms, and the data-file case falls between
  them.** The model is "bump for code, reset to delete". But when a data file the hash cannot
  see changes, code did not change (bump is semantically wrong) yet a recompute is needed - the
  answer is `reset()`, so for that slice memory-reliance (concern 3) returns. "reset" is thus
  not purely "delete outputs"; it is also "force recompute when the system cannot detect the
  reason". That dual role needs naming - the decision table below gives it two explicit rows
  (external input changed vs suspect/corrupt cache), and resetting *at the loader task* is the
  key subtlety: a downstream reset re-loads the cached old input.

**The fix that resolves most of the confusion: one decision table.** v4 introduces effectively
four ways the cache relationship changes (Parameter, bump, `accept_code`, reset); the
confusion is the absence of one place saying which to reach for. Put this in
`managing-workflows.rst` and the CLAUDE.md snippet verbatim:

| I changed... | Do this | Why |
|---|---|---|
| a value/knob that is a `Parameter` | nothing | new identity auto-reruns; old output kept side-by-side automatically |
| logic (this task's `run()` or a helper it imports), output will differ | bump `code_version` | propagates downstream, recomputes |
| code, but output is provably identical (rename, extract, log line) | `accept_code()` - only if certain; when unsure, bump | re-stamps without recompute; the one non-recomputing exit |
| an external input the pipeline reads (raw data file, API response) - not code, not a `Parameter` | `reset()` the loader/source task that ingests it (cascades downstream) | invisible to both mechanisms (no code fingerprint moves, no param identity changes - a hash blind spot); force the recompute at the point of ingestion, not downstream (a downstream reset still reloads the cached old data) |
| nothing the system can see, but I need a fresh compute (suspect or corrupt cached output) | `reset()` | forces recompute when no code fingerprint moved |
| I want the outputs gone | `reset()` | delete |
| first time adding `code_version` to a task I also just edited | bump *and* `reset()` once (or add it in an edit-free change first) | grandfathering would otherwise bless stale output |

The three highest-value rows are the external-input row, the "suspect cache" row, and the
first-add row - the cases the "bump, don't reset" tagline quietly does not cover. The
external-input case is the frequent, *legitimate* one (new data arrived, nothing is wrong):
because the loader-task pattern makes external data enter through a task, resetting that task
is the ingestion-point handle. It also re-incurs the memory-reliance residual (concern 3) -
the system cannot detect the new data, so the author must know to reset - which is the
strongest argument for the forward option in known-limitations: folding a source
content-hash/mtime into the loader task's identity so input changes auto-invalidate (not in
v4; the data-file blind spot is documented there).

**Net.** v4 addresses all seven concerns; #1/#2/#4/#5/#6 solidly, #3 and the data-file corner
of #7 with honest, documented residuals. The correctness engine is sound. The remaining risk
is entirely UX-of-the-verbs: the first-add grandfather trap (dangerous, and it is the adoption
moment), the silent `accept_code` mis-judgment, and the fuzzy reset-vs-bump boundary for
changes the hash cannot see. None require engine changes - they are a safe-adoption on-ramp,
warning/doc copy that ranks the three exits by risk, and the decision table above. Shipped
alongside the code, the feature feels trustworthy instead of like a fourth thing to keep in
sync.

## Implementation notes (divergences from the plan as built)

Shipped in oryxflow 26.7.12 (setup.py bumped). Baseline gate: the plan's "73 passing" was
stale -- the suite was already at **86 passing** before this work (tests added since the
plan was written); the gate was held at 86, byte-identical ids/paths, and the final suite
is **108 passing** (86 + 22 new in `test_code_invalidation.py` / `test_events.py`). The
end-to-end smoke (Verification 4) passed: fresh run -> cached -> comment-edit silent ->
logic-edit warns naming file+exits -> `accept_code()` silences -> bump reruns exactly the
band with `code change (1 -> 2)` / `upstream rerun` reasons queryable in `events.runs()`
and readable with plain `open()`/`jq` on the JSONL.

Divergences, in decreasing significance:

1. **The SQLite index was dropped entirely** (user-directed mid-implementation:
   "do NOT write too much custom code"). `runs()`/`warnings()`/`status()` are direct
   Python scans of the JSONL stream (`iter_events`), which at realistic volumes answer in
   milliseconds. This deleted the DDL/mirror/rebuild/schema-version/locking machinery
   (~120 lines) and decision 7's index-specific tests (delete-index rebuild, `SCHEMA_V`
   bump). The JSONL format is unchanged, so a derived index can be layered on later
   without migration if volumes ever demand it.
2. **Event writes are asynchronous** (user-requested: "logging must not slow the main
   workflow"). `append()` builds the envelope and enqueues; one daemon writer thread
   drains (ordering preserved, open-append-close per event as planned);
   `events.flush()` runs at build end, at atexit, and before every query, so the
   run's story is durable on return and queries are always current. Motivation:
   synchronous per-event I/O measured a 4-5x test-suite slowdown (9s -> 40s); with the
   async writer plus the caches in (9) the suite runs ~5-6s, faster than pre-feature.
3. **`task_log` capture is a hook in `TaskLogger` (`log.py`), not a loguru sink.** The
   plan's dedicated-sink mechanism cannot work: with logging disabled (the library
   default) loguru drops oryxflow-namespace records *before* any sink sees them, and
   events must be always-on regardless of log gating. `TaskLogger` retains its bound
   context (`_context` slot) and, for level methods, calls a build-scoped capture
   callback (`log.set_task_log_capture`, save/restore for re-entrant builds) with the
   formatted message + level + context; the loguru emit is unchanged.
4. **`StalenessWarning(UserWarning)` category with `simplefilter('always')`** (project-
   agent feedback): python's default warning filter dedups per call site, which would
   silence the second occurrence of the same warning within one live process (notebook,
   REPL) -- exactly when it matters. Exported as `oryxflow.StalenessWarning`.
5. **Project root is marker-based, not `Path.cwd()`** (project-agent feedback):
   `codehash._project_root()` walks up from the task's defining module (or cwd) to the
   nearest `.git`/`pyproject.toml`/`setup.py`/`setup.cfg`, cached; `codehash.PROJECT_ROOT`
   remains an explicit override (used by tests). Keeps the hash unit and relpath keys
   stable across subdir invocations / test runners / notebooks.
6. **Records stamp the interpreter version** (`py: codehash.PY_TAG`, project-agent
   feedback): `ast.dump` output changes across Python minors, so on a `py` mismatch the
   staleness comparison degrades to a silent re-stamp (same trust level as
   grandfathering) instead of firing a mass false-alarm wave after an interpreter
   upgrade.
7. **`settings.db` renamed `settings.state_filename`** (user-requested: multiple stores
   exist and it is not a database). It is the per-data-dir record file *name*;
   `state.py` honors it. The slot was unused anywhere else. The file itself was then
   renamed from the plan's `.oryxflow.json` to **`.oryxflow-code-status.json`**
   (user-requested: the generic name didn't say what it holds — the code-completeness
   status of each output).
8. **The advisory sweep (`_advise`) recurses upstream of a complete task** rather than
   checking only the skip-branch task itself: warnings follow the import graph, so an
   upstream-only module edit must warn even when the only visited (complete) task is
   downstream in a different module. Prunes at fingerprint `None` (a `None` fingerprint
   implies the entire upstream subtree is unversioned) and once per task_id per build.
9. **Performance caches added** (needed to hold the baseline's runtime):
   `codehash.module_hashes` full-walk result cached per (module, root) and revalidated by
   file mtimes; `_imports_of` cached by (path, mtime); `core._git_info` cached with a
   10s TTL (two subprocesses per build dominated small-DAG runtimes on Windows).
10. **Fingerprint memoization was considered and reverted**: a per-call memo was briefly
    added after "exponential on diamonds" feedback, then removed when the feedback was
    walked back (real DAG shapes make it a small constant factor; the plan's no-memo
    reasoning stands).
11. **Docs incorporate the project-agent UX review**: a "which verb, when" decision
    table; the safe adoption on-ramp (add `code_version` in an edit-free change);
    `accept_code` marked as the one non-recomputing, higher-bar exit ("only if certain
    -- when unsure, bump", also in the warning copy); the `keep_versions` toggle-on
    relocation gotcha; bulk `accept_code()` covering only `settings.dirpath`; the
    warnings-follow-imports vs reruns-follow-deps seam; git-checkout mtime noise on the
    grandfather guard. A diamond propagation test was added per the same review.
12. **Step 9 was done** (the plugin repo was available locally at
    `../oryxflow-claude-plugin`): `SKILL.md` iterate loop rewritten around
    bump-in-the-same-edit (reset kept as the pre-26.7.12 fallback and the
    data-file/suspect-cache verb), new "Code-aware invalidation & the event stream"
    section with the agent rules, `reference.md` reset section updated, plugin
    changelog `[Unreleased]` bullets added. Library-side guidance gated on
    `oryxflow >= 26.7.12`; the plugin floor stays 26.6.6.
13. **`events.print_status()` added post-review** (not in the plan): `status()`/`runs()`
    return data and print nothing, so the docs' bare session-start
    `oryxflow.events.status()` looked like a no-op in a script. `print_status()` is a
    thin formatter over `status()` (returns `None`); docs/skill now say "returns a
    dict -- print it, or use `print_status()`".
