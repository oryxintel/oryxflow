06# Add loguru-based logging to oryxflow

## Context

oryxflow currently has **no structured logging**. All diagnostic/user output goes through
scattered `print()` calls (execution summary, preview tree, invalidate prompts) and a bare
`traceback.print_exc()` on task failure (`core.py:500`). The `settings.log_level` field
(`settings.py:13`) exists but is **dead** — it's passed into `core.build()` and silently
ignored. There is no way for a user to get timing, per-task lifecycle events, or I/O
visibility, and no clean seam for task authors to log from inside their own `run()` methods.

We want loguru-based logging that (1) **never interferes** with a host application's logging,
and (2) gives useful diagnostics about the engine plus a contextual logger for task authors.

### Related pain point: confusing failure debugging

When a task's `run()` raises, the engine does `traceback.print_exc()` (`core.py:500`) to dump
the *real* traceback to stderr, then later raises a **generic, unchained**
`RuntimeError('Exception found running flow, check trace')` (`__init__.py:153`). Reproduced live:

```
Traceback (most recent call last):
  File ".../oryxflow/core.py", line 493, in _process
    result = task.run()
  File "tasks.py", line 15, in run
ZeroDivisionError: division by zero          <-- the REAL error, printed separately
...
RuntimeError: Exception found running flow, check trace   <-- what actually propagates
```

The propagated `RuntimeError` has **no `__cause__`** linking it to the `ZeroDivisionError` —
they print as **two separate, unrelated `Traceback (most recent call last)` blocks** (confirmed
by running `docs/example-minimal-cache.py`), so the real cause doesn't appear in "the usual
Python stack" and you must scroll *up* to find it. The existing docs section
`docs/source/run.rst:99-125` ("Debugging Failures") tries to explain this ("look further up")
but is **stale** — it shows the old luigi-style `:( ... Execution Summary` output that the
current engine no longer produces. We fix both the behavior and the docs.

**After the fix** the two blocks become one connected chain (verified live):

```
Traceback (most recent call last):
  File ".../example-minimal-cache.py", line 13, in run
    1/0
ZeroDivisionError: division by zero

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File ".../example-minimal-cache.py", line 36, in <module>
    flow.run()
RuntimeError: Exception found running flow, check trace. ...
```

### Design decisions (confirmed with user)

- **Disabled by default.** Follow loguru's official library pattern: all records are namespaced
  under `oryxflow`, and `logger.disable("oryxflow")` is called at import. The library emits
  nothing until the app opts in via `oryxflow.enable_logging()`. Zero interference with the
  host's loguru config or handlers.
- **Default enable level = `INFO`.** `enable_logging()` surfaces INFO and above.
- **Additive for normal output.** All existing `print()` / `preview` / summary / `input()`
  prompts stay exactly as-is. Loguru is added *only* for new diagnostic events. **Exception:**
  the failure path is improved (see next bullet) rather than left additive.
- **Failure path: replace `traceback.print_exc()` with exception chaining.** The bare
  `traceback.print_exc()` in `build()` is removed. Instead we (a) chain the propagated
  `RuntimeError` to the first real exception so the default output is ONE connected stack, and
  (b) emit each failing task's full traceback via `logger.opt(exception=True).error(...)`
  (visible when `enable_logging()` is on). This directly fixes the "two separate, unrelated
  tracebacks" debugging confusion.
- **Per-task `self.logger`** bound with `task_id` / `task_family`, governed by the same
  enable/disable namespace.
- **Hard dependency** on loguru (added to `setup.py`).

## Implementation

### 1. New module: `oryxflow/log.py`

Central place that owns the loguru logger and the namespace toggle.

```python
import sys
from loguru import logger

# Library best practice: emit nothing until the host app opts in.
logger.disable("oryxflow")

def enable_logging(level="INFO", sink=sys.stderr):
    """Turn on oryxflow's internal logging.

    Args:
        level (str): minimum level to surface (default "INFO").
        sink: where to write. Default sys.stderr. Pass sink=None to only
            re-enable the 'oryxflow' namespace and rely on the host app's
            existing loguru sinks (no new handler added).
    Returns:
        handler id (int) if a sink was added, else None. Pass it to
        logger.remove() for fine-grained teardown.
    """
    logger.enable("oryxflow")
    if sink is not None:
        return logger.add(sink, level=level, filter="oryxflow")
    return None

def disable_logging():
    """Silence oryxflow's internal logging again."""
    logger.disable("oryxflow")
```

Notes:
- The added sink is `filter="oryxflow"`, so it only carries oryxflow records and won't duplicate
  the app's own application logs. (If the app *also* has a catch-all sink, oryxflow records may
  appear twice once enabled — documented; use `sink=None` to avoid.)
- Every other module does `from oryxflow.log import logger` so they all share one logger object;
  record `name` stays the emitting module (`oryxflow.core`, `oryxflow.tasks`, ...), all covered by
  the `oryxflow` namespace disable/enable.

### 2. Re-export the API — `oryxflow/__init__.py`

- Add `from oryxflow.log import logger, enable_logging, disable_logging` near the top imports
  (after `from oryxflow import core`).
- These become `oryxflow.enable_logging(...)`, `oryxflow.disable_logging()`, and `oryxflow.logger`.

### 3. Engine lifecycle logging — `oryxflow/core.py` `build()` (lines 441-538)

Add `import time` and `from oryxflow.log import logger` at top. Instrument `_process` /
`_drive_generator`:

| Event | Seam (current line) | Level | Message fields |
|-------|--------------------|-------|----------------|
| Task skipped (already complete) | `core.py:466` `if task.complete()` | DEBUG | `task_id` |
| Task start | `core.py:493` before `task.run()` | INFO | `task_id`, `task_family`; start `time.perf_counter()` |
| Task complete | `core.py:505` after success | INFO | `task_id`, `duration` |
| Task failed (exception) | `core.py:499-503` | ERROR | `logger.opt(exception=True).error(...)` with `task_id` (full traceback via loguru; `print_exc` removed); capture `first_exc` |
| Task failed (dependency) | `core.py:476-478` | ERROR | `task_id` (failed because a dep failed) |
| External task missing output | `core.py:488` | WARNING | `task_id` (external, output not present) |
| Generator yielded batch | `core.py:513`/`522` | DEBUG | count of yielded requires |
| Run summary | after `core.py:529` | INFO | scheduled / ran / complete / failed counts (additive to the existing `print`) |

Duration: capture `t0 = time.perf_counter()` immediately before `task.run()`, compute on both
the success and exception paths.

### 3b. Fix unchained failure — `core.py` `build()` + `__init__.py` `run()`

The single biggest debugging-UX win, additive and small:

- In `build()`'s `except Exception as e:` block (`core.py:499-503`):
  - **Remove** the bare `traceback.print_exc()`.
  - Emit the full traceback through loguru: `logger.opt(exception=True).error("task failed: {}", task.task_id)`
    — shows the complete stack for *every* failing task, but only when `enable_logging()` is on.
  - Capture the **first** failure: `if first_exc is None: first_exc = e` (local `first_exc = None`
    initialized at the top of `build()`).
- Add a `first_exception` field to `RunResult` (`core.py:430-438`) and pass `first_exc` into the
  returned `RunResult` (`core.py:538`).
- In `run()` (`__init__.py:152-154`), chain the raise:
  ```python
  raise RuntimeError('Exception found running flow, check trace. ...') from result.first_exception
  ```
  Python then prints *"The above exception was the direct cause of the following exception:"*,
  linking the generic `RuntimeError` to the real `ZeroDivisionError` in one connected stack —
  no more two unrelated tracebacks. (`result` may be a bool in some paths — guard with
  `getattr(result, 'first_exception', None)`.)

### 4. I/O logging — `oryxflow/tasks/__init__.py` (`TaskData`)

DEBUG-level, additive, no behavior change:
- `save()` (line 224): after persisting, log `task_id` + persist keys saved.
- `outputLoad()` (line 180) and `inputLoad()` (line 125): log `task_id` + keys loaded.
- `invalidate()` (around line 55): log `task_id` invalidated.

Use `from oryxflow.log import logger` at top of the module. (Keep these at DEBUG so the default
INFO enable stays quiet about routine I/O.)

### 5. Invalidation logging — `oryxflow/__init__.py`

In `invalidate_upstream()` / `invalidate_downstream()` (lines ~226-279) and the forced-task
path in `run()` (line 139), log INFO one line per task invalidated. This is additive to the
existing confirm prints.

### 6. Per-task contextual logger — `oryxflow/core.py` `Task`

Add a cached property on `Task`:

```python
@property
def logger(self):
    # contextual logger for task authors; auto-tagged with task identity
    if getattr(self, "_logger", None) is None:
        self._logger = logger.bind(task_id=self.task_id, task_family=self.task_family)
    return self._logger
```

Place near other `Task` properties. It lives in the `oryxflow` namespace, so it's silent until
`oryxflow.enable_logging()` — consistent with library logging. Task authors use it inside
`run()`:

```python
class MyTask(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.logger.info("loading raw input")     # tagged task_id / task_family
        df = self.inputLoad()
        self.logger.debug("rows: {}", len(df))
        self.save(df)
```

### 7. Wire up `settings.log_level` (`settings.py:13`)

It's currently dead. Either (a) make `enable_logging()`'s default level read
`settings.log_level`, or (b) leave `settings.log_level` as the documented knob and drop it from
the `core.build` opts dict in `__init__.py:144` (it's ignored there anyway). **Recommended:**
keep `settings.log_level` as the source of the default level for `enable_logging()` and remove
the dead `'log_level'` entry from the `opts` dict at `__init__.py:144`.

### 8. Dependency — `setup.py`

Add `loguru` to `install_requires` (line 14).

### 9. Docs — `docs/source/run.rst`

**Rewrite the "Debugging Failures" section (lines 99-125)** to match real current behavior and
the new exception chaining. Replace the stale luigi-style `:(` example with the actual output,
and explain the pattern clearly:

```
Debugging Failures
------------------

If a task fails, oryxflow prints the real traceback at the point of failure and then raises a
``RuntimeError`` chained to that original error (``... the direct cause of the following
exception ...``). Read the FIRST traceback — the line in your task's ``run()`` is the real
cause. Example::

    File "tasks.py", line 37, in run     <== the real error is here
        1/0
    ZeroDivisionError: division by zero

    The above exception was the direct cause of the following exception:
    ...
    RuntimeError: Exception found running flow, check trace

Tips:
* The first traceback (the ZeroDivisionError above) points at your bug; the trailing
  RuntimeError is just oryxflow reporting that the flow aborted.
* Set a breakpoint in the task's run() and step through it.
* Run a single task in isolation to debug it directly: ``TaskTrain().run()``
  (note: this skips dependency resolution — make sure upstream outputs already exist).
* Turn on engine logging to see which task failed and timing:
  ``oryxflow.enable_logging()`` (see "Logging" below).
```

**Update the log-level paragraph (lines 226-230).** The `oryxflow.settings.log_level` /
luigi-style log levels text is obsolete. Replace with a short **"Logging"** subsection
documenting the new loguru API:

```
Logging
-------

oryxflow uses `loguru <https://loguru.readthedocs.io>`_. To stay out of your application's way,
its logging is DISABLED by default. Turn it on::

    import oryxflow
    oryxflow.enable_logging()                 # INFO+ to stderr
    oryxflow.enable_logging(level="DEBUG")    # also I/O, cached-skips, dependency detail
    oryxflow.enable_logging(sink=None)        # route into YOUR existing loguru sinks, add no handler
    oryxflow.disable_logging()                # silence again

    flow = oryxflow.Workflow(MyTask)          # then run normally
    flow.run()

INFO shows task start/complete (+duration), failures and invalidation; DEBUG adds save/load
I/O and skipped (already-complete) tasks.

Logging inside your own tasks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Every task has a contextual ``self.logger`` (a loguru logger pre-tagged with this task's
``task_id`` and ``task_family``)::

    class TaskTrain(oryxflow.tasks.TaskPickle):
        def run(self):
            self.logger.info("training on {} rows", len(self.inputLoad()))
            ...

These records share oryxflow's namespace, so they too are silent until ``enable_logging()``.
If you prefer logging that is fully independent of oryxflow's on/off switch, just use your own
``from loguru import logger`` directly in your task code.
```

The existing "Hiding Execution Output" text about ``execution_summary`` (lines 215-224) stays —
that is separate from logging.

## What we log (taxonomy summary)

- **INFO** (visible on default enable): task start, task complete (+duration), task/dep
  failure, run summary, invalidation. The "what's happening" stream.
- **DEBUG** (opt-in via `enable_logging(level="DEBUG")`): skipped/cached tasks, save/load/input
  I/O with keys, generator yields. The verbose "why/details" stream.
- **WARNING**: external task with missing output.
- **ERROR**: task run exception — full traceback via `logger.opt(exception=True)` (replaces the
  old bare `print_exc`); the always-visible connected stack comes from the chained `RuntimeError`.
- Task-author logs via `self.logger` at whatever level they choose.

### Concrete sample output (Task1 → Task3 flow)

`oryxflow.enable_logging()` — INFO:
```
... | INFO | oryxflow.core - task start: Task1 (Task1_11111111111111111111_39624b13bd)
... | INFO | oryxflow.core - task complete: Task1 in 0.041s
... | INFO | oryxflow.core - task start: Task3
... | INFO | oryxflow.core - task complete: Task3 in 0.046s
... | INFO | oryxflow.core - run summary: scheduled=2 ran=2 complete=0 failed=0
```

`oryxflow.enable_logging(level="DEBUG")` — adds I/O + skips:
```
... | DEBUG | oryxflow.tasks - saved Task1 keys=['data']
... | DEBUG | oryxflow.tasks - loaded input for Task3 keys=['input1']
... | DEBUG | oryxflow.tasks - saved Task3 keys=['data']
```
Re-run (cached):
```
... | DEBUG | oryxflow.core - task skipped (already complete): Task1
... | DEBUG | oryxflow.core - task skipped (already complete): Task3
... | INFO  | oryxflow.core - run summary: scheduled=2 ran=0 complete=2 failed=0
```
Failure (`1/0`):
```
... | ERROR | oryxflow.core - task failed: Task1
Traceback (most recent call last): ... ZeroDivisionError: division by zero
```
Task author's own `self.logger.info("training on {} rows", n)`:
```
... | INFO | oryxflow.tasks | task_id=Task3_aae4b32f9c - training on 3 rows
```
Default (no `enable_logging()`): **nothing** from oryxflow — only the existing `print()` summary.

## Files modified

- `oryxflow/log.py` — **new**, owns logger + enable/disable.
- `oryxflow/__init__.py` — re-export API; invalidation logging; chain `RuntimeError from
  result.first_exception`; drop dead `log_level` opt.
- `oryxflow/core.py` — `build()` lifecycle logging + capture `first_exception`; `RunResult`
  gains `first_exception`; `Task.logger` property.
- `oryxflow/tasks/__init__.py` — DEBUG I/O logging in `save`/`outputLoad`/`inputLoad`/`invalidate`.
- `oryxflow/settings.py` — (optional) keep `log_level` as default-level source.
- `setup.py` — add `loguru` dependency.
- `docs/source/run.rst` — rewrite stale "Debugging Failures" section; replace obsolete
  `log_level` text with a new "Logging" subsection (engine + `self.logger`).

## Verification

Primary entry point is `flow = oryxflow.Workflow(Task)` → `flow.run()` (delegates to module-level
`run()` → `core.build()`), so all hooks fire regardless of which entry point is used.

1. **Default silence (no interference):**
   ```python
   import oryxflow
   flow = oryxflow.Workflow(MyTask)
   flow.run()
   # confirm NO loguru output on stderr by default, only the existing execution-summary print().
   ```
2. **Opt-in:**
   ```python
   import oryxflow
   oryxflow.enable_logging()               # INFO + stderr
   flow = oryxflow.Workflow(MyTask)
   flow.run()                             # see task_start / task_complete + duration
   oryxflow.enable_logging(level="DEBUG")  # also see cached-skip + save/load I/O
   flow.run()
   oryxflow.disable_logging()              # silent again
   ```
3. **Task author logger:** add `self.logger.info(...)` in a task's `run()`, confirm records
   carry `task_id`/`task_family` (via `logger.add(..., format=...)` showing `extra`), and are
   silent until `enable_logging()`.
3b. **Failure chaining (the reported pain point):** reuse the temp ZeroDivisionError flow already
   reproduced during planning. After the change, confirm the propagated traceback shows
   *"The above exception was the direct cause of the following exception"* linking the
   `ZeroDivisionError` → `RuntimeError`, and that `enable_logging()` adds an ERROR line naming
   the failed task. Confirm the rewritten `run.rst` example matches this real output.
4. **No host interference:** in a script that configures its own loguru (`logger.add(...)`),
   `import oryxflow` must not change the app's sinks; with `enable_logging(sink=None)` oryxflow
   records flow into the app's existing sinks.
5. **Regression:** run the suite — must stay at the **73-passing** baseline (tests don't capture
   stdout, and logging is disabled by default, so output assertions are unaffected):
   ```bash
   python -m pytest tests/test_main.py tests/test_workflow.py \
       tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
   ```
   Also confirm `loguru` is installed in the test env.

## Implementation notes (divergences from the plan as built)

Three things the original plan didn't anticipate, fixed during implementation (kept passing at
73). All live in `oryxflow/log.py`:

1. **`self.logger` needed a `TaskLogger` facade, not `logger.bind(...)`.** loguru's
   enable/disable and `filter="oryxflow"` gate by the *emitting frame's* module name, not by the
   bound logger. A plain bound logger emitted from a task author's own module gets name
   `<their module>`, so `disable("oryxflow")` wouldn't silence it and `filter="oryxflow"` would
   exclude it — `self.logger` only "worked" via loguru's unfiltered default handler. Fix:
   `TaskLogger` does the real `logger.log()` *inside log.py* (frame name `oryxflow.*`), so it is
   gated like the engine logs; it `logger.patch`es the display name to `oryxflow.task` and binds
   `task_id`/`task_family`. `Task.logger` returns a cached `TaskLogger`.
2. **`enable_logging` removes loguru's default handler (id 0).** Otherwise every record prints
   twice (default catch-all handler + oryxflow's handler) and `level=` is ignored (id 0 is DEBUG).
   `enable_logging` now drops its previously-added sink and handler 0, then adds one filtered
   handler; repeat calls replace rather than stack (module global `_handler_id`). `sink=None`
   still touches no handlers.
3. **`settings.log_level` is wired as the default level** (plan §7 recommendation): `log_level`
   set to `'INFO'`, and `enable_logging(level=None)` reads it via a lazy import (top-level import
   would be circular). The dead `'log_level'` opt was removed from `__init__.py`.

User-facing docs added at `docs/source/logging.rst` (linked from `index.rst` toctree;
`run.rst` keeps a short pointer to it).

4. **`enable_logging` gained a `colorize` arg (added after the plan).** Signature is now
   `enable_logging(level=None, sink=sys.stderr, colorize=None)`, passed straight to
   `logger.add(..., colorize=colorize)`. `colorize=None` auto-detects via the sink's `isatty()`
   (uncolored unless the sink is a TTY), so redirected/captured runs are clean with no second
   plain sink; `True`/`False` force it. Documented in `logging.rst`.
