# oryxflow: Hierarchical iterate-and-aggregate — native DAG aggregator + concat helpers

## Scope & execution notes (read first)

- **Target repo to edit: the oryxflow library at `the oryxflow repo`.** All file
  paths below (`oryxflow/…`, `tests/…`, `docs/…`) are relative to that repo.
- **`the consumer project` is a separate downstream CONSUMER repo, used
  in this plan only as a real-world validation example. Do NOT edit it.** Its migration (below)
  is illustrative of what the library changes unlock; it is not part of this task.
- **First implementation action:** save this plan file verbatim into the oryxflow repo at
  `docs/todo/20260701-engine-hierarchical-aggregate.md` (repo convention: the plan is the design
  record, committed in the same commit as the code it describes). Step 8's roadmap section is
  part of that record.
- Line/`:NN` references are as of this writing; if they have drifted, re-locate by the named
  symbol (function/class), which is stable.
- Test baseline to hold: **73 passing**. Run from repo root:
  `python -m pytest tests/test_main.py tests/test_workflow.py tests/test_workflowMulti.py
  tests/test_workflowMulti2.py -q`. Needs `pandas`, `pyarrow`, `openpyxl`, `scikit-learn`,
  `jinja2`, `tables` (a `UserWarning: datatable failed` is benign).
- Style: plain, comment-light, no type annotations; params only from `oryxflow/parameter.py`;
  docs standardize on `self.inputLoad()`/`self.inputLoadConcat()` and `persists` (plural).
  Keep stable-contract surfaces unchanged: `tasks.TaskData`/`TaskAggregator`, `targets.*`,
  `Task.to_str_params`, `clone`, `get_params`, `task_id`, `external`/`persist`.

## Context

The user builds hierarchical iterate-and-aggregate pipelines: per-**state** tasks (param
`state`) feed an aggregating **country** task (param `country`) that combines all of a
country's states; further country/city tasks run downstream and aggregate again; the whole
thing runs for many countries via `WorkflowMulti` over a country grid.

Today the "combine all states" step is done with **flow-within-flow**: the country task's
`run()` builds and runs a nested `Workflow`. `core.build()` is deliberately re-entrant
(core.py:519), so it works — but it has three problems:

1. **Summary blind spot.** The nested `build()` gets its own local `RunResult`
   (`ran`/`already_complete`/`failed` are local per call, core.py:531-534). The outer
   `RunResult` never sees the state tasks, so the main execution summary can't monitor them.
2. **Reset doesn't cascade.** `reset_upstream`/`reset_downstream` walk only `requires()`
   edges (`utils.traverse` / `core.dfs_paths`). Subflow tasks are created inside `run()`, on
   no edge, so central reset from `run.py` can't reach them — the user has to hand-track
   resets inside the calling task. (2a: they also occasionally need `reset_upstream`/
   `reset_downstream` on those, not just a plain reset.)
3. **Repeated boilerplate.** Every aggregator hand-writes `pd.concat` + "tag each frame with
   its param values" so the groupby keys (state/country) survive.

**Decision (team recommendation): native DAG aggregator over first-class nested subflows.**
The country task's `requires()` returns a **dict of per-state task instances**, making the
whole hierarchy **one DAG in one `build()` call**. This fixes problems 1 and 2 *for free* —
no engine change — because DAG edges are already the single source of truth for tracking and
reset. Problem 3 is solved with a small, reusable concat helper. Runtime-discovered fan-out
(state list only known after an upstream task runs) is **out of scope** (stage-2 roadmap).

### Why native aggregator, not nested-subflow machinery

| Dimension | Native aggregator (chosen) | First-class nested subflows |
|---|---|---|
| Engine change | **None** — pure ergonomics | Subflow registry + `RunResult`-merge protocol + reset that descends into subflows; invasive edits to `build`, `RunResult`, invalidate machinery |
| Core invariant "DAG edges = single source of truth for tracking+reset" | **Strengthened** — everything stays on `requires()` edges | **Violated** — hides tasks off the edge set (the very cause of problems 1 & 2) |
| Backward-compat | Additive only; no stable-contract surface changes | Touches load-bearing `build`/`RunResult`/`flows` plumbing flagged fragile in the arch notes |
| Maintainability | Concat logic in one place; two thin adapters | Two parallel notions of "child work" every future change must handle |
| Cost | State set must be enumerable up front (accepted scope) | Only justified by runtime-discovered fan-out (deferred) |

### Verified against the source (do not relitigate)

- `core.flatten` flattens **dict values** (core.py:49-52) → `{'CA': StateTask(...), 'NY': ...}`
  flattens to `[StateTask(CA), StateTask(NY)]`.
- `utils.traverse` recurses `flatten(t.requires())` (utils.py:53) → `taskflow_upstream` →
  `invalidate_upstream` resets every per-state task. **Problem 2 solved.**
- `build._process` iterates `flatten(task.requires())` (core.py:549) → state tasks land in the
  single `RunResult`. **Problem 1 solved.** `find_deps`/`dfs_paths` use
  `set(flatten(task.requires()))` (core.py:419,429) → `reset_downstream` cascades too (2a).
- `inputLoad()` on dict-requires returns a **dict keyed by requires keys** — the list-conversion
  guard at tasks/__init__.py:180 is false for the `task=None`/`as_dict=False` dict case
  (confirmed personally + test_main.py:148-153).
- Raw significant param values: `get_param_names()` defaults to **significant-only**
  (core.py:164-166, verified) and `getattr(task, name)` returns the **raw** value
  (`to_str_params` would give serialized strings — wrong for numeric groupby keys).
- `WorkflowMulti.params[flow]` holds **raw** `{param: value}` dicts (`__init__.py:574-588`).

## Real migration this enables (consumer-project, the motivating case)

`the consumer project` — `tasks.py:1066` `RunAllModelPredictCurrentProd`
iterates `cfg.sectors`, builds a `oryxflow.Workflow(ModelPredictCurrentPresentation, params, env='prod')`
per sector, calls `flow2.reset(ReturnsBenchmarkAll); flow2.reset(FeaturesSource1)` *inside the task*,
runs it, and manually `pd.concat`s `df_recommend` across sectors. Exhibits all three problems.

**Before → after (in the consumer-project repo, once the library changes below ship):**

```python
# BEFORE: flow-within-flow, reset buried in the task, custom loop + pd.concat
class RunAllModelPredictCurrentProd(oryxflow.tasks.TaskExcelPandas):
    period_current = oryxflow.Parameter()
    def run(self):
        params = dict(env='prod', alpha=False, transformx='chg_yoy_rank', transformy='rankpct',
                      model='rf', regularize=True, strategy='ls', period_current=self.period_current)
        dfl = []
        for sector in cfg.sectors:
            params['sector'] = sector; params['nyears'] = 2 if sector=='Industrial' else 3
            flow2 = oryxflow.Workflow(ModelPredictCurrentPresentation, params, env='prod')
            flow2.reset(ReturnsBenchmarkAll); flow2.reset(FeaturesSource1)   # <- hardcoded reset
            flow2.run()
            dfl.append(flow2.outputLoad(ModelPredictCurrentPresentation)[0])
        self.save(pd.concat(dfl).drop(columns=['wgt','trade_side']))

# AFTER: native aggregator; reset moves OUT to run_output.py
class RunAllModelPredictCurrentProd(oryxflow.tasks.TaskExcelPandas):
    period_current = oryxflow.Parameter()
    def requires(self):
        base = dict(env='prod', alpha=False, transformx='chg_yoy_rank', transformy='rankpct',
                    model='rf', regularize=True, strategy='ls', period_current=self.period_current)
        return {s: ModelPredictCurrentPresentation(**{**base, 'sector': s,
                    'nyears': 2 if s=='Industrial' else 3}) for s in cfg.sectors}
    def run(self):
        df = self.inputLoadConcat(keys='all')      # 'all' = first of persist=['all','highlight','compare']
        self.save(df.drop(columns=['wgt','trade_side']))

# run_output.py — reset is now central, toggled where prod is run:
flow = oryxflow.Workflow(tasks.RunAllModelPredictCurrentProd, {'period_current':'2025Q4'}, env='prod')
flow.reset_upstream(tasks.RunAllModelPredictCurrentProd,
                    only=[tasks.ReturnsBenchmarkAll, tasks.FeaturesSource1])   # list of families
flow.run()
```

Notes validated against the consumer-project code: `ModelPredictCurrentPresentation.persist =
['all','highlight','compare']` (tasks.py:994) → `keys='all'` selects `df_recommend`.
`ReturnsBenchmarkAll` is the shared benchmark (no sector param) — `taskflow_upstream` dedups by
identity so `only=` resets it **once**, not once per sector (fixes the run.py:45 "1x for all
sectors" complaint). `env='prod'` still reaches the whole DAG via the existing
`_attach_to_tasks`/instance-cache propagation because `requires()` now puts every sector task on
the DAG. `requires()` must pass the same full param set `flow2` did.

## Worked example (minimal, for docs)

`cfg.py` holds the enumeration: `STATES = {'US': {'CT','NY'}, 'UK': {'London','Belfast'}}`.

```python
# tasks.py
import oryxflow, cfg, pandas as pd

class DataLoadState(oryxflow.tasks.TaskPqPandas):
    country = oryxflow.Parameter(); state = oryxflow.Parameter()
    def run(self): self.save(fetch_raw(self.country, self.state))

@oryxflow.requires(DataLoadState)                 # copies country+state params, wires requires()
class ProcessState(oryxflow.tasks.TaskPqPandas):
    def run(self): self.save(self.inputLoad().assign(processed=1))

class Country(oryxflow.tasks.TaskPqPandas):
    country = oryxflow.Parameter()
    def requires(self):
        return {s: ProcessState(country=self.country, state=s) for s in cfg.STATES[self.country]}
    def run(self): self.save(self.inputLoadConcat())   # stacks states, keeps state/country cols

# run.py
flow = oryxflow.WorkflowMulti(Country, params={'country': list(cfg.STATES)})
flow.run()
dfall = flow.outputLoadConcat(Country)             # all countries, one frame, tagged by country
```

Central reset of one family across every state/country (the problem-2 win):

```python
flow.reset_upstream(Country, only=DataLoadState)   # only DataLoadState instances everywhere;
flow.run()                                         # ProcessState/Country auto-recompute
# flow.reset_upstream(Country)                     # or reset the whole upstream (no `only=`)
```

The `only=` filter (Step 3b) enumerates `US/CT, US/NY, UK/London, UK/Belfast` via the DAG — no
hand-listing. With the old flow-within-flow these were invisible to reset; that is the fix.

## Implementation

### Step 1 — Shared concat core: `oryxflow/utils.py`

Add at end of file (import pandas lazily, matching `apply_noise`/`to_parquet` local imports).

```python
def concat_iter(items, concat_fn=None, keys=None, ignore_index=True):
    """Stack an iterable of (identifier, params, data) triples into one DataFrame.
    params: dict of raw values -> added as columns by default (groupby keys survive).
    data: a single DataFrame, or a list/dict of DataFrames (multi-persists).
    concat_fn(identifier, params, df)->df: hook called per frame instead of default tagging.
    keys: subset of param names to tag (default all)."""
    import pandas as pd
    frames = []
    for identifier, params, data in items:
        subframes = list(data.values()) if isinstance(data, dict) \
            else list(data) if isinstance(data, (list, tuple)) else [data]
        params = params or {}
        for df in subframes:
            if concat_fn is not None:
                df = concat_fn(identifier, params, df)
            else:
                df = df.copy()                       # avoid mutating cached inputs
                tagcols = params if keys is None else {k: params[k] for k in keys if k in params}
                for col, val in tagcols.items():
                    df[col] = val
            frames.append(df)
    return pd.concat(frames, ignore_index=ignore_index) if frames else pd.DataFrame()
```

`df.copy()` respects the in-memory-cache mutation gotcha; with `concat_fn` the caller owns
copy semantics.

### Step 2 — `TaskData.inputLoadConcat(...)`: `oryxflow/tasks/__init__.py`

Add right after `inputLoad` (ends line 199). The in-task aggregation one-liner.

```python
def inputLoadConcat(self, keys=None, tag=True, tagkeys=None, as_dict=False,
                    concat_fn=None, cached=False):
    """Load every dependency and concatenate into one DataFrame. Works for the dict form of
    requires() ({key: Task(...)}) and the list/positional form. By default each dependency's
    significant params are added as columns. concat_fn(identifier, params, df)->df overrides."""
    requires = self.requires()
    if isinstance(requires, dict):
        items = list(requires.items())        # (key, task)
    elif isinstance(requires, (list, tuple)):
        items = list(enumerate(requires))     # (index, task)
    else:
        items = [(None, requires)]            # single dep
    def _gen():
        for ident, dep in items:
            data = self.inputLoad(keys=keys, task=ident, as_dict=as_dict, cached=cached)
            params = {n: getattr(dep, n) for n in dep.get_param_names()} if tag else {}
            yield ident, params, data
    import oryxflow.utils
    return oryxflow.utils.concat_iter(_gen(), concat_fn=concat_fn, keys=tagkeys)
```

`inputLoad(task=ident)` indexes `self.input()[ident]` — a dict key for dict-requires, an int
for positional-requires, whole input for the single-dep `ident=None` case.

### Step 3 — `WorkflowMulti.outputLoadConcat(...)`: `oryxflow/__init__.py`

Add after `WorkflowMulti.outputLoadAll` (ends line 695). **New method, not a `concat=` kwarg** —
overloading `outputLoad`'s return shape (sometimes dict, sometimes DataFrame) is a footgun and
would touch a stable-contract surface; a distinct method keeps `outputLoad` stable and concat
semantics explicit.

```python
def outputLoadConcat(self, task=None, keys=None, as_dict=False, cached=False,
                     concat_fn=None, tagkeys=None):
    """Load `task` output for every flow and concatenate into one DataFrame,
    tagging each flow's rows with that flow's raw params."""
    per_flow = self.outputLoad(task=task, keys=keys, as_dict=as_dict, cached=cached)
    items = ((flow, self.params[flow], per_flow[flow]) for flow in self.params.keys())
    return oryxflow.utils.concat_iter(items, concat_fn=concat_fn, keys=tagkeys)
```

**Do not** add concat to `Workflow.outputLoadAll` — it stacks *different* task families (keyed
by class name), whose schemas differ, so row-stacking is meaningless. The two aggregation
points the user needs are covered: within-DAG by `inputLoadConcat` (Step 2), across-flows by
`outputLoadConcat` (Step 3).

### Step 3b — Family-targeted central reset: `only=` filter + a bug fix

This is what lets a user centrally reset **one task family** (e.g. `DataLoadState`) whose param
space (`state`) is *internal* to the DAG — the exact pain in problem 2. Because
`check_dependencies=True` (settings.py:11) makes `complete()` recursive (tasks/__init__.py:96-99),
invalidating just `DataLoadState` auto-forces `ProcessState`/`Country` to recompute — no manual
downstream bookkeeping.

**`invalidate_upstream(task, confirm=False, only=None)`** (`oryxflow/__init__.py:221`): after
`tasks = taskflow_upstream(task, only_complete=False)`, filter by family:

```python
if only is not None:
    only = only if isinstance(only, (list, tuple, set)) else (only,)
    tasks = [t for t in tasks if isinstance(t, tuple(only))]
```

Thread `only=` through **`Workflow.reset_upstream(task, confirm=False, only=None)`**
(`__init__.py:514`) and **`WorkflowMulti.reset_upstream(task=None, flow=None, confirm=False,
only=None)`** (`__init__.py:736`, pass `only=only` into both the per-flow fan-out and the
single-flow branch). Optional symmetric `only=` on `invalidate_downstream`/`reset_downstream`
for parity (nice-to-have).

**Bug fix (found while tracing this):** the single-flow branches of
`WorkflowMulti.reset_downstream` (`__init__.py:728`) and `reset_upstream` (`__init__.py:738`)
mistakenly call `self.workflow_objs[flow].reset(task, confirm)` — the *plain* reset — instead
of `.reset_downstream(...)` / `.reset_upstream(...)`. Fix each to call the matching variant.
Harmless on the all-flows path; wrong when a single `flow=` is targeted. Add a regression test.

### Step 4 — Repurpose the `runIterConcat` stub: `oryxflow/__init__.py:971`

It's currently a verbatim copy of `runLoad` — no iteration, no concat, no callers, no tests.
Repurpose to the `WorkflowMulti` grid + `outputLoadConcat` one-liner (safe: nothing depends
on it):

```python
def runIterConcat(task, params, load=True, taskLoad=None, reset=False,
                  concat_fn=None, tagkeys=None):
    """Run `task` across a grid of params (one flow per param set) and return the
    per-flow outputs concatenated into one DataFrame, each flow tagged with its params."""
    taskLoad = task if taskLoad is None else taskLoad
    flow = oryxflow.WorkflowMulti(task, params)
    if reset:
        flow.reset(task)
    flow.run()
    return flow.outputLoadConcat(taskLoad, concat_fn=concat_fn, tagkeys=tagkeys) if load else flow
```

### Step 5 — requires-dict idiom (no new required helper)

`params_generator_single`/`_df` produce `{i: {param: v}}` for `WorkflowMulti`, **not** a
`{tag: Task(...)}` requires dict. Prefer the documented house-style dict comprehension
(advtasksdyn.rst:20 already shows this shape):

```python
def requires(self):
    return {s: StateTask(country=self.country, state=s)
            for s in STATES_BY_COUNTRY[self.country]}
```

Optionally add trivial sugar `utils.requires_grid(task_cls, param, values, **base)` →
`{v: task_cls(**{param: v}, **base) for v in values}` — nice-to-have, not required.

### Step 6 — Docs

- `docs/source/advtasksdyn.rst` — under "Fixed Dynamic" (after line 26), add a "Hierarchical
  iterate-and-aggregate" subsection: the `StateTask`/`CountryTask` native-aggregator example
  using `self.inputLoadConcat()`, explicitly calling out the three wins (one DAG → preview
  shows every state, summary lists them, `reset_upstream`/`reset_downstream(StateTask)`
  cascade) as the reason to migrate off flow-within-flow.
- `docs/source/workflow.rst` — under "Operations on multi experiment workflow" (after line
  137), add `flow.outputLoadConcat(CountryTask)` and `oryxflow.runIterConcat(CountryTask,
  params={'country': [...]})`.
- Keep task bodies on `self.inputLoad()` / `self.inputLoadConcat()` and `persists` (plural).

### Step 7 — Tests (hold the 73-passing baseline)

`tests/test_main.py` (near `test_dynamic` and the multi-dep inputLoad tests, use
`TaskCache`/small frames like neighbours):
- `test_concat_iter_default` (unit): `concat_iter` with `(id, {'state': s}, df)` triples →
  assert `state` column + `len == sum(len)`; add a `data=dict` (multi-persist) case and a
  `concat_fn` case (hook output used, default tagging skipped).
- `test_inputLoadConcat_dict` (DAG): `CountryTask.requires` → `{s: StateTask(state=s)}` for
  `['CA','NY']`; `run` does `self.save(self.inputLoadConcat())`; assert
  `set(df['state'])=={'CA','NY'}` and row count.
- `test_inputLoadConcat_list` (DAG): positional `@oryxflow.requires(A, B)` form still stacks
  and tags from each dep instance.
- `test_aggregator_reset_cascades` (**regression guard for problem 2**): run the DAG, assert
  all `StateTask` complete; `invalidate_upstream(CountryTask())`; assert every `StateTask`
  incomplete.
- `test_reset_upstream_only` (family filter): after a run, `flow.reset_upstream(CountryTask,
  only=DataLoadState)` invalidates only the `DataLoadState` instances (assert those incomplete,
  a sibling family still complete), and a rerun recomputes downstream (recursive `complete()`).
- `test_workflowMulti_reset_single_flow` (bug-fix guard): `reset_upstream(task, flow=<one>)`
  actually invalidates that flow's *upstream* (not just the single task).

`tests/test_workflowMulti.py`:
- `test_outputLoadConcat` / `test_runIterConcat`: `WorkflowMulti(CountryTask,
  params={'country':[...]})` → `.run()` → concat; assert `country` column has all flow values
  and correct row count.

### Step 8 — Stage-2 roadmap note (DO NOT BUILD NOW)

This section lives inside the design record created in "Scope & execution notes" above
(`docs/todo/20260701-engine-hierarchical-aggregate.md` = this plan). It documents the
native-aggregator decision and sketches the deferred runtime-discovered path:
`TaskAggregator`'s generator/yield mode (`build._drive_generator`, core.py:591-607) already
records yielded tasks into the current `RunResult` (summary works), but they sit on no static
edge so reset can't follow them. Future close: record generator-yielded reqs onto the driving
task inside `_drive_generator`, and teach `utils.traverse`/`core.dfs_paths` to follow that
recorded set — keeping the "DAG edges = single source of truth" invariant. No
subflow/RunResult-merge machinery needed.

## Files modified

- `oryxflow/utils.py` — add `concat_iter(...)`; optional `requires_grid(...)`.
- `oryxflow/tasks/__init__.py` — add `TaskData.inputLoadConcat(...)` after `inputLoad`.
- `oryxflow/__init__.py` — add `WorkflowMulti.outputLoadConcat(...)`; repurpose `runIterConcat`
  (line 971); add `only=` filter to `invalidate_upstream` + `Workflow`/`WorkflowMulti.reset_upstream`;
  fix the single-flow `WorkflowMulti.reset_downstream`/`reset_upstream` copy/paste bug (lines 728,738).
- `docs/source/advtasksdyn.rst`, `docs/source/workflow.rst` — hierarchical aggregator +
  concat examples.
- `tests/test_main.py`, `tests/test_workflowMulti.py` — new tests (Step 7).
- `docs/todo/20260701-engine-hierarchical-aggregate.md` — design record + stage-2 roadmap.
- **No change** to `core.py`, `parameter.py`, `targets/*` — the engine already supports
  dict-requires.

## Verification

- `python -m pytest tests/test_main.py tests/test_workflow.py tests/test_workflowMulti.py
  tests/test_workflowMulti2.py -q` → **73 prior tests still pass**, plus the new
  concat/aggregator tests.
- Manual end-to-end: build a 2-country / 2-state `CountryTask` DAG.
  `oryxflow.preview(...)` shows all state tasks; `oryxflow.run(...)` summary lists them;
  `flow.reset_upstream(CountryTask)` leaves every `StateTask` incomplete (the old
  flow-within-flow blind spot, now closed); `flow.reset_upstream(CountryTask,
  only=DataLoadState)` leaves only `DataLoadState` incomplete, and a rerun recomputes
  `ProcessState`/`CountryTask` (recursive `complete()`).

## Implementation notes (divergences from the plan as built)

- **`WorkflowMulti.__init__` single-key grid fix (not in the original plan).** The plan's
  canonical example and Steps 3/4 pass `params={'country': [...]}` (a single-key dict whose
  value is a list). `__init__` routed *any* dict-of-list to `utils.generate_exps_for_multi_param`,
  which assumes ≥2 keys and raised `IndexError: list index out of range` on a single key. Added a
  branch: single-key dict-of-list now goes to `utils.params_generator_single` (one flow per value,
  `self.params[i] = {param: value}`), multi-key still uses the cartesian generator. This is what
  makes `WorkflowMulti(Country, params={'country': [...]})`, `outputLoadConcat`, and
  `runIterConcat` work at all; `self.params[flow]` still holds raw `{param: value}` dicts so the
  concat tagging is unchanged.
- **`WorkflowMulti.reset_downstream` signature.** Beyond the copy/paste bug fix (it called the
  plain `.reset(task, confirm)` instead of `.reset_downstream(...)`), it also never forwarded a
  downstream target. Added a `task_downstream=None` parameter and threaded it through both the
  single-flow and all-flows branches so `reset_downstream` is actually usable on a `WorkflowMulti`.
- **`utils.requires_grid` sugar added** (the optional Step 5 helper):
  `requires_grid(task_cls, param, values, **base)` → `{v: task_cls(**{param: v}, **base) for v in values}`.
- Test count: 73 baseline + 8 new = **81 passing**.

## Follow-up: family-aware `reset_downstream` (added after initial ship)

`Workflow.reset_downstream(task, task_downstream)` instantiated the *start* task via
`get_task(task)` purely to read its `.task_family`, which raised `MissingParameterException` for
families whose params are internal to the DAG (e.g. `CountryFeatures` needs `country`, absent
from the flow's params). But `invalidate_downstream` only ever uses `task.task_family`, and
`task_family` is a **class-level** property (core.py metaclass), so the instantiation was
unnecessary.

Fix (`__init__.py` `Workflow.reset_downstream`): pass `task` straight through to
`invalidate_downstream` without instantiating it. Now `flow.reset_downstream(CountryFeatures)`
works — `task_downstream` defaults to the flow's default task (the terminal), and
`core.dfs_paths`/`find_deps` already discover the whole band by family. This gives a
**cascade-independent** downstream reset (every task on the paths is invalidated explicitly), the
complement to `reset_upstream(root, only=Family)` which resets one family and relies on recursive
`complete()` to recompute downstream — the latter silently does nothing when
`check_dependencies=False` because the run target short-circuits the traversal.

Tests: `test_reset_downstream_family`, `test_reset_upstream_full_and_only`,
`test_reset_upstream_default_anchor`, `test_reset_downstream_single_flow`, plus a strengthened
`test_reset_downstream_multi`. Baseline now **93 passing**.

## Follow-up: `reset_downstream` accepts a family list

Extended `invalidate_downstream(task, ...)` to accept a list of families (union of each family's
downstream band), so `flow.reset_downstream([FamilyA, FamilyB])` resets several non-contiguous
families and everything downstream of each in one explicit call — the downstream analogue of
`reset_upstream(only=[...])`. The single-or-iterable→tuple normalization is factored into a
shared `_as_families()` helper used by both `invalidate_upstream` (`only=`) and
`invalidate_downstream` (kept DRY per the symmetry). Chosen over a `cascade=`/`explicit=` flag on
`reset_upstream`, which would have duplicated `reset_downstream` and overloaded the word
"cascade" (already the recursive-`complete()` concept). Test: `test_reset_downstream_family_list`.
Baseline now **94 passing**.
