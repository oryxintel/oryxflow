# TaskAggregator redefined as a `requires()`-based group node

## Context

`TaskAggregator` — the task type whose `run()` only yielded other tasks — **could not be used with
`Workflow` or `WorkflowMulti` at all.** Every method on the flow object that resolves the task
instance raised, so an aggregator could only be driven by the module-level `oryxflow.run()`.

Reproduce it with any aggregator on the old contract (no parameters, no `path`, no `env` needed):

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
`flow.preview()`, `flow.get_task()`, `flow.complete()`, `flow.outputLoad()`. There was no workaround
short of abandoning the flow object, which is why `docs/docs/advtasksdyn.md` and
`tests/test_main.py::test_dynamic` both drove aggregators with module-level `oryxflow.run()`.

### Why it happens

Two mechanisms that were built for `TaskData` were never extended to `TaskAggregator`:

1. **`Workflow` passes `path`/`flows` as *constructor kwargs*.**
   `oryxflow/__init__.py` in `Workflow.__init__` unconditionally does:

   ```python
   self.params = dict(**self.params, **{'flows': {}})
   ```

   so `'flows'` is a permanent entry in `self.params` for **every** flow, even one with no flows
   attached. `path` lands in the same dict when `path`/`env` is set. Then `Workflow.get_task()`
   does `return task(**self.params)`.

   `path` and `flows` are **not** Parameters (deliberately — see
   `docs/todo/20260606-sys-param-global.md`; they don't ride through `clone()`), so handing them to
   the parameter machinery is the anomaly. It only works because of mechanism 2.

2. **`TaskData` absorbs and filters them; `TaskAggregator` didn't.**
   `TaskData` declares them as keyword-only args and strips anything that isn't a declared parameter
   *before* calling `super().__init__()`, and overrides `get_param_values` with the same filter, so
   the metaclass's parameter resolution never sees the extras either. `TaskAggregator` subclasses
   **`core.Task` directly**, not `TaskData`, so `flows={}` reached `core.Task`'s parameter resolution
   and was rejected.

The same gap applies to any user-written bare `core.Task` subclass, not just `TaskAggregator`.

### The deeper problem: yielded children are invisible to the whole library

The exception is a symptom, not the disease. On the old contract an aggregator declared its children
by **yielding them from `run()`**, so they existed only in `deps()` and never in `requires()`. Every
propagation mechanism in the library walks `requires()` — `utils.traverse()` (behind
`taskflow_upstream`, `_attach_to_tasks`, `invalidate_upstream`, `reset_upstream`, `FlowExport`) and
`utils.print_tree()`. So even with the absorption fix alone, an aggregator's children stay invisible:
`preview()` prints the group by itself, per-flow `path`/`env` silently never reaches the children,
and upstream reset/export skip them. The documented decorated form (`docs/example-onnx.py`) papered
over this by listing the children **twice** — once in `@oryxflow.requires`, once in `yield` — kept in
sync by hand.

Meanwhile the capability the class was pitched for ("spawn multiple tasks without processing any of
the outputs") is already covered natively: `oryxflow.run([T1(), T2()])` and `flow.run([T1, T2])` both
accept a list of roots, `WorkflowMulti` covers same-task-many-params, and the `requires()` fan-out +
`inputLoadConcat()` pattern covers the case where you do want a combined output.

**Outcome:** keep the class as a named group node, but move the group from `run()` to `requires()`.
It becomes an ordinary DAG node with no output of its own, so `Workflow`, preview expansion, per-flow
`path`/`env`, `reset_upstream` and `FlowExport` all work for free rather than each needing a patch.

### Design decisions

- **Children are declared in `requires()` (or `@oryxflow.requires`); `run()` does nothing.** Confirmed
  with the user. This is what makes every `requires()`-walking mechanism work without touching
  `utils.traverse()` or `print_tree()` — no engine-wide change, no blast radius outside this class.
- **Clean break, no deprecation release.** Confirmed with the user. Breaking changes to the engine
  surface are acceptable (the downstream stability constraint was dropped in 91fc7e5); it lands as a
  `BREAKING:` + `Migration:` bullet in `CHANGELOG.md`. The old generator form fails loudly at
  construction with a message naming the fix, rather than silently doing nothing.
- **Keep the `path`/`flows` absorption.** It is still required: the class subclasses `core.Task`, and
  `Workflow.get_task()` passes both as kwargs. This duplicates `TaskData`'s pair; hoisting both into
  `core.Task` stays a follow-up, not part of this change.
- **Keep the engine's generator seam** (`core.py` `_drive_generator`). It is the dynamic-requires
  path, independent of this class; `test_dynamic` is retargeted to a plain task so it stays covered.
- **Pure passthrough — no artifact of its own.** The group saves nothing and owns no target;
  `complete()` is derived entirely from the tasks it requires. **Rejected: giving it a marker output**
  (a `TaskJson`/`TaskCache` writing a stub file so it has its own completeness). A marker can go stale
  in both directions — present while a member has been invalidated, absent while every member is
  cached — so the derived answer is the correct one and the file would be a second source of truth.
  It also keeps the group free of `path`/target machinery. Nothing needs the artifact: the code
  fingerprint already folds `deps()`, so downstream `code_version` propagation works without a state
  record for the group itself (`codecheck.code_state` returns `(None, None)` for a task with no
  `_resolved_dirpath`, which is what `TaskAggregator` already did).
- **Rejected: deleting `TaskAggregator` outright.** A named group node other tasks can `requires()`
  is worth keeping now that it costs nothing to make work.
- **Rejected: the absorption-only fix** this file originally planned. It fixes the exception but
  leaves the children invisible to preview, per-flow paths, reset and export — the group would run
  but nothing else would see inside it.
- **Rejected: filter at the injection site** — have `Workflow.get_task()` pass only declared
  parameters and rely on `_attach_to_tasks()` to set `path`/`flows` by mutation. Cleanest framing,
  but `Register.__call__` memoizes instances by `(class, serialized-params)` and `Workflow` relies on
  that memo to carry mutated `path`/`flows` to instances retrieved later (`outputPath`, `FlowExport`)
  — see "Instance memoization is load-bearing" in the repo-root `CLAUDE.md`. Removing the constructor
  route could leave upstream instances without a `path`.

## Implementation

### 1. Rewrite `TaskAggregator` — `oryxflow/tasks/__init__.py`

Replace the whole class body; add `import inspect` at the top of the file.

```python
class TaskAggregator(core.Task):
    """
    Task which groups other tasks, without saving an output of its own
    ...
    """

    def __init__(self, *args, path=None, flows=None, **kwargs):
        # `path`/`flows` are set by Workflow, are not Parameters, and must not reach
        # the parameter machinery -- same absorption TaskData does.
        kwargs_ = {k: v for k, v in kwargs.items(
        ) if k in self.get_param_names(include_significant=True)}
        super().__init__(*args, **kwargs_)
        if inspect.isgeneratorfunction(type(self).run):
            raise RuntimeError(
                '{}: TaskAggregator no longer yields tasks from run(). Declare the group in '
                'requires() (or @oryxflow.requires) and leave run() empty.'.format(self.task_family))
        self.path = getattr(self, 'path', path)
        self.flows = flows

    @classmethod
    def get_param_values(cls, params, args, kwargs):
        kwargs_ = {k: v for k, v in kwargs.items(
        ) if k in cls.get_param_names(include_significant=True)}
        return super(TaskAggregator, cls).get_param_values(params, args, kwargs_)

    def run(self):
        pass

    def reset(self, confirm=False):
        return self.invalidate(confirm=confirm)

    def invalidate(self, confirm=False):
        for t in self.deps():
            t.invalidate(confirm)
        return True

    def complete(self, cascade=True):
        return all(t.complete(cascade) for t in self.deps())

    def output(self):
        return [t.output() for t in self.deps()]

    def outputLoad(self, keys=None, as_dict=False, cached=False):
        return [t.outputLoad(keys, as_dict, cached) for t in self.deps()]
```

Notes:
- `super(TaskAggregator, cls)` in `get_param_values`, not bare `super()` — the metaclass calls it as
  a classmethod (mirrors `TaskData`).
- **Delete** the old `deps()` override. `core.Task.deps()` is `flatten(self.requires())`, which is
  now exactly right, and the `code_version` propagation it was added for keeps working through the
  base implementation.
- Every method now reads `self.deps()` where it used to read `self.run()`.
- `complete()` on a group with no `requires()` is vacuously `True` — a group with no members is a
  no-op node, not an error.

### 2. Retarget the generator test — `tests/test_main.py`

`test_dynamic` was the only coverage of `core.build._drive_generator`. Rewrite it against a plain
task with a generator `run()` (dynamic requirements), which is what that seam is for now.

### 3. New tests for the group contract — `tests/test_main.py`

`test_aggregator_workflow` (in-memory: complete/run/outputLoad/preview-expansion/reset) and
`test_aggregator_workflow_env` (on-disk, `env='prod'`, asserts the per-flow env reaches the group's
children). Both inside `class TestMain`; the on-disk one takes the existing `cleanup` fixture. Give
the task classes distinct family names — the suite shares `tests/data/`. Assert paths against
`pathlib.Path(...)`, never a raw string.

### 4. Migrate the code-invalidation test — `tests/test_code_invalidation.py`

In `test_aggregator_propagation`, replace `Agg`'s generator `run()` with
`def requires(self): return [T1(), T2()]`; assertions unchanged (propagation now flows through the
base `deps()`).

### 5. Docs

- `docs/docs/advtasksdyn.md`, "Collector Task" — rewritten: lead with `flow.run([T1, T2])` (no group
  task needed), then the group task for when something downstream depends on the group, then
  `WorkflowMulti` for same-task-many-params. Both `yield` examples removed.
- `docs/docs/advparam.md` — the `@oryxflow.requires` + aggregator example is decorator + `pass`.
- `docs/example-onnx.py` — the two `yield self.clone(...)` lines dropped; the decorator above already
  declares the group.
- Root `CLAUDE.md` — engine note no longer attributes generator `run()` to `TaskAggregator`; the
  layout entry records the group-node contract.
- `../oryxflow-claude-plugin/skills/oryxflow/reference.md` — the `TaskAggregator` row names
  `requires()`. **Edited only, left uncommitted** — that repo is committed by the maintainer.

### 6. `CHANGELOG.md`

One `BREAKING:` bullet under `## [Unreleased]` / `### Changed` with a same-bullet `Migration:` clause.

## Files modified

- `oryxflow/tasks/__init__.py` — `TaskAggregator` rewritten: `requires()`-based members, `path`/`flows`
  absorption, empty `run()`, loud error on the old generator form, `deps()` override deleted;
  `import inspect` added.
- `tests/test_main.py` — `test_dynamic` retargeted to a plain generator-`run()` task; new
  `test_aggregator_workflow` and `test_aggregator_workflow_env`; module-level `io`,
  `contextlib.redirect_stdout`, `pathlib.Path` imports.
- `tests/test_code_invalidation.py` — `test_aggregator_propagation`'s `Agg` uses `requires()`.
- `docs/docs/advtasksdyn.md`, `docs/docs/advparam.md`, `docs/example-onnx.py` — examples on the new form.
- `CLAUDE.md` — engine note + layout entry.
- `CHANGELOG.md` — `BREAKING:` + `Migration:` bullet under `## [Unreleased]`.
- `docs/todo/20260726-engine-aggregator-flows-kwarg.md` — this file, rewritten as the design record.
- `../oryxflow-claude-plugin/skills/oryxflow/reference.md` — one row; **left uncommitted**.

## Verification

1. **The reported bug is gone.** The Context snippet with the group moved into `requires()`:
   `flow.get_task()`, `flow.preview()`, `flow.run()`, `flow.complete()` and `flow.outputLoad()` all
   succeed, and `outputLoad()` returns the members' data (`[1, 2]` rows).
2. **The old form fails loudly.** An aggregator that still yields from `run()` raises `RuntimeError`
   at construction with the migration message — not a silent no-op.
3. **Baseline holds.** From the repo root (`data/` resolves to `tests/data/`):

   ```bash
   python -m pytest tests/test_main.py tests/test_workflow.py \
       tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
   ```

   **88 passing** (86 baseline + the two new tests). A benign `UserWarning: datatable failed` and
   sklearn convergence warnings are expected. `python -m pytest tests/test_code_invalidation.py -q`
   is unchanged at 43.
4. **Docs build.** `python scripts/build_docs.py` — generated doc tests still pass and `mkdocs build`
   produces no new link warnings.

## Out of scope (noted while diagnosing)

- **`flows` is injected even when unused.** `Workflow.__init__` adds `{'flows': {}}` to `self.params`
  unconditionally, so every task construction carries a kwarg that is almost always empty. Only ever
  setting it when a flow is actually attached would shrink the blast radius of this class of bug, but
  it changes a code path every flow uses.
- **The error message names an internal.** `unknown parameter flows` points at a kwarg the user never
  wrote. Whatever absorbs these kwargs could distinguish "you passed an unknown parameter" from "the
  engine passed you an internal one".
- **Hoist the absorption into `core.Task`** — one implementation instead of the two near-identical
  copies in `TaskData` and `TaskAggregator`, and it would fix user-written bare `core.Task`
  subclasses too. Rejected here purely on blast radius: `core.Task.__init__` / `get_param_values` sit
  under the entire engine.
