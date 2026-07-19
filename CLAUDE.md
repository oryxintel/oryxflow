# CLAUDE.md

Guidance for working in the **oryxflow** repo.

## What this is

oryxflow is a small, self-contained Python library for building data-science workflows: you
declare `Task` classes with parameters, dependencies (`requires`), and a `run()` that
`save()`s output; the engine runs the DAG in dependency order and skips tasks whose output
already exists. It has no heavyweight workflow-engine dependency — the task model, parameters,
and executor all live in `oryxflow/core.py` + `oryxflow/parameter.py`.

## Layout

```
oryxflow/
  __init__.py        # public API: run, preview, Workflow, WorkflowMulti, FlowExport/Import,
                     #   invalidate_*, requires/inherits, re-exported Parameter types
  core.py            # the engine: Task, Register metaclass, Target/LocalTarget, flatten,
                     #   getpaths, task_id_str, inherits/requires, find_deps, build()
  parameter.py       # Parameter, Int/Float/Bool/Date/Dict/List/Choice/Enum Parameter
  tasks/__init__.py  # TaskData (+ TaskCache/Json/Pickle/CSV/Excel/Pq/Markdown...), TaskAggregator
  targets/__init__.py# CacheTarget, _LocalPathTarget, DataTarget + format targets, Target re-export
  settings.py        # global settings (dirpath, cached, check_dependencies, task-id lengths...)
  cache.py           # `data` = in-memory target cache (dict)
  utils.py           # print_tree (preview), traverse, param generators, bcolors
  functional.py      # decorator-based functional Workflow API (uses only oryxflow.requires)
tests/               # pytest suite (see tests/setup.md)
docs/                # examples + docs/todo/ design notes
```

Ignore `bak/`, `build/`, `dist/`, `*.egg-info/`, `data/`, `models/`, `tests-data/` — local
artifacts / backups, not source. (`oryxflow/core/` is an empty stale dir; the real module is
`oryxflow/core.py`.)

## Public API (what exists — check here before adding new code)

Everything below is exported from `oryxflow` (top-level) unless noted. Reach for these
instead of reinventing them.

**Run / load**
- `run(tasks, forced=None, forced_all=False, forced_all_upstream=False, confirm=False, abort=True, ...)` — run task(s) in dependency order (`workers` is accepted but ignored — sequential engine).
- `preview(tasks, show_params=True, ...)` / `show(task)` — print the execution tree without running.
- `runLoad(task, params=None, load=True, taskLoad=None, reset=False)` — one-liner: build a `Workflow`, optionally `reset`, `run`, then `outputLoad` and return it. `runIt(...)` is `runLoad(..., load=False)`. `runIterConcat(task, params, ...)` is the multi-flow analogue: builds a `WorkflowMulti` over a param grid, runs, and returns `outputLoadConcat` (per-flow outputs row-stacked into one DataFrame, tagged by params). Prefer these for quick run-and-fetch over hand-rolling a `Workflow`.

**Workflow objects** — `Workflow(task=None, params=None, path=None, env=None)` and `WorkflowMulti(...)` (multi-experiment: `params` is `{flow_name: {param: val}}`, methods take an optional `flow=` selector). Key methods on both: `run`, `preview`, `complete`, `outputLoad` / `outputLoadAll` / `outputLoadMeta` / `outputLoadMetaJson`, `outputPath`, `reset` / `reset_upstream` / `reset_downstream`, `set_default`, `get_task`, `attach_flow`. `reset_upstream(anchor, only=<family|families>)` invalidates just those task families across the whole upstream DAG (relies on recursive `complete()` to recompute what's downstream of them); on `WorkflowMulti` `anchor` defaults to the flow's default task. `reset_downstream(task, task_downstream=None)` invalidates a task's **family** and everything downstream of it up to `task_downstream` (defaults to the flow's default task) — it does **not** instantiate `task` (only reads the class-level `.task_family`), so it works for DAG-internal-param families you can't name, and it invalidates the whole band *explicitly* (cascade-independent, unlike `reset_upstream(only=)`). **`WorkflowMulti` only:** `outputLoadConcat(task=None, ...)` — narrow, opt-in: row-stacks every flow's output into one DataFrame tagged by each flow's params (use only when all flows' outputs are schema-compatible DataFrames; it collapses the per-flow `{flow: data}` dict `outputLoad` returns, so it's a *separate* method by design, not an `outputLoad` kwarg).

**Deps / params (decorators)** — `@requires(*tasks | {name: task})` copies parent params **and** wires `requires()`; `@inherits(...)` copies params only (adds `clone_parent`/`clone_parents`, you write `requires()` yourself). Params live in `parameter.py`, re-exported: `Parameter`, `IntParameter`, `FloatParameter`, `BoolParameter`, `DateParameter`, `DictParameter`, `ListParameter`, `ChoiceParameter` (string-native, validates against `choices=[...]`), `EnumParameter` (use `significant=False` to exclude from `task_id`).

**Share / move flows** — `FlowExport(tasks=None, flows=None, save=False, path_export='tasks_export.py')` generates standalone task files; `FlowImport(...)` loads them back. (Generated-text contract — see the FlowExport note under Conventions.)

**Invalidate** — `invalidate_upstream(task, confirm=False)`, `invalidate_downstream(task, task_downstream, confirm=False)`, plus `taskflow_upstream` / `taskflow_downstream`. NB: `invalidate_all` and `invalidate_orphans` are **stubs that raise `NotImplementedError`** — don't point users at them.

**Config** — `set_dir(dir=None)` (init + set data dir), `enable_cloud_storage(protocol, bucket, prefix=None)` / `enable_gcs(bucket, prefix=None)` (fsspec-backed; needs the `gcs`/`s3`/`cloud-base` install extra), `enable_logging()` / `disable_logging()`, and the mutable `settings.*` (`dirpath`, `cached`, `check_dependencies`, `execution_summary`, task-id lengths).

**Functional API** — `oryxflow.functional.Workflow` is a separate decorator-based style (`@flow.task`, `@flow.requires`, `@flow.params`, `@flow.persists`). Independent of the class-based API above; don't mix the two in one example.

**Task-body idiom** (inside `run()`): default to `self.inputLoad()` (single dep → the data; `a, b = self.inputLoad()` for a multi-output dep; `self.inputLoad(keys='train')` to pick one output; `self.inputLoad(task=0|'name')` to pick one dep). Reach for `self.input()` only when you want the target object itself (its `.path`, or a deliberate `.load()`). For an aggregator whose `requires()` fans out over a dict/list of deps, `self.inputLoadConcat(...)` row-stacks them into one DataFrame, tagging each with its params (the in-`run()` analogue of `WorkflowMulti.outputLoadConcat`; see `docs/todo/20260701-engine-hierarchical-aggregate.md`). Save with `self.save(...)` / `self.saveMeta(...)`. Keep examples on `self.inputLoad()` — the docs standardize on it. Three tiers on each side, low→high: `input()`/`output()` (target) → `inputLoad()`/`outputLoad()` (data, the default) → `inputLoadConcat()`/`outputLoadConcat()` (concat, narrow).

**`input()` indexing gotcha.** `self.input()` mirrors `requires()`: a single Target for a single-output dep, a `{persist: target}` **dict** for a multi-`persists` dep, and — for multiple deps — either a positional **list** (`requires(T0, T1)`) or a **dict** keyed by your names (`requires({'features0': T0, 'features1': T1})`). Integer indexing `[0]`/`[1]` is *only* the positional case; **prefer the named-dict form** so you select deps by meaningful name (`self.input()['features0']` / `self.inputLoad(task='features0')`) instead of by position. A persist output is always selected **by name**, never by index: `self.input()['train'].load()` (single dep), `self.input()['features0']['train'].load()` (named multi-dep), or `self.input()[0]['train'].load()` (positional). `self.input().load(keys=...)` does NOT work for a multi-`persists` dep — there `self.input()` is a dict, not a target. Equivalent high-level: `self.inputLoad(keys='train')`.

Outputs are declared with **`persists`** (plural) — e.g. `persists = ['x', 'y']`. `persist` (singular) is a backwards-compatible alias folded into the internal `self.persist` in `TaskData.__init__`; engine code reads `self.persist`, but docs/examples standardize on `persists`.

## Architecture notes that bite

Read `docs/todo/20260606-sys-decouple-luigi.md` and
`docs/todo/20260606-sys-param-global.md` before changing the engine — they capture the
non-obvious decisions. Highlights:

- **Sequential engine.** `core.build()` runs the DAG in-process, in dependency order. The
  `workers` argument is accepted but ignored. It handles `external=True` tasks (never run),
  generator `run()` (TaskAggregator / dynamic yields), errors (mark failed, record first
  exception, `oryxflow.run(abort=True)` raises `RuntimeError` *chained* to it — see "Logging"),
  and is re-entrant (a task's `run()` may call `oryxflow.run()` — flow-within-a-flow).

- **Deterministic `task_id`**: `f"{family}_{summary}_{md5(sorted_params_json)[:10]}"`. Many
  tests hard-code ids like `Task1__99914b932b`. If you touch `task_id_str`, expect those to
  break — keep the algorithm stable unless intentionally rebaselining.
  `task_id.split('_')[0]` must equal the task family (directory convention in `_getpath`).

- **Instance memoization is load-bearing.** `Register.__call__` (core.py) caches instances by
  `(class, serialized-params)`, so `Task(**same_params)` returns the *same* object and `__init__`
  runs only on the first call. `Workflow` relies on this: it sets per-flow `path`/`flows` by
  *mutating* a task instance, then retrieves the same instance later via `outputPath`/`FlowExport`.
  `path`/`flows` are NOT Parameters, so they don't ride through `clone()` — the cache is the only
  thing that carries them to upstream tasks. Don't remove it without redesigning that propagation.

- **In-memory cache + mutation gotcha.** `TaskCache`/`CacheTarget.load()` returns the cached
  object *by reference*. Mutating a loaded input in place corrupts upstream cached data. This is
  expected behavior, not a bug.

- **Params.** Only the trimmed set in `parameter.py` exists (`Parameter`, `Int/Float/Bool/Date/
  Dict/List/Choice/Enum`). `ChoiceParameter(choices=[...])` is string-native and validates in
  `normalize` (fail-fast at construction / default resolution) — no `enum.Enum` needed. `significant=False` is excluded from `task_id` (so two tasks differing only
  in an insignificant param share an id but are distinct cached instances). Dict/List values are
  stored raw (not frozen) and serialized with sorted keys for id determinism.

- **Stable contract for d6tflow2** (a separate downstream repo): keep `tasks.TaskData`/
  `TaskPqPandas`/`TaskAggregator`, `targets.DataTarget`/`_LocalPathTarget`/`Target`,
  `Task.to_str_params(only_significant=…)`, `clone`, `get_params`, `task_id`/`task_family`,
  and `external`/`persist` semantics stable.

## Logging (dev notes)

loguru-based, designed to a plan: `docs/todo/20260606-sys-logging.md` (read it before changing
logging). User-facing docs: `docs/docs/logging.md`. Key facts for working on it:

- **`oryxflow/log.py` owns everything.** It holds the single `logger`, calls
  `logger.disable("oryxflow")` at import (library pattern: silent until the app opts in), and
  exposes `enable_logging(level=None, sink=sys.stderr, colorize=None)` / `disable_logging()`
  (`colorize=None` auto-detects: ANSI only when the sink is a TTY, so redirected/captured runs
  are clean; pass `True`/`False` to force it) (re-exported from
  `__init__.py` as `oryxflow.enable_logging` etc.). Every other module does
  `from oryxflow.log import logger` so records stay in the `oryxflow.*` namespace.
- **Namespace gating is by *caller module name*, not the logger object.** `logger.disable/enable`
  and the sink's `filter="oryxflow"` both key off `record["name"]`, which loguru derives from the
  emitting frame. That's why engine logs (emitted inside `core.py`/`tasks/__init__.py`) are
  governed correctly, but a task author's `self.logger.info()` — emitted from *their* module —
  would NOT be. Hence `TaskLogger` (in `log.py`): a thin facade that `__getattr__`-delegates
  every loguru method but wraps the call in a closure *defined in log.py*, so the actual loguru
  call's frame name is `oryxflow.*` and the record is gated like the rest. It also `logger.patch`es
  the display name to `oryxflow.task` and pre-binds `task_id`/`task_family` into `extra`
  (`bind()` returns a `TaskLogger` so the wrapping survives added context). `Task.logger`
  (core.py) returns a cached `TaskLogger`. **Don't** replace it with a plain `logger.bind(...)`
  or have `__getattr__` return the loguru method directly — then the emit runs in the *caller's*
  frame, gating silently breaks (records leak via any catch-all handler and ignore the on/off
  switch).
- **`enable_logging` removes loguru's default handler (id 0).** loguru ships a pristine
  unfiltered stderr handler at DEBUG; without removing it, enabling would (a) double-print every
  record and (b) ignore `level=` (id 0 shows DEBUG regardless). So with the default
  `sink=stderr`, `enable_logging` drops its previously-added sink *and* handler 0, then adds one
  filtered handler. `sink=None` touches no handlers (for apps that configured their own loguru).
  Module global `_handler_id` tracks the sink so repeat calls replace instead of stack.
- **Default level** comes from `settings.log_level` (`'INFO'`) via a *lazy* import inside
  `enable_logging` (top-level `log.py`→`settings` would be circular: settings→core→log).
- **Failure path** (see plan §3b): `build()` no longer does `traceback.print_exc()`; it logs the
  traceback via `logger.opt(exception=True).error(...)` and records the first exception on
  `RunResult.first_exception`; `__init__.py:run()` does `raise RuntimeError(...) from
  result.first_exception` so the propagated stack is one connected chain.
- **Level taxonomy:** INFO = task start/complete(+duration)/failure/run-summary/invalidation;
  DEBUG = cached-skips + save/load/input I/O (keys) + generator yields; WARNING = external task
  missing output; ERROR = task `run()` raised. Keep routine I/O at DEBUG so default INFO stays
  quiet.
- **Tests are unaffected** because logging is disabled by default and tests don't capture
  stderr — adding log points won't move the 73-passing baseline. To assert on log output, attach
  a loguru sink that appends to a list (loguru doesn't use stdlib `logging`, so pytest `caplog`
  won't see it).

## Plans (design notes you can execute from a clean session)

Non-trivial work is planned first, and the plan is saved **in the repo** at
`docs/todo/<YYYYMMDD>-<area>-<topic>.md` (e.g. `docs/todo/20260606-sys-logging.md`,
`20260606-sys-decouple-luigi.md`). `<area>` is a short tag like `sys`, `engine`, `tasks`.

These plans double as the architecture record (the "Architecture notes that bite" section above
points at them) **and** as executable specs. The intended workflow is: write the plan → clear
Claude Code (`/clear`) → in the fresh session say "execute `docs/todo/<file>.md`". Because the
new session has **no memory of the planning conversation**, the plan file must be completely
self-contained.

A plan file MUST contain, in order:

1. **`## Context` — the WHY.** The problem or need, what prompted it, and the intended outcome.
   Write it so someone with zero prior context understands *why this is worth doing* before any
   how. Include the current broken/limiting behavior (quote real output/errors where it helps).
2. **`### Design decisions`** — the choices made and *why* (and notably what was rejected), so the
   executor doesn't relitigate them. Mark anything the user explicitly confirmed.
3. **`## Implementation`** — numbered, ordered steps. Each step names the **exact file** and the
   function/seam (e.g. ``core.py:493`` `build()` before `task.run()`), shows the code or precise
   change, and is concrete enough to apply without guessing. For a pattern repeated across files,
   describe it once and list representative paths.
4. **`## Files modified`** — the full list, one line each, with what changes in each.
5. **`## Verification`** — exactly how to prove it works end-to-end: commands to run, expected
   output, and the test baseline to hold (see "Running tests" — currently **73 passing**).

Keep it scannable but complete: enough that a clean session can execute it faithfully, including
re-deriving the goal. Don't reference "the conversation" or "as discussed" — inline everything.

**When a plan is implemented:**

- Leave the file in `docs/todo/` as the design record.
- If the implementation diverged from the plan (different approach, a fix the plan didn't
  anticipate, a rejected step), append an **`## Implementation notes (divergences from the plan
  as built)`** section to the plan file capturing *what* changed and *why* — so the file stays a
  truthful design record, not a stale spec. `docs/todo/20260606-sys-logging.md` is the worked
  example (its addendum records the `TaskLogger` facade and the default-handler removal that the
  original plan missed).
- **Commit the plan file in the same commit as the code it describes** (and its divergence notes
  with the code that caused them), so the design record and the implementation never drift apart
  in history.

## Running tests

From the repo root (paths resolve `data/` → `tests/data/`):

```bash
python -m pytest tests/test_main.py tests/test_workflow.py \
    tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
```

Only `test_*.py` are collected (see `tests/setup.md` for what each covers). A benign
`UserWarning: datatable failed` and sklearn convergence warnings are expected. Current
baseline: **73 passing**. Needs `pandas`, `pyarrow`, `openpyxl`, `scikit-learn`, `jinja2`,
`tables`; `datatable` is optional (soft-fails).

## Conventions

- **User docs (`docs/docs/*.md`) are written for data scientists, not library developers.**
  They were deliberately rewritten from a user-benefits perspective (commit 316a9fb): say what
  the reader gets and what to type ("lists each distinct warning once, so its length answers
  'how many pending warnings do I have?'"), never how it's computed. No jargon or library
  internals ("deduped message set", "enumeration root", "record schema") — internals belong in
  code comments, `docs/todo/` plans, and contributor-facing docstrings. The CLAUDE.md snippet /
  plugin skill text is agent-facing and may be more technical, but still favors verbs over
  mechanisms.
- Platform is Windows (PowerShell); tests compare against `pathlib.Path(...)` (not raw strings)
  so they pass cross-OS. Keep new path assertions OS-agnostic.
- Match surrounding style; the codebase is plain, comment-light, no type annotations.
- When changing `FlowExport`'s generated-file template, update the exact expected strings in
  `tests/test_workflow.py::TestFlowExports` (they assert generated text byte-for-byte).
- Don't commit/push unless asked. Default branch is `main`; feature work is on `decouple*`.
- **Multi-line strings: match the here-string syntax to the shell tool you're actually calling.**
  The Bash and PowerShell tools use different syntaxes — don't mix them. For a multi-line commit
  message: in the **Bash** tool use a quoted heredoc (`git commit -F - <<'EOF' … EOF`, or
  `-m $'line1\nline2'`); in the **PowerShell** tool use a here-string (`@'…'@`, closing `'@` at
  column 0). Crossing them silently corrupts the message (e.g. `@'…'@` in bash leaks literal `@`
  lines). When unsure, write the message to a temp file and `git commit -F`.
