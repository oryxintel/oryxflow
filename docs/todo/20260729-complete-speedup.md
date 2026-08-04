# `complete()` is O(paths), not O(tasks) — context for a speedup plan

> **Superseded:** the executable plan written from this is
> `docs/todo/20260730-engine-traversal-scope.md` (traversal-scoped memo for `complete()` and
> `requires()` resolution; it settles the open questions below and records why the post-order
> `build()` restructure was rejected). This file stays as the investigation record.

> **This is a pre-plan, not an executable plan.** It deliberately has no `## Implementation`
> section: the fix is a correctness-sensitive change to how `build()` decides what to skip, and
> the shape should be chosen with the measurements below in hand rather than pinned down here.
> A fresh session should read this, decide the approach (see "Candidate levers" and "Open
> questions"), then write the real plan — Context / Design decisions / Implementation / Files
> modified / Verification — in a new `docs/todo/` file and execute that.

## Context

Every `oryxflow.run()`, `preview()`, and `Workflow.complete()` asks each task whether it is
already complete. On a fan-out-heavy DAG that check dominates: a downstream consumer project
reported **~24s to check 41 branches** against 75 upstream tasks, and **~95s** across four
top-level runs — before any task does any work. A no-op re-run of an already-complete flow, which
should be nearly free, is the slowest thing in an iteration cycle.

The cause is structural, not I/O. `TaskData.complete(cascade=True)`
(`oryxflow/tasks/__init__.py:116`) recurses:

```python
complete = super().complete()                      # do my outputs exist?
if complete and not external:
    complete = self._code_ok()                     # is my code fingerprint still valid?
if settings.check_dependencies and cascade and not external:
    complete = complete and all(
        [t.complete() for t in core.flatten(self.requires())])    # ... and every dep, recursively
```

and `build()._process` (`oryxflow/core.py:1066`) calls `task.complete()` once per unique task. Its
own `visited[tid]` memo prevents re-*processing* a task, but each of those N calls kicks off a
fresh full-closure cascade. So the number of `complete()` invocations is the sum of every task's
upstream closure size — the number of **paths** through the DAG, not the number of tasks. A
diamond (fan-out then combine) multiplies: the shared node upstream of 41 branches is asked 42
times.

Measured on a synthetic that reproduces the reported shape (41 branches, one shared aggregator
over 32 leaves, 75 tasks total — script below):

```
recursive complete(): 1.46s
  complete() invocations: 1,428  unique tasks: 75  redundancy: 19.0x
  worst single task: 42 calls
with a naive scoped memo: 0.48s  (3.0x)
no-op flow.run(): 1.55s
  with a naive complete() memo: 0.73s  (2.1x)
```

19.0× reproduces the consumer project's independently measured 19.1× (1435 calls / 75 tasks), so
the synthetic is faithful and can be used as the plan's benchmark.

### What has already been done — do not redo these

Committed in `684210d`, so the numbers above are *after* them:

- **`_class_source` memo** (`oryxflow/codehash.py`). Profiling showed 88.6% of `os.stat` calls
  under a recursive `complete()` came from `_class_source` → `_project_root` + `_is_local`,
  re-resolving each task class's source file and project root on every call. Memoized on
  `(cls, PROJECT_ROOT)`. Measured **4.7× on a no-op `run()`, 2.0× on a bare `complete()`**. A
  negative result (`start is None`, i.e. the module is absent from `sys.modules`) is deliberately
  **not** cached — it may be imported later and would then resolve to a real file.
- `state._load` already caches records per directory (`oryxflow/state.py:47`), so
  `state.get_record` is one dict lookup after the first read per directory. Not a lever.
- `codehash.freeze()` / `unfreeze()` already bracket the whole of `build()`
  (`oryxflow/core.py:1187`), so mtime re-stats are already suppressed for the duration of a run.

**A wrong diagnosis to not chase.** The original report attributed the per-call floor to
`_walk_cache_get` re-stat'ing every file in the import closure on each call. That is not it —
`freeze()` already covers `build()`, proven by identical `os.stat` counts frozen vs unfrozen (see
`bench_complete.py` shape below: 8,244 frozen vs 13,694 unfrozen for 41 bare `complete()` calls
*outside* a run, but a run is always frozen). The remaining cost is call **count**.

## Candidate levers

Roughly in increasing order of risk and payoff. Not mutually exclusive.

1. **Scope-memoize `complete()` for the duration of one traversal** (the "`complete_scope`" idea).
   Upper bound measured above: **3.0× on a recursive `complete()`, 2.1× on a no-op `run()`.`**
   Shape would mirror the existing `codehash.freeze()`/`unfreeze()` pair — a context manager in
   `core.py` that `TaskData.complete` consults.

   **The hazard that makes this non-trivial:** inside `build()`, completeness genuinely *changes*
   as tasks run. `freeze()` is safe because code cannot change mid-build; output existence can and
   does. A naive memo held across a whole `build()` is wrong — it is only correct for the pure
   read-only traversals (`preview()`, `Workflow.complete()`, the `invalidate_*` filters) and for a
   no-op run where nothing happens to run. Any real fix must either invalidate the memo when a
   task runs (and know which entries that touches — everything downstream of it), or scope the
   memo to a single `_process` call rather than the whole build.

2. **Restructure `build()._process` to post-order.** The redundancy exists because `_process`
   asks "is this task complete?" (a question that recurses over the whole closure) *before*
   descending into deps. If it descended first and then asked `complete(cascade=False)` plus "did
   any dep just run", each task's own completeness would be evaluated exactly once and the cascade
   would be unnecessary inside `build()` altogether — redundancy becomes structurally impossible
   rather than memoized away. This is the principled fix and the riskiest: it changes skip
   semantics, and must preserve `external=True` handling, generator `run()` dynamic yields, the
   `already_complete` / `ran` accounting that `RunResult` reports, and the interaction with
   `_code_ok`'s `_dep_state` fold (which is a *second*, record-based mechanism for "a dependency
   rematerialized" — see Open questions).

3. **Memoize `requires()` resolution per instance.** An independent, unmeasured-but-visible cost:
   the same benchmark resolves `requires()` **586 times for 75 tasks** — the shared aggregator 294
   times, each call re-running `_resolve_requires` → `requires_grid` → 32 `clone()` calls (~9,400
   clones for one no-op run). Every level of every cascade rebuilds its dependency dict. Cheaper
   and lower-risk than 1 or 2, and it compounds with them.

   **Hazard:** `requires()` is not quite pure. Callable grid values and `derive=` callables read
   module-level config (`cfg.REGIONS`, `cfg.SOURCE`), and the documented contract is that editing
   that config and re-running your script picks up the change — true across processes, but a memo
   would freeze it *within* one process. Needs a decision on scope (per-traversal, like 1) rather
   than a permanent per-instance cache.

## Open questions for the plan to settle

- **Is `settings.check_dependencies`'s cascade still load-bearing given `_code_ok`'s `_dep_state`
  fold?** There appear to be two overlapping mechanisms for "an upstream task rematerialized, so
  my stored output is stale": the recursive `complete()` cascade, and the record-based dep-state
  fold in `_code_ok` (`oryxflow/tasks/__init__.py:131`). If the record mechanism is sufficient
  inside `build()`, lever 2 gets much simpler. If it is not, understand precisely which case needs
  the cascade before removing it. `tests/test_code_invalidation.py` is where the answer lives.
- **Which traversals get the memo?** `build()` is the hard one. `preview()` /
  `utils.print_tree` (`oryxflow/utils.py:23` calls `task.complete()` per node),
  `Workflow.complete()` (`oryxflow/__init__.py:629`), and the `invalidate_*` filters
  (`__init__.py:142`, `:190`, `:205`) are all pure reads where a plain per-call memo is safe and
  free. Shipping those first is a real user-visible win (`preview()` is interactive) with almost
  no risk, and it de-risks the `build()` work by settling the API shape first.
- **Public or internal?** Is `complete_scope` an internal optimization with no API surface, or does
  anything user-facing (a `Workflow.complete(scope=...)`, a settings flag) need to exist? Default
  assumption: internal, no new public API, no behaviour change — a settings escape hatch only if a
  correctness risk is identified that cannot be closed.
- **Does the memo key on `task_id` or on the instance?** `Register.__call__` memoizes instances by
  `(class, serialized-params)`, so the instance is already canonical — but `path`/`flows` are
  *mutated* onto instances by `Workflow` and are not parameters, so two flows share a `task_id`
  while writing to different directories. **A memo keyed on `task_id` alone is therefore wrong
  across flows** (`WorkflowMulti`, or any `attach_flow`). Key on the resolved output path, or on
  the instance, or scope the memo per flow. This is the most likely source of a subtle bug.

## Verification the plan must specify

- Baseline to hold: **184 passing** (`python -m pytest tests/ -q`). `tests/test_code_invalidation.py`
  is the one that actually exercises the completeness rules — expect it to be the gate.
- `python scripts/build_docs.py --check` (a `complete()` change can alter doc-example output).
- A before/after on the benchmark below. **Use the call counts as the primary metric, not
  wall-clock** — repeated runs of the same benchmark on Windows varied 1.46s / 1.55s / 2.03s for
  equivalent work, so a claimed sub-2× wall-clock win is inside the noise. Call counts are
  deterministic.
- Correctness cases to assert explicitly, because a memo can make all of these silently pass by
  returning a stale `True`: a task made incomplete mid-run by its dependency rerunning; a
  `code_version` bump on a shared upstream task; two flows (`WorkflowMulti`) whose tasks share a
  `task_id` but write to different directories; `external=True` tasks; a generator `run()` that
  yields new tasks mid-build.

## Benchmark (self-contained; save to a scratch dir and run from the repo root)

```python
"""How much of complete()'s cost is redundant CALLS (vs per-call work)?"""
import time, shutil, collections
import pandas as pd
import oryxflow
from oryxflow.tasks import TaskData

oryxflow.settings.log_level = 'WARNING'
oryxflow.set_dir('data-bench3/')
MARKETS = ['m{}'.format(i) for i in range(41)]        # 41 branches, as reported

class SLeaf(oryxflow.tasks.TaskPqPandas):
    i = oryxflow.IntParameter(default=0)
    def run(self): self.save(pd.DataFrame({'v': [self.i]}))

@oryxflow.requires_each(SLeaf, i=list(range(32)))     # one shared aggregator over 32 leaves
class SAgg(oryxflow.tasks.TaskPqPandas):
    def run(self): self.save(self.inputLoadConcat())

@oryxflow.requires(SAgg)
class SNarr(oryxflow.tasks.TaskPqPandas):
    market = oryxflow.Parameter(default='m0')
    def run(self): self.save(self.inputLoad().assign(market=self.market))

@oryxflow.requires({'input': SAgg})                   # the diamond: shared dep + fan-out
@oryxflow.requires_each(SNarr, market=MARKETS)
class SReport(oryxflow.tasks.TaskPqPandas):
    def run(self): self.save(self.inputLoadConcat(task='SNarr'))

flow = oryxflow.Workflow(SReport)
flow.run()                                            # make everything complete

_orig = TaskData.complete
calls = collections.Counter()

def counting(self, cascade=True):
    calls[self.task_id] += 1
    return _orig(self, cascade=cascade)

TaskData.complete = counting
t = time.perf_counter(); SReport().complete(); base = time.perf_counter() - t
TaskData.complete = _orig
total, uniq = sum(calls.values()), len(calls)
print('recursive complete(): {:.2f}s'.format(base))
print('  invocations: {:,}  unique: {}  redundancy: {:.1f}x  worst task: {}'.format(
    total, uniq, total / uniq, max(calls.values())))

# upper bound: a naive memo (correct here ONLY because nothing runs during a pure complete())
memo = {}
def memoized(self, cascade=True):
    key = (self.task_id, cascade)
    if key not in memo:
        memo[key] = _orig(self, cascade=cascade)
    return memo[key]

TaskData.complete = memoized
t = time.perf_counter(); SReport().complete(); scoped = time.perf_counter() - t
TaskData.complete = _orig
print('naive scoped memo: {:.2f}s ({:.1f}x)'.format(scoped, base / scoped))

t = time.perf_counter(); flow.run(); print('no-op run(): {:.2f}s'.format(time.perf_counter() - t))
shutil.rmtree('data-bench3/', ignore_errors=True)
```

To count `requires()` re-resolution (lever 3) instead, wrap `core._resolve_requires` — note it
must be patched in **both** places, since the generated `requires()` closes over the module
global: `core._resolve_requires = f` and
`core._spec_requires.__globals__['_resolve_requires'] = f`.
