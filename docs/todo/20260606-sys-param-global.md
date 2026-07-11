# Global / system flow params, instance memoization, and the "task didn't run" problem

Date: 2026-06-06
Context: came out of the luigi-removal rewrite (`oryxflow/core.py`, `oryxflow/parameter.py`).
While replacing luigi we discovered that a small piece of luigi semantics — **task
instance memoization** — is load-bearing for how `Workflow` propagates per-flow context
(`path`, `env`, `flows`). This doc records how that works and the design question around a
"global flow param every task can write to".

---

## TL;DR

- `Workflow(task, env=…/path=…)` makes every task in the flow read/write under a shared
  path. This is a real, used feature.
- `path` and `flows` are **not Parameters** — they're plain attributes. They don't ride
  through `clone()`, so the Workflow delivers them by **mutating task instances** and relying
  on `Task(**same_params)` returning the **same object** later (instance memoization).
- We re-implemented that memoization in ~15 lines on the `Register` metaclass. **Decision:
  keep it.** It is what makes top-down flow config work, especially for upstream tasks.
- A "global flow param that every task can *write* to and downstream tasks *consume*" only
  works while every producer actually runs. The moment a producer is **complete and skipped**,
  in-memory shared state is never written → downstream sees nothing. The only skip-safe
  channels are **persisted output** (`save`/`inputLoad`) and **persisted metadata**
  (`saveMeta`/`metaLoad`). *(Run-2 / cross-process consumption is out of scope for now.)*

---

## How the instance cache works

### The hook: a metaclass `__call__`

`PathTask2(...)` actually calls `type(PathTask2).__call__(PathTask2, ...)`. Since `Task`'s
metaclass is `Register`, that dispatches to `Register.__call__`. The default `type.__call__`
does `obj = cls.__new__(cls); cls.__init__(obj, ...); return obj`. Overriding it lets us
decide whether to build a new object or return an existing one.

```python
# oryxflow/core.py
_instance_cache = {}                       # module-global, shared by all Task subclasses

class Register(type):
    @property
    def task_family(cls):                  # (unrelated) class-level Task.task_family
        return cls.__name__

    def __call__(cls, *args, **kwargs):
        try:
            params       = cls.get_params()                          # [(name, Parameter), ...]
            param_values = cls.get_param_values(params, args, kwargs) # [(name, value), ...]
            param_objs   = dict(params)
            key = (cls, tuple((n, param_objs[n].serialize(v)) for n, v in param_values))
            hash(key)                                                # force hashability check
        except Exception:
            return super().__call__(*args, **kwargs)                # fallback: fresh, uncached

        inst = _instance_cache.get(key)
        if inst is None:
            inst = super().__call__(*args, **kwargs)                # __new__ + __init__ (miss only)
            _instance_cache[key] = inst
        return inst
```

It is **~15 lines** plus one line of state (`_instance_cache = {}`). "Keeps it in memory" =
that dict holds a **reference** to every instance, so (a) the next identical call returns the
same object and (b) the GC never frees it *while the process runs*. Lifetime is
process-lifetime: nothing evicts entries during the run, and at process exit the whole heap is
reclaimed by the OS unconditionally — so this is a memory-residency concern, not a leak that
survives the process. `oryxflow.core._instance_cache.clear()` reclaims it mid-run if ever
needed.

### The key: identity by *parameters*, not by call site

Key = `(class, serialized-param-values)`:

- `get_param_values` resolves positional/keyword/defaults into the canonical `(name, value)`
  list. For `TaskData` the overridden version **filters out non-parameter kwargs** like
  `path` and `flows`, so those never enter the key.
- `serialize(v)` turns each value into a `str` → hashable. (Params store raw lists/dicts,
  which aren't hashable; their serialized form is. `hash(key)` is a belt-and-suspenders
  check.)

So two calls are the same task **iff same class and same serialized values of all declared
params**. `PathTask2()` and `PathTask2(path='data/env=prod', flows={})` both reduce to
`(PathTask2, ())` — identical — because `path`/`flows` aren't params. That equivalence is the
point.

### Hit vs miss — and why skipping `__init__` matters

- **Miss:** `super().__call__(...)` runs `__new__` + `__init__`, stores, returns.
- **Hit:** returns the stored instance **without re-running `__new__`/`__init__`.**

The second part is essential. If `__init__` re-ran on every `PathTask2(...)`, it would
re-execute `self.path = getattr(self, 'path', path)` and clobber `.path` back to the class
attribute, wiping out whatever the Workflow mutated.

---

## Why Workflow path/env propagation depends on this

`TaskData.__init__` (`tasks/__init__.py`) resolves path as:

```python
self.path = getattr(self, 'path', path)
```

i.e. *if the class declares a `path`, use it; else use the argument.* So a task that
hard-codes its own path ignores the `path` argument passed by the Workflow:

```python
class PathTask2(oryxflow.tasks.TaskPqPandas):
    path = 'data/data2_changed'      # class attr wins over constructor arg
```

The env override reaches `PathTask2` only through `Workflow._attach_to_tasks`
(`__init__.py`), which **mutates the instance**:

```python
for temp_task in taskflow_upstream(task_inst):
    temp_task.path = self.params['path']   # instance attr shadows the class attr
```

Trace of the failing case (`tests/test_workflow.py::TestWorkflowOutput::test_workflow_set_path_and_env`):

1. `Workflow(PathTask3, env='prod')` → `_attach_to_tasks` instantiates the target and walks
   upstream. `PathTask2` is created via `clone` with **no** path arg → key `(PathTask2, ())`
   → **miss** → `__init__` sets `.path` = class attr `'data/data2_changed'`. The loop then
   **mutates** `inst.path = 'data/env=prod'`.
2. Later `outputPath(PathTask2)` → `get_task` → `PathTask2(path='data/env=prod', flows={})`
   → key `(PathTask2, ())` → **hit** → returns *that same mutated instance*, init not re-run
   → `.path` is still `'data/env=prod'`. ✓

Without the cache, step 2 builds a fresh object whose `__init__` reads the class attr →
`'data/data2_changed'` → wrong.

Notes:
- Only tasks that hard-code a class-level `path` need this. `PathTask1`/`PathTask3` have no
  class `path`, so the constructor argument reaches them directly — they pass even without a
  cache. The test correctly isolates the "task declares its own path, Workflow overrides it"
  collision.
- **Upstream** propagation needs the cache regardless of the class-attr issue, because
  `path`/`flows` aren't params and `clone()` copies only params — so the shared dict / path
  reaches upstream tasks *only* via mutate-cached-instance.

Empirical confirmation: disabling the memoization (monkeypatching `Register.__call__` to
always build fresh) reproduces exactly one failure —
`test_workflow_set_path_and_env` (`data/data2_changed` vs `data/env=prod`). Restoring it →
74/74 pass.

---

## Two different "flow params"

### 1. Config pushed *down* by the orchestrator (path, env, flow name) — WORKS

Set by the Workflow before/around execution; independent of whether any task runs. This is
what `path` and `flows` are today. It is delivered by mutating instances + identity, i.e. it
**depends on the instance cache**. Keeping the cache keeps this feature working.

### 2. Data produced *by a task* during `run()`, consumed downstream — has a hard limit

This is the "every task can write, downstream consumes" idea. Its limit is the question
"what if the task doesn't run?"

Demonstration with a plain in-memory blackboard (independent of oryxflow internals):

```python
BLACKBOARD = {}                       # a shared, in-memory "global flow param"

class A(oryxflow.tasks.TaskCache):
    def run(self):
        BLACKBOARD['secret'] = 42     # producer writes during run()
        self.save(pd.DataFrame({'a':[1]}))

@oryxflow.requires(A)
class B(oryxflow.tasks.TaskCache):
    def run(self):
        print('B reads BLACKBOARD =', dict(BLACKBOARD))
        self.save(pd.DataFrame({'a':[1]}))
```

- **Run 1** — A runs, writes `secret=42`, B reads `{'secret': 42}`. ✓
- **Run 2** — A's output already exists → A is **skipped** → `run()` never executes → B reads
  `{}`. ✗

This is fundamental, not a oryxflow quirk: completed tasks are deliberately **not** re-run, so
anything a producer leaves only in memory is gone on the next process / after a consumer-only
reset / on a worker. (This is also why the earlier `self.flows['x']=…` attempt raised
`NoneType object does not support item assignment`: the shared `flows` dict only propagates to
upstream tasks when non-empty and only via the mutate-cached-instance path — the plumbing is
half-wired because in-memory sharing can't carry produced data.)

> Run-2 / cross-process consumption is **out of scope** for now. Documented here so the limit
> is understood, not to be solved in this pass.

### The only skip-safe channels are persisted

- **Task output** — `A.save(...)` → persisted → `B.inputLoad()`. Primary mechanism; defined
  to survive skips (that's what `complete()`/`output()` are for).
- **Metadata** — `A.saveMeta({...})` → persisted next to the output → `B.metaLoad()`. The
  right home for "small side-channel value, not the main dataframe" — i.e. a durable
  per-task "flow param" a downstream task can read.

A global mutable dict is only safe for **config injected top-down** (case 1), never for
**produced data** (case 2).

---

## Decision

- **Keep the instance cache.** It is ~15 lines + one dict, negligible memory for normal
  flows, faithful to luigi, and load-bearing for top-down flow config (case 1). Scope here is
  **run 1 only**.

## Caveats to remember

- **Global & process-lifetime.** `_instance_cache` retains every distinct `(class, params)`
  instance for the life of the process (luigi behaved identically). Only matters if you
  programmatically construct huge numbers of distinct-parameter tasks in one long-lived
  process. `oryxflow.core._instance_cache.clear()` reclaims it.
- **Keyed by *all* params, incl. `significant=False`.** Matches luigi. Subtle consequence:
  two tasks differing only in an insignificant param (e.g. `env = Parameter(significant=False)`)
  share the same `task_id` (id uses only significant params) and compare `__eq__`-equal, yet
  are **distinct cached instances** — so each can hold its own `.path`/state.
- **`__eq__`/`__hash__` remain by `task_id`**, independent of this cache. The cache governs
  object identity (`is`); equality/hashing govern set/dict membership in `build()` dedup and
  `find_deps`.

## Possible follow-ups (not in scope now)

- **Make `path`/`flows` `significant=False` Parameters.** Then they ride through the
  constructor *and* `clone()` to upstream tasks, don't affect `task_id`, and the cache is no
  longer needed for propagation. Bigger change: touches `TaskData.__init__`, `_getpath`,
  kwargs filtering, and has d6tflow2 compatibility implications.
- **Metadata-backed "flow store" helper** (`task.flow_set(k, v)` / `task.flow_get(k)`) for the
  write-and-consume pattern that survives skipped producers — i.e. case 2 done correctly on
  top of persisted metadata. (Relevant only once run-2 / cross-process is back in scope.)
