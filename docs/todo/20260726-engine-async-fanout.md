# Concurrent execution for IO-bound task fan-out

## Context

`core.build()` runs the DAG in-process, sequentially. `oryxflow.run(workers=...)` accepts the
argument and **ignores it**. For CPU-bound analysis work that is the right default and nobody
notices. For **IO-bound fan-out it is the one case where the sequential engine is visibly the
wrong shape**, and users hit it.

The motivating shape: a recurring benchmark that queries several external HTTP APIs across a
parameter grid — say 5 endpoints × 8 inputs = 40 tasks, each a network round-trip of 10–60
seconds, each independent of the others. Wall-clock sequentially is the *sum* of 40 round-trips
(~20 minutes) where the work itself is almost entirely waiting on sockets. The same grid with 4
requests in flight finishes in a quarter of the time. Users who reach for oryxflow after
hand-rolling this with `asyncio` will notice they gave up an order of magnitude of wall-clock.

**What this is not.** This is not a request for distributed or multi-process execution — that is
explicitly out of scope for oryxflow (see the README's "When *not* to use oryxflow": distributed
or larger-than-memory execution is Flyte/Metaflow territory). This is about not blocking on a
socket when there are other ready tasks in the DAG.

### Design decisions

**1. Sequential stays the default. It is not the problem it appears to be.**

Before adding concurrency it is worth stating why the pressure to add it is weaker than it looks,
because that is what keeps the scope small:

- Per-task caching removes most of the *reason* for concurrency in these workloads. When a failed
  run loses everything, you want the whole grid done before something breaks. When each task's
  output is independently cached and a re-run resumes, "get it done fast" stops being a
  correctness concern and becomes a comfort concern.
- Concurrency *causes* failures of its own. N in-flight requests sharing one timeout budget is a
  common source of spurious timeouts; a sequential task gets the full budget and does not compete
  with its siblings.
- So the residual cost of sequential execution is **wall-clock only**, on jobs that are usually
  scheduled rather than interactive.

That is why this plan does **not** make concurrency the default, and does not touch the execution
path for tasks that don't ask for it.

**2. Rejected: naive `workers=N` thread pool over the whole DAG.**

The obvious implementation — thread-pool the ready set in `build()` — is unsafe against three
things this codebase relies on, all documented in the repo-root `CLAUDE.md`:

- **`Register.__call__` instance memoization is load-bearing and unsynchronized.** It caches
  instances by `(class, serialized-params)` and `Workflow` propagates per-flow `path`/`flows` by
  *mutating* those cached instances. Concurrent `Task(**params)` construction races on both the
  cache dict and the mutation.
- **`settings` is a global mutable module.** `dirpath`, `cached`, `check_dependencies` are read
  during execution; a task that flips one mid-run affects its concurrent siblings.
- **`cache.data` is a shared dict** and `CacheTarget.load()` returns objects *by reference* —
  concurrent readers of a mutated cached object is a data race on top of the documented
  in-place-mutation gotcha.

Making the whole engine thread-safe is a much larger change than the problem justifies, and it
would put a concurrency hazard in the path of every existing user to benefit a minority workload.

**3. Chosen: opt-in, per-task async, with the engine still owning the DAG.**

A task declares itself IO-bound by defining `async def run()`. `build()` collects contiguous
*ready and independent* async tasks and awaits them together in one event loop, bounded by a
concurrency limit. Everything else — dependency order, completion checks, caching, invalidation,
logging, error handling — is unchanged and still sequential. Single-threaded, so none of the three
hazards above applies: there is exactly one thread mutating the register, settings, and cache.

**4. Also ship the zero-engine-change escape hatch, and document it first.**

A task author can already fan out *inside* one `run()` with `asyncio.run(...)`, saving a
list/dict of results as one output. That is available today, needs nothing from the engine, and is
the right answer when the fan-out is a homogeneous batch. Its cost is granularity: the whole batch
is one cache entry, so one failure re-runs all of it. Document that tradeoff so users can choose
per-task granularity (this plan) or per-batch (the escape hatch) knowingly.

## Implementation

1. **`core.py` — detect async tasks.** In the `Task` base, add a cached class-level check:

   ```python
   @classmethod
   def _is_async(cls):
       return inspect.iscoroutinefunction(cls.run)
   ```

   Generator `run()` (dynamic yields) and `async def run()` are mutually exclusive — raise a clear
   `TypeError` at class definition time in `Register.__new__` if a task is both an async function
   and an async generator, rather than failing obscurely inside `build()`.

2. **`settings.py` — add the bound.** `max_concurrency = 1` (default preserves today's behavior
   exactly). Document it as "how many `async def run()` tasks may be in flight at once; has no
   effect on ordinary tasks."

3. **`core.py:build()` — batch the ready async tasks.** At the point where the next runnable task
   is selected, if it is async, gather the maximal set of *currently ready* async tasks whose
   dependencies are all complete and which do not depend on one another, cap it at
   `settings.max_concurrency`, and run that batch in one `asyncio.run(...)` via
   `asyncio.gather(..., return_exceptions=True)`. Preserve per-task semantics exactly:
   completion/cache checks before the batch, `save()` and event/log emission per task after it,
   and per-task exception capture so one failure marks one task failed and still records
   `RunResult.first_exception` for the first in DAG order (not first-to-fail in wall-clock, so
   runs stay deterministic).

4. **`core.py` — logging.** Keep the existing INFO start/complete-with-duration per task. Duration
   is per task, measured around its own coroutine, not the batch. Add one DEBUG line naming the
   batch size when a batch of >1 runs, so a user can confirm concurrency actually engaged.

5. **`__init__.py:run()` — wire `workers`.** `workers` is currently accepted and ignored. Either
   map it to `settings.max_concurrency` for the duration of the call, or deprecate it explicitly.
   **Do not leave it silently ignored while a real concurrency setting exists elsewhere** — that is
   the confusing state. Prefer mapping it, with a docstring note that it bounds async tasks only.

6. **Docs.** `docs/docs/run.md` gains a short section: when to use `async def run()`, the
   `max_concurrency` setting, and the per-task vs per-batch granularity tradeoff from design
   decision 4. Keep it benefits-first per the docs convention — lead with "a grid of slow API
   calls finishes in a fraction of the time", not with the scheduler mechanics. Add the page to
   the `llmstxt` `sections` check if it is a new page (it is not — `run.md` already exists).

## Files modified

- `oryxflow/core.py` — `Task._is_async()`; async-batch execution inside `build()`; the
  both-async-and-generator guard in `Register.__new__`.
- `oryxflow/settings.py` — new `max_concurrency` setting, default `1`.
- `oryxflow/__init__.py` — `run()` maps `workers` onto `max_concurrency` instead of ignoring it.
- `docs/docs/run.md` — user-facing section on async tasks and the granularity tradeoff.
- `tests/test_main.py` — new async-task tests (below).

## Verification

1. **Baseline holds.** From the repo root:

   ```bash
   python -m pytest tests/test_main.py tests/test_workflow.py \
       tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
   ```

   Expect **86 passing**, unchanged — `max_concurrency=1` by default means no existing path
   behaves differently.

2. **New tests** in `tests/test_main.py`:
   - An `async def run()` task runs, saves, and is cached on a second run exactly like a sync task.
   - A grid of N independent async tasks with `settings.max_concurrency = 4` completes in
     measurably less wall-clock than the sum of their sleeps (assert `elapsed < 0.6 * sum`, loose
     enough not to be flaky on CI).
   - Dependency order is still respected: an async task depending on another async task never
     starts before its parent completes.
   - One failing task in a batch marks exactly that task failed, leaves its siblings' outputs
     saved, and `oryxflow.run(abort=True)` still raises `RuntimeError` chained to the first
     exception in DAG order.
   - A task that is both an async function and an async generator raises `TypeError` at class
     definition.

3. **Manual check.** A 5×8 grid of `asyncio.sleep(2)` tasks: ~80s at `max_concurrency=1`, ~20s at
   `4`, identical cached results, and a second run does nothing at either setting.
