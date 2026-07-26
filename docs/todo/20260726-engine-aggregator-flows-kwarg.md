# TaskAggregator cannot be driven by a Workflow (`unknown parameter flows`)

## Context

`TaskAggregator` — the task type whose `run()` only yields other tasks — **cannot be used with
`Workflow` or `WorkflowMulti` at all.** Every method on the flow object that resolves the task
instance raises, so an aggregator can only be driven by the module-level `oryxflow.run()`.

Reproduce it with any aggregator (no parameters, no `path`, no `env` needed):

```python
import oryxflow, pandas as pd
oryxflow.set_dir('data/')

class Task1(oryxflow.tasks.TaskCache):
    n = oryxflow.IntParameter(default=1)
    def run(self): self.save(pd.DataFrame({'a': range(self.n)}))

class Collect(oryxflow.tasks.TaskAggregator):
    def run(self):
        yield Task1(n=1)
        yield Task1(n=2)

oryxflow.run(Collect())              # works
flow = oryxflow.Workflow(Collect)    # constructing the flow also works
flow.run()                           # <-- raises
```

```text
UnknownParameterException: Collect[args=(), kwargs={'flows': {}}]: unknown parameter flows
```

Construction succeeds; **everything after it fails** with the same exception — `flow.run()`,
`flow.preview()`, `flow.get_task()`, `flow.complete()`, `flow.outputLoad()`. There is no workaround
short of abandoning the flow object, which is why `docs/docs/advtasksdyn.md` and
`tests/test_main.py::test_dynamic` both drive aggregators with module-level `oryxflow.run()`. Nothing
documents that this is a hard limitation rather than a stylistic choice, so a user who reaches for
`Workflow` with an aggregator hits an exception whose message points at a parameter they never wrote.

### Why it happens

Two mechanisms that were built for `TaskData` were never extended to `TaskAggregator`:

1. **`Workflow` passes `path`/`flows` as *constructor kwargs*.**
   `oryxflow/__init__.py:401` in `Workflow.__init__` unconditionally does:

   ```python
   self.params = dict(**self.params, **{'flows': {}})
   ```

   so `'flows'` is a permanent entry in `self.params` for **every** flow, even one with no flows
   attached. `path` lands in the same dict when `path`/`env` is set. Then
   `Workflow.get_task()` (`oryxflow/__init__.py:629`) does:

   ```python
   return task(**self.params)
   ```

   `path` and `flows` are **not** Parameters (deliberately — see
   `docs/todo/20260606-sys-param-global.md`; they don't ride through `clone()`), so handing them to
   the parameter machinery is the anomaly. It only works today because of mechanism 2.

2. **`TaskData` absorbs and filters them; `TaskAggregator` doesn't.**
   `TaskData` (`oryxflow/tasks/__init__.py:38`) declares them as keyword-only args and strips
   anything that isn't a declared parameter *before* calling `super().__init__()`:

   ```python
   def __init__(self, *args, path=None, flows=None, **kwargs):
       kwargs_ = {k: v for k, v in kwargs.items()
                  if k in self.get_param_names(include_significant=True)}
       super().__init__(*args, **kwargs_)
       self.path = getattr(self, 'path', path)
       ...
       self.flows = flows
   ```

   and it overrides `get_param_values` (`oryxflow/tasks/__init__.py:52`) with the same filter, so the
   metaclass's parameter resolution never sees the extras either.

   `TaskAggregator` (`oryxflow/tasks/__init__.py:611`) subclasses **`core.Task` directly**, not
   `TaskData`. It has neither override, so `flows={}` reaches `core.Task`'s parameter resolution and
   is rejected.

The same gap applies to any user-written bare `core.Task` subclass, not just `TaskAggregator` — an
aggregator is simply the one the library ships.

### Design decisions

- **Chosen: mirror `TaskData`'s absorption on `TaskAggregator`** (option A below). It is the smallest
  change that matches the pattern already in the file, touches one class, and cannot affect any
  `TaskData` path — so the 86-test baseline is not at risk. **This fix has been verified to work**
  (see Verification): with the two methods added, all five previously-failing flow methods pass and
  `flow.outputLoad()` returns the children's data.
- **Rejected for this change: hoist the absorption into `core.Task`** (option B). It is arguably the
  better home — one implementation instead of two near-identical copies, and it would fix every bare
  `core.Task` subclass at once, including user-written ones. It is rejected here purely on **blast
  radius**: `core.Task.__init__` / `get_param_values` sit under the entire engine, so every task in
  the test suite exercises them, whereas option A cannot touch a `TaskData` path at all. Fixing a
  reproducible exception and refactoring parameter resolution are two changes and should be two
  commits. Option B is a reasonable immediate follow-up once A has settled — there is **no external
  compatibility constraint** blocking it (breaking changes to the engine surface are acceptable;
  land them as a `BREAKING:` entry in `CHANGELOG.md` per its own conventions).
- **Rejected: filter at the injection site** (option C) — have `Workflow.get_task()` pass only
  declared parameters and rely on `_attach_to_tasks()` to set `path`/`flows` by mutation. This is the
  most *correct* framing, because `path`/`flows` are not Parameters and should never have travelled
  through the constructor. It was rejected as a bug fix because of a specific hazard:
  `Register.__call__` memoizes instances by `(class, serialized-params)` and `Workflow` **relies on
  that memo to carry the mutated `path`/`flows` to instances retrieved later** (`outputPath`,
  `FlowExport`) — see "Instance memoization is load-bearing" in the repo-root `CLAUDE.md`. Non-
  parameters aren't part of the memo key, so filtering them out would not change the key; but
  `TaskData.__init__` sets `self.path` on **first construction only** (`getattr(self, 'path', path)`
  under memoization), and `_attach_to_tasks()` is called only on the tasks handed to it, so removing
  the constructor route could leave upstream instances without a `path`. Verifying that requires
  exercising the per-flow `path`/`env` and `FlowExport` paths, which is more than this bug needs.
  **Do not attempt option C as part of this change.**
- **Also worth fixing, and cheap: the misleading error.** Even after option A, a user who passes a
  genuinely unknown kwarg to a flow gets `unknown parameter flows`-style noise pointing at an
  internal name. Out of scope here; noted at the end.

## Implementation

### 1. Absorb `path`/`flows` on `TaskAggregator`

File: `oryxflow/tasks/__init__.py`, class `TaskAggregator` (line 611).

Add the two methods below as the **first** members of the class, immediately after the docstring and
before `def reset(...)`. They are a deliberate copy of `TaskData`'s pair (lines 38 and 52) — keep the
bodies identical to those, so the two stay easy to diff and to de-duplicate later under option B:

```python
    def __init__(self, *args, path=None, flows=None, **kwargs):
        # `path`/`flows` are set by Workflow, are not Parameters, and must not reach
        # the parameter machinery -- same absorption TaskData does.
        kwargs_ = {k: v for k, v in kwargs.items(
        ) if k in self.get_param_names(include_significant=True)}
        super().__init__(*args, **kwargs_)
        self.path = getattr(self, 'path', path)
        self.flows = flows

    @classmethod
    def get_param_values(cls, params, args, kwargs):
        kwargs_ = {k: v for k, v in kwargs.items(
        ) if k in cls.get_param_names(include_significant=True)}
        return super(TaskAggregator, cls).get_param_values(params, args, kwargs_)
```

Note `super(TaskAggregator, cls)` rather than a bare `super()` — matching `TaskData`'s explicit form
(line 55), which matters because the metaclass calls `get_param_values` as a classmethod.

### 2. Add a regression test

File: `tests/test_main.py`, beside the existing `test_dynamic` (which drives an aggregator through
module-level `oryxflow.run()` — leave that test as it is; it covers the other entry point).

Add a test that fails before step 1 with `UnknownParameterException` and passes after. Cover every
method that goes through `get_task()`, because they all broke together:

```python
def test_aggregator_workflow(cleanup_files):
    class Task1Agg(oryxflow.tasks.TaskCache):
        n = oryxflow.IntParameter(default=1)
        def run(self):
            self.save(pd.DataFrame({'a': range(self.n)}))

    class CollectAgg(oryxflow.tasks.TaskAggregator):
        def run(self):
            yield Task1Agg(n=1)
            yield Task1Agg(n=2)

    flow = oryxflow.Workflow(CollectAgg)
    assert flow.get_task() is not None
    flow.preview()
    flow.run()
    assert flow.complete()
    assert [len(df) for df in flow.outputLoad()] == [1, 2]
```

Match the surrounding file's conventions for fixtures and task naming (give the classes distinct
family names so they don't collide with other tests' cached output — the suite shares `tests/data/`).

### 3. Cover the `path` variant too

The same absorption handles `path`, so add one assertion (or a second small test) driving an
aggregator through a flow that sets a path, e.g. `oryxflow.Workflow(CollectAgg, env='exp1')` or
`path=`. Follow whichever form `tests/test_workflow.py` already uses for per-flow paths, and assert
against `pathlib.Path(...)` rather than a raw string so it passes on both Windows and POSIX.

### 4. Document that aggregators work with flows

File: `docs/docs/advtasksdyn.md`.

The page currently only ever drives aggregators with module-level `oryxflow.run()`. Once step 1
lands, add a short note (user-facing voice — what the reader can now type, not why it broke) that an
aggregator can be wrapped in a `Workflow` like any other task, with a two-or-three-line example.
Do **not** describe the parameter-absorption mechanics on the page; that belongs here.

## Files modified

- `oryxflow/tasks/__init__.py` — `TaskAggregator` gains the `__init__` / `get_param_values` pair that
  absorbs the non-Parameter `path` and `flows` kwargs `Workflow` passes.
- `tests/test_main.py` — new regression test(s) driving an aggregator through `Workflow`, covering
  `get_task`, `preview`, `run`, `complete`, `outputLoad`, plus the `path`/`env` variant.
- `docs/docs/advtasksdyn.md` — note plus short example showing an aggregator inside a `Workflow`.
- `docs/todo/20260726-engine-aggregator-flows-kwarg.md` — this file; add an
  `## Implementation notes (divergences from the plan as built)` section if the built fix differs.

## Verification

**1. The bug reproduces before the change.** Save the snippet from the Context section and run it;
expect `UnknownParameterException: ... unknown parameter flows` from `flow.run()`.

**2. The fix resolves it.** The chosen approach was validated by subclassing `TaskAggregator` with
exactly the two methods from step 1 and running an aggregator through a flow. Expected output after
the change — all five methods succeed and the children's data loads:

```text
get_task: OK
preview: OK
run: OK
complete: OK
outputLoad: OK
rows: [1, 2]
```

**3. The new test fails before and passes after.** Stash step 1, run the new test, confirm it errors
with `UnknownParameterException`; unstash and confirm it passes.

**4. Hold the baseline.** From the repo root (paths resolve `data/` → `tests/data/`):

```bash
python -m pytest tests/test_main.py tests/test_workflow.py \
    tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
```

Expect **86 passing plus the tests added in steps 2–3**. A benign `UserWarning: datatable failed`
and sklearn convergence warnings are expected. Any *other* change in that count means the absorption
leaked into a `TaskData` path — it must not, since step 1 touches only `TaskAggregator`.

**5. Docs still build.** `python scripts/build_docs.py` — the doc tests must still pass and
`mkdocs build` must produce no new link warnings after the `advtasksdyn.md` edit.

## Out of scope (noted while diagnosing)

- **`flows` is injected even when unused.** `Workflow.__init__` adds `{'flows': {}}` to `self.params`
  unconditionally (`oryxflow/__init__.py:401`), so every task construction carries a kwarg that is
  almost always empty. Only ever setting it when a flow is actually attached would shrink the blast
  radius of this whole class of bug, but it changes a code path every flow uses.
- **The error message names an internal.** `unknown parameter flows` points at a kwarg the user never
  wrote. Whatever absorbs these kwargs could raise a message that distinguishes "you passed an
  unknown parameter" from "the engine passed you an internal one".
- **`preview()` doesn't expand an aggregator's children.** The tree prints the aggregator alone
  (`+--[Collect-{} (PENDING)]`) rather than the tasks it yields, so the preview understates what will
  run. Separate issue from this exception; worth its own plan if it bothers anyone.
- **Option B** (hoisting absorption into `core.Task`) as a follow-up de-duplication once option A has
  settled — it would also fix user-written bare `core.Task` subclasses.
