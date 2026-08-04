# Traversal-scoped memos: `complete()`, `requires()` resolution and `_code_fingerprint`

## Context

Every `oryxflow.run()`, `preview()` and `Workflow.complete()` asks each task whether it is
already complete before doing anything else. On a fan-out-heavy DAG that check dominates the
whole command: a downstream consumer project reported **~24s to check 41 branches** against 75
upstream tasks, and **~95s** across four top-level runs — before any task did any work. A no-op
re-run of an already-complete flow, which should be nearly free, is the slowest thing in an
iteration cycle, and `preview()` — an interactive command — is worse.

The cause is structural, not I/O. **Three** engine questions each recurse over a task's whole
upstream closure, and every traversal asks them once per task (`build()._process`) or once per
**printed node** (`utils.print_tree`). So the number of evaluations is the number of **paths**
through the DAG, not the number of tasks. A diamond (fan out, then combine) multiplies: the
shared node upstream of 41 branches is asked 42 times and re-walks its own 32 dependencies each
time.

| recursive question | where | what it re-does per call |
| --- | --- | --- |
| `TaskData.complete(cascade=True)` | `oryxflow/tasks/__init__.py:116` | own output + `_code_ok()`, then `t.complete()` for every dep, recursively |
| `_resolve_requires(task)` | `oryxflow/core.py:616` | `requires_grid` → one `clone()` per fanned branch, at every level of every cascade |
| `Task._code_fingerprint` | `oryxflow/core.py:436` | own code token folded with `d._code_fingerprint` for every dep, recursively |

The fingerprint is the largest of the three and the least obvious: it is a property, so it looks
free at the call sites (`codecheck.code_state`, `Advisor.advise` for every complete task,
`make_record` after every run), and it recurses without any memo — by explicit decision, because
a *permanent* memo would go stale on a runtime `code_version` bump.

### Measurements — before

The synthetic below reproduces the reported shape (41 branches, one shared aggregator over 32
leaves, 75 tasks, parquet outputs). All numbers from this repo at `684210d`; the script becomes
`scripts/bench_traversal.py` in this change:

```
                          wall    complete()      requires()   _code_fingerprint
cold (first) run()       3.90s      75 calls      766 calls        5,774 calls
no-op run()              1.77s   1,428 calls      586 calls        8,439 calls
recursive complete()     1.57s   1,428 calls      376 calls        5,552 calls
preview()                4.75s   5,552 calls      873 calls       13,685 calls
partial rerun (1 shared
  upstream invalidated)  2.24s   1,428 calls      796 calls       11,294 calls
```

19.0× redundancy on `complete()` (1,428 / 75 unique) reproduces the consumer project's
independently measured 19.1× (1,435 / 75), so the synthetic is faithful.

Three facts from those numbers shape the design:

- **`complete()`'s blowup only happens when outputs exist.** A cold run makes just **75**
  `complete()` calls, because `complete = super().complete()` is False on a missing output and
  Python's `and` short-circuits before the dependency cascade. The pathological case is exactly
  the no-op / mostly-complete re-run — the case that should be free.
- **The fingerprint has no such escape hatch**: 5,774 recursive evaluations even on a cold run,
  8,439 on a no-op one. It is the reason a *first* run of a wide DAG also pays a fixed tax.
- **`preview()` is the worst offender** (13,685 fingerprint + 5,552 completeness evaluations),
  because `print_tree` prints one node per *path* — 1,428 nodes for these 75 tasks — and
  cascades from each.

One consequence worth stating separately: with cloud storage (`enable_gcs`/
`enable_cloud_storage`) each completeness check is a **remote object-existence call**, billed
per operation. The 19× redundancy is therefore 19× the API calls and 19× the latency, on every
no-op run. Collapsing the cascade to one evaluation per task is a cost reduction there, not
only a speedup.

### What has already been done — do not redo these

Committed in `684210d`, so the numbers above are *after* them:

- **`_class_source` memo** (`oryxflow/codehash.py:72`). Profiling showed 88.6% of `os.stat`
  calls under a recursive `complete()` came from `_class_source` → `_project_root` + `_is_local`
  re-resolving each task class's source file and project root on every call. Memoized on
  `(cls, PROJECT_ROOT)`. Measured **4.7× on a no-op `run()`, 2.0× on a bare `complete()`**. A
  negative result (`start is None`) is deliberately not cached.
- `state._load` already caches records per directory (`oryxflow/state.py:47`), so
  `state.get_record` is one dict lookup after the first read per directory. Not a lever.
- `codehash.freeze()`/`unfreeze()` already bracket the whole of `build()`
  (`oryxflow/core.py:1187`), so source-mtime re-stats are suppressed for the duration of a run.
  A wrong diagnosis to not chase: the remaining cost is **not** `_walk_cache_get` re-stat'ing
  the import closure (identical `os.stat` counts frozen vs unfrozen inside a build). It is
  evaluation **count**.

### Design decisions

1. **Memoize per traversal, not per process.** A context manager `core.traversal_scope()`
   (mirroring the existing `codehash.freeze()`/`unfreeze()` pair) makes three memos live for the
   duration of one traversal. Outside a scope every memo is `None`, so a bare `t.complete()` in
   user code behaves exactly as today.

2. **Two memo flavours, because their invalidation rules genuinely differ.**
   - **Volatile — completeness.** A task that materializes or is invalidated changes what is
     complete, so this memo is dropped at those points: `build()` brackets every `run()`,
     `TaskData.save()` clears, `invalidate()` clears. Measured cost of clearing: none —
     `build()`'s own `visited` map already asks each task at most once per build at the top
     level, so a full 43-task rerun executes the check 75 times, the same as a no-op run.
   - **Structural — resolved `requires()` and `_code_fingerprint`.** Both are functions of the
     code and the parameters, not of the outputs, so they live for the whole traversal and are
     *not* cleared when a task runs. `codehash.freeze()` already brackets `build()` on exactly
     that premise (code cannot change mid-build), and `_process` resolves `requires()` before a
     task runs and `input()` after it — a `requires()` that answered differently across a run
     would already be wiring a different DAG. Freezing it per traversal *enforces* an
     assumption the engine has always made.

3. **`_dep_state()` must NOT be memoized.** It is the volatile twin of `_code_fingerprint`
   (`tasks/__init__.py:182`): it folds each dependency's record `output_id`, which is precisely
   what changes when a dependency rematerializes. It is also not hot once the other three are
   memoized (one evaluation per task per completeness check). Left alone, deliberately.

4. **Only `cascade=True` completeness is memoized.** That is where the recursion is.
   `cascade=False` does no recursion, so there is nothing to save, and it is what the load
   paths ask (`outputLoad`, `outputLoadMeta`, `outputLoadAllMeta`:
   `tasks/__init__.py:381,510,520,530,628`) immediately after a save — exactly where a cached
   answer would be a liability. Not memoizing it removes that whole class of hazard for free.

5. **Keyed on `id(task)`, with the task held in the value**, so an id cannot be recycled while
   its entry lives. `Register.__call__` (`core.py:153`) already memoizes instances by
   `(class, serialized params)`, so the instance is canonical. `task_id` would be the wrong key
   in general — `Workflow` propagates a per-flow `path` by *mutating* a non-parameter attribute
   onto shared instances, so two flows share a `task_id` while writing to different directories.
   **What makes flows safe is the scoping, not the key: never widen a scope across flows.**
   `WorkflowMulti.run()`/`preview()` loop one flow at a time and re-`_attach_to_tasks` between
   flows; one scope per flow build is both correct and the maximum that is correct.

6. **Thread-local state.** `oryxflow.run(main_thread_only=False)` is documented as usable "in
   apps and workers", so concurrent builds in one process are a supported shape. Two threads
   traversing the same memoized task *instance* could otherwise read each other's completeness
   answers. A `threading.local` subclass costs nothing and removes the class entirely (it also
   makes the nesting depth per-thread, which is the only correct reading).

7. **A generation counter guards the write-after-compute race.** An entry is only stored if no
   invalidation happened while it was being computed. Three lines; makes the memo safe even if
   some future (or user) code materializes something from inside a `requires()`.

8. **Rejected: post-order restructure of `build()._process`.** The other candidate — descend
   into deps first, then ask `complete(cascade=False)` plus "did any dep just run" — makes the
   redundancy structurally impossible instead of memoized away. Rejected because with the memo
   the redundancy is *already* gone (1,428 → 116 invocations is O(V+E): one root call plus 115
   edges, exactly), so post-order would take 116 → 75 on the part that is no longer the
   problem. Against that it (a) changes `RunResult.complete` accounting — every upstream task
   would be reported complete where today a fully-complete flow reports only its root, and
   `tests/test_runresult.py` plus the documented summary wording depend on those counts; (b)
   needs a special case to preserve `settings.check_dependencies=False` semantics, which
   `tests/test_main.py::test_execute` pins (line 369 `assert t3.complete()  # no cascade
   upstream`, then a run that must leave `t1` incomplete); (c) buys nothing on the cold path,
   which already short-circuits at 75 calls.

9. **Rejected: precise invalidation instead of clearing the volatile memo.** A tempting
   refinement: on a run, drop only the `False` entries, since a memoized `True` provably has an
   all-complete closure and therefore cannot contain the task that just ran. Prototyped and
   measured: **identical counts** (a full 43-task rerun executes 75 completeness checks either
   way), because `build()`'s `visited` map already prevents re-asking, and a downstream whose
   upstream rematerialized short-circuits on its own `_code_ok()` before cascading. Rejected: a
   proof-dependent rule that also breaks under `check_dependencies=False` (where a `True` says
   nothing about deps), for no measurable gain.

10. **Rejected: memo stats in the event stream.** The `run_finished` payload is a curated,
    user-documented record of what happened to the *data* (`docs/docs/managing-workflows.md`
    enumerates it); engine cache counters do not belong there. They go to a DEBUG log line plus
    an internal `core.traversal_stats()`, which matches the level taxonomy (DEBUG = routine
    internals) and is what the regression tests assert on.

11. **Answered: is the `check_dependencies` cascade still load-bearing given `_code_ok`'s
    `_dep_state` fold?** Yes — do not remove it. `_dep_state` catches an upstream that
    *rematerialized*; it cannot catch an upstream whose output was **deleted**, because a reset
    leaves the record and its `output_id` in place, so the fold is unchanged. The cascade is
    also the only mechanism at all when the code-invalidation feature is inert
    (`_code_fingerprint is None`, e.g. `settings.code_version_auto=False` with no explicit
    `code_version`, where `_code_ok()` returns True unconditionally).
    `tests/test_main.py::test_execute:367` pins it: `assert not t3.complete()  # cascade
    upstream` after resetting `t1`/`t2`.

12. **No new public API, plus one escape hatch.** `settings.traversal_memo = True` exists so a
    correctness surprise in the field has a one-line workaround, and so the suite can assert
    **equivalence**: identical `preview()` text and identical `RunResult` counts with the memo
    on and off. Not documented in `docs/docs/` — it is not something a user should have to think
    about.

### Measurements — after (prototype)

Same DAG, same script, all three memos with the invalidation rules above:

```
                          wall    complete()          requires()      _code_fingerprint
                                  calls / exec        calls / exec    calls / exec
cold (first) run()       1.50s      75 /  75          475 /  43         265 /  75      2.6x
no-op run()              0.05s     116 /  75          172 /  43         265 /  75       35x
recursive complete()     0.04s     116 /  75          129 /  43         190 /  75       39x
preview()                0.06s   1,543 /  75          213 /  43         190 /  75       79x
partial rerun            0.16s     116 /  75          215 /  43         308 /  75       14x
```

Every traversal converges on the same invariant: **one execution per unique task** (75), and one
`requires()` resolution per task that has a spec (43). That invariant — not wall-clock — is what
the regression test asserts, because repeated wall-clock runs of this benchmark on Windows vary
by ±30% for identical work.

> **Note (post-plan): `preview()` now runs the unused-input lint.** The lint
> (`oryxflow/inputcheck.py`, added after these numbers were captured) fires inside `preview()`
> only — `run()`/`build()` deliberately do **not** lint, to keep the execution path free — so the
> `preview()` row does marginally more work than shown; the `run()` rows are unchanged. It does
> **not** change this plan's design or its invariant: the lint is memoized per class (O(families),
> not O(paths)), so it never enters the per-path recursion this plan removes. Treat the wall-clock
> figures above as indicative; the execution **counts** are still exact.

## Implementation

### 1. `oryxflow/settings.py` — the escape hatch

Next to `check_dependencies` (line 31):

```python
check_dependencies = True
# traversal-scoped memos for complete()/requires()/code-fingerprint (see core.traversal_scope).
# On by default; False restores the un-memoized recursion, which is strictly slower and is only
# useful to isolate a suspected memo bug (the test suite uses it to assert equivalence).
traversal_memo = True
```

### 2. `oryxflow/core.py` — the scope, the memos, the fingerprint seam

Add `import contextlib` and `import threading` to the stdlib import block (lines 10-16).

Insert this section immediately after `getpaths()` (after `core.py:84`, before `task_id_str`):

```python
# ----------------------------------------------------------------------------------------------
# Traversal-scoped memos
# ----------------------------------------------------------------------------------------------
# Three engine questions recurse over a task's whole upstream closure, and a traversal asks each
# once per task (build) or once per printed NODE (print_tree) -- so the number of evaluations is
# the number of PATHS through the DAG, not the number of tasks. Measured on a 41-branch fan-out
# over a shared aggregator (75 tasks), one no-op run(): 1,428 completeness checks, 586 requires()
# resolutions, 8,439 code-fingerprint evaluations; one preview(): 5,552 / 873 / 13,685.
#
# Within ONE traversal none of those answers may change, so each is computed once per task. The
# memos come in two flavours because their invalidation rules differ:
#
#   VOLATILE -- completeness. A task that materializes or is invalidated changes what is
#     complete, so this memo is dropped at those points: build() brackets every run(),
#     TaskData.save() clears, invalidate() clears. Clearing is measurably free -- build()'s own
#     `visited` map means each task is asked once per build at the top level anyway, so a full
#     43-task rerun executes 75 checks, the same as a no-op run.
#   STRUCTURAL -- resolved requires() and _code_fingerprint. Both are functions of the code and
#     the parameters, not of the outputs, so they live for the whole traversal. codehash.freeze()
#     already brackets build() on that premise (code cannot change mid-build), and _process
#     resolves requires() before a task runs and input() after it -- a requires() answering
#     differently across a run would already be wiring a different DAG. Freezing per traversal
#     enforces an assumption the engine has always made.
#
# NOT memoized, and must not be: TaskData._dep_state(), the volatile twin of _code_fingerprint.
# It folds each dep's record output_id, which is exactly what changes when a dep rematerializes.
#
# Keyed on id(task) with the task held in the value, so an id cannot be recycled while its entry
# lives. NOT keyed on task_id: Workflow propagates a per-flow `path` by mutating a non-parameter
# attribute onto shared instances, so two flows share a task_id while writing to different
# directories. What makes flows safe is the SCOPING, not the key -- NEVER widen a scope across
# flows (WorkflowMulti runs/previews one flow at a time and re-attaches paths between them).
#
# State is thread-local: run(main_thread_only=False) supports concurrent builds in an app, and
# two threads traversing the same memoized instance must not read each other's answers.


class _TraversalState(threading.local):
    """Per-thread memo state; the class attributes are the per-thread defaults."""
    complete = None       # id(task) -> (task, complete?)          None -> no traversal open
    requires = None       # id(task) -> (task, (deps, groups))
    fingerprint = None    # id(task) -> (task, fingerprint)
    depth = 0             # nesting: a task's run() may call oryxflow.run()
    gen = 0               # bumped on every volatile clear (see _memoized)
    stats = None          # counters for the traversal in flight
    last_stats = None     # counters of the most recent completed traversal


_traversal = _TraversalState()


def _new_stats():
    return dict(complete_hit=0, complete_miss=0, requires_hit=0, requires_miss=0,
                fingerprint_hit=0, fingerprint_miss=0, cleared=0)


@contextlib.contextmanager
def traversal_scope():
    """Memoize completeness, resolved dependencies and code fingerprints for the duration of
    one traversal -- a build, a preview, a ``Workflow.complete()``, ``accept_code()``, the
    upstream/downstream walks behind the invalidate helpers.

    Nested scopes share one memo and only the outermost drops it, so a task's ``run()`` calling
    ``oryxflow.run()`` (flow-within-a-flow) needs no special case: that nested build clears the
    shared volatile memo whenever it materializes something.
    """
    from oryxflow import settings          # lazy: settings imports core
    st = _traversal
    if not getattr(settings, 'traversal_memo', True):
        yield
        return
    if st.depth == 0:
        st.complete, st.requires, st.fingerprint = {}, {}, {}
        st.stats = _new_stats()
    st.depth += 1
    try:
        yield
    finally:
        st.depth -= 1
        if st.depth == 0:
            s = st.last_stats = st.stats
            logger.debug(
                "traversal memo: complete {}/{}, requires {}/{}, fingerprint {}/{} hit, "
                "{} invalidation(s)",
                s['complete_hit'], s['complete_hit'] + s['complete_miss'],
                s['requires_hit'], s['requires_hit'] + s['requires_miss'],
                s['fingerprint_hit'], s['fingerprint_hit'] + s['fingerprint_miss'],
                s['cleared'])
            st.complete = st.requires = st.fingerprint = st.stats = None


def traversal_stats():
    """Memo counters for the traversal in flight, else the most recent one.

    Internal. The regression tests assert on the MISS counts: one miss per unique task is the
    invariant this whole section exists to hold, and unlike wall-clock it is deterministic.
    """
    st = _traversal
    return dict(st.stats or st.last_stats or _new_stats())


def traversal_memo_clear():
    """Drop the volatile memo: something changed what is complete (a task materialized, an
    output was invalidated). The structural memos are untouched -- neither the code nor the DAG
    shape changes mid-traversal."""
    st = _traversal
    if st.complete is not None:
        st.complete.clear()
        st.gen += 1
        st.stats['cleared'] += 1


def _memoized(memo, kind, task, compute):
    st = _traversal
    if memo is None:
        return compute()
    hit = memo.get(id(task))
    if hit is not None:
        st.stats[kind + '_hit'] += 1
        return hit[1]
    st.stats[kind + '_miss'] += 1
    gen = st.gen
    value = compute()
    if st.gen == gen:
        # nothing was invalidated while we were computing; the task is held in the entry so its
        # id cannot be recycled underneath us
        memo[id(task)] = (task, value)
    return value


def complete_cached(task, cascade, compute):
    """Evaluate a cascading ``complete()`` at most once per task per traversal.

    ``cascade=False`` is never memoized: it does no recursion, so there is nothing to save, and
    it is what the load paths ask right after a save.
    """
    if not cascade:
        return compute()
    return _memoized(_traversal.complete, 'complete', task, compute)


def fingerprint_cached(task, compute):
    return _memoized(_traversal.fingerprint, 'fingerprint', task, compute)
```

Then the fingerprint seam — replace the `_code_fingerprint` property (`core.py:436-461`) with a
memoized property plus the existing body, and correct the "not memoized" note so the next reader
does not read the memo as a bug:

```python
    @property
    def _code_fingerprint(self):
        """Recursive code identity, compared against the state store by
        TaskData.complete() -- a mismatch forces a rerun (authoritative). The own
        token is the explicit ``code_version`` when declared; otherwise, with
        ``settings.code_version_auto`` (the default), the AST hash of the task's own
        class plus the repo-local symbols it transitively references, so logic edits
        rerun automatically -- and edits to unrelated siblings in the same file don't.
        None when neither applies here or upstream (feature inert).
        For tasks WITH an explicit code_version the AST source-hash stays a
        warn-only advisory and never gates completeness.

        Memoized per TRAVERSAL only (``traversal_scope``), never per instance: instances are
        process-long-lived via the Register cache, so a cached fingerprint would go stale on a
        runtime ``code_version`` bump between runs. Within one traversal it cannot change --
        ``codehash.freeze()`` already brackets ``build()`` on that premise -- and recomputing it
        is O(paths): 8,439 recursive evaluations for one no-op run over 75 tasks.
        """
        return fingerprint_cached(self, self._code_fingerprint_compute)

    def _code_fingerprint_compute(self):
        dep_fps = [d._code_fingerprint for d in self.deps()]
        own = self.code_version
        ...          # the rest of today's body, unchanged
```

Then split `_resolve_requires` (`core.py:616`) into a memoizing wrapper plus the existing body.
Keep the name `_resolve_requires` on the wrapper: the generated `requires()` (`_spec_requires`,
line 670) and `_requires_groups` (line 693) both close over the module global, so both pick up
the memo with no other change. Both callers only read `[0]`/`[1]` and neither mutates the
returned structures, so handing back the same objects is safe.

```python
def _resolve_requires(task):
    """Return ``(deps, groups)``. ``deps`` is what ``requires()`` yields; ``groups`` maps a
    fan-out group name to ``{dependency key: branch label}``.

    Memoized for the duration of a traversal (``traversal_scope``): every level of every
    completeness cascade calls this, and for a fan-out each call re-clones every branch.

    An UNNAMED fan-out keys dependencies on the value itself, exactly as it always has. Naming
    a group qualifies its keys with that name (``chart_north``) -- which is how naming resolves
    a collision between two fan-outs over the same values. The label stays the bare value, so
    ``inputLoad(flatten=False)`` yields ``{'chart': {'north': ...}}`` either way.
    """
    return _memoized(_traversal.requires, 'requires', task, lambda: _resolve_requires_uncached(task))


def _resolve_requires_uncached(task):
    cls = type(task)
    ...          # the rest of today's body, unchanged
```

### 3. `oryxflow/core.py` — `build()` brackets every `run()` and opens the scope

Extract the `run()` invocation into a nested helper so both clears live in one place, next to
`_drive_generator` (`core.py:1216`; closures resolve at call time):

```python
    def _run_task(task):
        # A task that materializes changes what is complete, so no volatile answer may outlive
        # it -- and code inside run() (inputLoad, a flow-within-a-flow oryxflow.run(), a manual
        # complete()) must not read an answer computed before it either. Hence both sides.
        # TaskData.save() clears too; this bracket also covers tasks that materialize without
        # save() and runs that fail part-way.
        traversal_memo_clear()
        try:
            result = task.run()
            if inspect.isgenerator(result):
                return _drive_generator(result)
            return True
        finally:
            traversal_memo_clear()
```

and rewrite the `try` in `_process` (`core.py:~1163`, the `try: result = task.run()` block) to
call it:

```python
        try:
            if not _run_task(task):
                failed.append(TaskFailure(task, reason="dependency failed"))
                _emit_failed(task, 'dependency failed', None, time.perf_counter() - t0)
                visited[tid] = False
                return False
        except Exception as e:
            ...          # unchanged
```

Then open the scope around the processing loop, inside the existing `freeze()` bracket
(`core.py:1250-1256`):

```python
    _codehash.freeze()   # code can't change mid-build: skip per-complete() mtime re-stats
    try:
        with traversal_scope():
            for task in tasks:
                _process(task)
    finally:
        _codehash.unfreeze()
        _log.set_task_log_capture(previous_capture)
```

### 4. `oryxflow/tasks/__init__.py` — consult the memo, clear on materialization

`TaskData.complete` (line 116) — keep the docstring on `complete`, move the body to
`_complete_check`:

```python
    def complete(self, cascade=True):
        """
        Check if a task is complete: output exists AND the stored code fingerprint
        matches the current one (``_code_ok`` -- a ``code_version`` bump makes the
        task incomplete and forces a rerun; authoritative, unlike the warn-only AST
        source-hash advisory). With ``check_dependencies``, cascades upstream.

        The cascading form is evaluated once per task per engine traversal (see
        ``core.traversal_scope``); ``cascade=False`` is never memoized.
        """
        return core.complete_cached(self, cascade, lambda: self._complete_check(cascade))

    def _complete_check(self, cascade):
        complete = super().complete()
        if complete and not getattr(self, 'external', False):
            complete = self._code_ok()
        if oryxflow.settings.check_dependencies and cascade and not getattr(self, 'external', False):
            complete = complete and all(
                [t.complete() for t in core.flatten(self.requires())])
        return complete
```

`TaskAggregator.complete` (line 730) — the other recursive implementation, and note it recurses
even for `cascade=False`, so it goes through the same seam:

```python
    def complete(self, cascade=True):
        return core.complete_cached(
            self, cascade, lambda: all(t.complete(cascade) for t in self.deps()))
```

Add `core.traversal_memo_clear()` with this comment at four points:

```python
        # this task's outputs changed: an open traversal's completeness answers are stale
        core.traversal_memo_clear()
```

- `TaskData.save` (line 417), after the `logger.debug("saved {} keys={}", ...)` line.
- `TaskExcelPandas.save` (line 616), after `self.output().save(data, **kwargs)`.
- `TaskData.invalidate` (line 85), inside the `if c == 'y':` block after `self._invalidate_meta()`.
- `TaskExcelPandas.invalidate` (line 653), after `self.output().invalidate()`.

(`TaskAggregator.invalidate` delegates to its dependencies' `invalidate()`, so it is covered.)

### 5. `oryxflow/__init__.py` — open a scope at each traversal entry point

- `preview()` (line 82) — wrap the loop, so `print_tree`'s per-node cascades share one memo.
  This also covers `Workflow.preview` and `WorkflowMulti.preview`, which both funnel here, and
  gives `WorkflowMulti` one scope **per flow**, which is the maximum that is correct:

  ```python
      with core.traversal_scope():
          for t in tasks:
              msg += oryxflow.utils.print_tree(t, indent=indent, last=last,
                                               show_params=show_params, clip_params=clip_params)
  ```

- `Workflow.complete()` (line 671):

  ```python
      def complete(self, task=None, cascade=True):
          with core.traversal_scope():
              return self.get_task(task).complete(cascade=cascade)
  ```

- `taskflow_upstream()` (line 211) and `taskflow_downstream()` (line 226) — wrap the **whole**
  body, not just the `only_complete` filter. `utils.traverse` and `core.dfs_paths` re-resolve
  `requires()` at every node of every path (`dfs_paths` enumerates paths outright), so the
  structural memo is the bigger win there, and the scope closes before any caller invalidates:

  ```python
      with core.traversal_scope():
          tasks = oryxflow.utils.traverse(task)
          if only_complete:
              tasks = [t for t in tasks if t.complete()]
          return tasks
  ```

- `run()`'s forced-task filter (line 136-142) — wrap the `taskflow_downstream` collection and
  the `complete()` filter, closing the scope before the invalidation loop:

  ```python
          with core.traversal_scope():
              invalidate = []
              for tf in forced:
                  for tup in tasks:
                      invalidate.append(oryxflow.taskflow_downstream(tf, tup))
              invalidate = set().union(*invalidate)
              invalidate = {t for t in invalidate if t.complete()}
  ```

  `invalidate_upstream`/`invalidate_downstream` need nothing further — they inherit the
  `taskflow_*` scopes and invalidate after they close (and `invalidate()` clears regardless).

- `Workflow.dependents` (`__init__.py:741`) and `Workflow.dependencies` (`:767`) — the
  discoverable dependency queries added after this plan was written. `dependencies(target=None)`
  already funnels through the scoped `taskflow_upstream`; the `find_deps`/`find_paths` branches of
  both methods do not. Wrap each method body in `core.traversal_scope()`. Marginal on a single
  call — `find_deps`/`find_paths` (`core.py:900`/`:908`) already carry their own per-walk memo
  (`_reachable_to`/`_suffix_paths`, `core.py:856`/`:871`) — but consistent with the "every
  traversal entry point opens a scope" rule, and it shares the structural `requires()` memo when a
  caller loops these. The `WorkflowMulti` delegators (`:1158`, `:1169`) call one `Workflow` method
  per flow, so wrapping the `Workflow` methods covers both, one scope per flow.

### 6. `oryxflow/codecheck.py` — scope `accept_code`'s walk

`accept_code._walk` calls `t._code_fingerprint` per task, so a bare `flow.accept_code()` on a
wide DAG pays the same O(paths) fingerprint recursion (5,774 evaluations for 75 tasks). Wrap the
root-dispatch loop (`codecheck.py:414`, the `for task in (...)` loop that drives `_walk` — `_walk`
is at `codecheck.py:365` and recurses via `t._code_fingerprint` + `t.requires()`) in
`with core.traversal_scope():`. Safe: the walk reads
fingerprints and `_dep_state()` (unmemoized) and writes records with `output_id` preserved, so
no memoized answer can change underneath it.

### 7. `scripts/bench_traversal.py` — new, committed

The benchmark below, verbatim, so the win is reproducible instead of pasted into a plan. It
writes and removes `data-bench-traversal/` in the cwd, prints one row per traversal with
calls/executions, and takes `--memo/--no-memo` (`settings.traversal_memo`) so before/after is
two invocations of one committed script.

### 8. `tests/test_traversal_memo.py` — new

Copy the `env` fixture shape from `tests/test_code_invalidation.py:14` (isolated `tmp_path` data
dir, `oryxflow.state.clear_cache()`). Build the fan-out shape with **`TaskCache`** targets, not
parquet, so the DAG is fast enough for the suite (measured 1.27s vs 1.77s per no-op traversal
before the change, and ~0.03s after), and scale it to ~8 branches × 6 leaves — the assertions are
against the DAG's own task count, never a magic number.

1. **The win, as a regression test.** After a first `flow.run()`: a no-op `flow.run()` and a
   `preview()` each report `traversal_stats()['complete_miss'] == <unique task count>` and
   `fingerprint_miss == <unique task count>`, and `requires_miss == <tasks with a spec>`. Before
   this change those are 1,428 / 8,439 / 586 and 5,552 / 13,685 / 873 on the 75-task shape.
2. **Hits actually happen**: `complete_hit > 0` and `fingerprint_hit > complete_hit` on the same
   traversal (the fingerprint is the deeper recursion) — a memo that is present but never hit
   would satisfy (1) while doing nothing.
3. **Equivalence with the memo off.** For the same DAG: `preview(print_it=False)` returns a
   byte-identical string with `settings.traversal_memo` True and False; and for a DAG where
   some tasks are complete and some are not, `len(r.ran)`, `len(r.complete)`, `r.reasons` and
   `r.warnings` match either way.
4. **A dependency that reruns mid-build invalidates downstream.** A ← B ← C, all complete; bump
   `B.code_version`; `oryxflow.run(C)` must rerun both B and C. Remove the `_run_task` clears
   and this must fail.
5. **`save()` inside a `run()` body.** A task whose `run()` calls `oryxflow.run(other)` (the
   documented flow-within-a-flow) where `other` was incomplete: the outer run succeeds, `other`
   materialized, and a task downstream of `other` in the same build still reruns.
6. **`invalidate()` clears an open scope.** Inside an explicit `with core.traversal_scope():`,
   assert `t.complete()`, then `t.reset(confirm=False)`, then assert `not t.complete()`. Remove
   the `invalidate()` clears and this must fail.
7. **`cascade=False` is never memoized.** Inside a scope: `assert not t.complete(cascade=False)`,
   materialize via `t.run()`, `assert t.complete(cascade=False)` — this is what `outputLoad`'s
   guard depends on.
8. **Two flows, one `task_id`, two directories.** Two `Workflow`s over identical params with
   different `path=`: run the first, then the second; both must actually run and both output
   paths must exist. Then `flow_a.complete()` and `flow_b.complete()` independently True.
9. **A `code_version` bump between two traversals is seen.** `oryxflow.run(t)`; bump
   `T.code_version`; `oryxflow.run(t)` reruns. Guards the fingerprint memo against ever being
   promoted to per-instance.
10. **Nesting.** `traversal_stats()` reports one scope's worth of counters when a scope is
    entered twice (nested), and `core._traversal.complete is None` after the outermost exits.
11. **`external=True` and generator `run()`** stay covered by `tests/test_main.py`
    (`test_external`, `TaskDynamic` at line 1049) and `tests/test_code_invalidation.py` — no new
    tests, but they are the ones to watch in the full run.

## Files modified

- `oryxflow/settings.py` — **unchanged** (the escape hatch became `core._traversal_memo_enabled`;
  see the divergence note below).
- `oryxflow/core.py` — `import contextlib`, `import threading`; module-private
  `_traversal_memo_enabled = True` escape hatch; new traversal-memo section
  (`_TraversalState`, `traversal_scope`, `traversal_stats`, `traversal_memo_clear`, `_memoized`,
  `complete_cached`, `fingerprint_cached`); `Task._code_fingerprint` split into a memoized
  property + `_code_fingerprint_compute` (docstring corrected); `_resolve_requires` split into a
  memoizing wrapper + `_resolve_requires_uncached`; `build()` gains `_run_task` and opens
  `traversal_scope()` around the `_process` loop.
- `oryxflow/tasks/__init__.py` — `TaskData.complete` split into `complete` (memo seam) +
  `_complete_check`; `TaskAggregator.complete` routed through the same seam;
  `traversal_memo_clear()` in `TaskData.save`, `TaskExcelPandas.save`, `TaskData.invalidate`,
  `TaskExcelPandas.invalidate`.
- `oryxflow/__init__.py` — `traversal_scope()` around `preview()`'s loop, `Workflow.complete()`,
  the bodies of `taskflow_upstream`/`taskflow_downstream`, `run()`'s forced-task filter, and the
  `find_deps`/`find_paths` branches of `Workflow.dependents`/`dependencies` (covers the
  `WorkflowMulti` delegators, one scope per flow).
- `oryxflow/codecheck.py` — `traversal_scope()` around `accept_code`'s dispatch loop.
- `scripts/bench_traversal.py` — new; the committed benchmark.
- `tests/test_traversal_memo.py` — new; the eleven cases above.
- `docs/todo/20260730-engine-traversal-scope.md` — this plan (commit with the code).
- `docs/todo/20260729-complete-speedup.md` — the pre-plan; already carries a pointer here.

No user-facing documentation changes: behaviour is unchanged and there is no new public API.

## Verification

1. **Test baseline.** `python -m pytest tests/ -q` — currently **184 passed** (~16s). Must stay
   at 184 plus the new tests, with no existing assertion edited.
   `tests/test_code_invalidation.py` and `tests/test_main.py::TestMain::test_execute` are the
   real gates (the completeness rules; the `check_dependencies=False` contract).
2. **Doc examples.** `python scripts/build_docs.py --check` — must not regenerate any
   `tests/test_docs_*.py`.
3. **Benchmark.** `python scripts/bench_traversal.py --no-memo` then `--memo`, from a scratch
   directory. Expected (counts are deterministic — 75 unique tasks, 43 with a `requires` spec;
   wall-clock on Windows varies ±30% for identical work, so **treat the counts as the metric**):

   | traversal | complete() exec | requires() exec | fingerprint exec | wall |
   | --- | --- | --- | --- | --- |
   | cold run() | 75 → 75 | 766 → 43 | 5,774 → 75 | 3.90s → 1.50s |
   | no-op run() | 1,428 → 75 | 586 → 43 | 8,439 → 75 | 1.77s → 0.05s |
   | recursive `complete()` | 1,428 → 75 | 376 → 43 | 5,552 → 75 | 1.57s → 0.04s |
   | `preview()` | 5,552 → 75 | 873 → 43 | 13,685 → 75 | 4.75s → 0.06s |
   | partial rerun | 1,428 → 75 | 796 → 43 | 11,294 → 75 | 2.24s → 0.16s |

   The invariant to check in every row is **one execution per unique task**. `preview()`'s 1,543
   *invocations* are one memo lookup per printed node: `print_tree` prints one node per **path**
   (1,428 nodes for these 75 tasks), which is inherent to printing a tree and is deliberately
   not addressed here — what mattered is that each node stopped cascading.
4. **Correctness cases a memo could make silently pass by returning a stale `True`** — each is a
   test in step 8, and each must be *seen to fail* when the corresponding clear is removed:
   a dependency rerunning mid-build (`_run_task` clears); an output deleted inside an open scope
   (`invalidate()` clears); a hand-materialization inside a `run()` body (`save()` clears); a
   `code_version` bump between traversals (fingerprint memo scoping); two flows sharing a
   `task_id` but writing to different directories (scope-per-build); a `cascade=False` load
   guard after a save (never memoized).
5. **Field verification without instrumenting.** `oryxflow.enable_logging(level='DEBUG')` prints
   one line per traversal: `traversal memo: complete 41/116, requires 86/129, fingerprint
   115/190 hit, 0 invalidation(s)`. The consumer project that reported ~24s can confirm the fix
   from that line alone.

## Implementation notes (divergences from the plan as built)

- **The escape hatch is a core-private flag, not `settings.traversal_memo`.** The plan put a
  `traversal_memo = True` knob in `oryxflow/settings.py` (§1, decision 12). As built it is
  `oryxflow.core._traversal_memo_enabled = True` instead, and `settings.py` is unchanged. Reason:
  the flag's real job is the test-equivalence toggle (case 3 asserts memo-on == memo-off); the
  "field rollback" justification is weak — a stale answer would almost always be a silent stale
  `True` a user could not easily notice, so a user-facing knob invites nobody to reach for it
  correctly. Keeping it out of `settings` (a user-facing surface) and in `core` (where the other
  memo internals live) matches "not something a user should have to think about" without pretending
  it is a supported user setting. `traversal_scope()` reads the module global directly rather than
  doing a lazy `from oryxflow import settings` import. The benchmark and the test suite set
  `core._traversal_memo_enabled` to toggle modes; field rollback, if ever needed, is the same
  one line.
- Everything else was built as specified. The `accept_code` dispatch loop is wrapped with a
  2-space-indented `for` under the `with` to keep the large loop body's diff minimal (valid, if
  unusual, indentation).

## Known remaining costs (deliberately out of scope)

Recorded so the next reader doesn't mistake them for oversights, and so a future report of
"preview is still slow on my DAG" lands on the right line:

- **`utils.print_tree` prints one node per DAG *path*** — 1,428 lines of output for 75 tasks.
  After this change each of those nodes is one memo lookup, so it is fast (0.06s), but the
  output volume itself still grows with paths, not tasks. Fixing that means changing what
  `preview()` shows (collapse repeated subtrees, or clip), which is a UX decision, not a
  performance one.
- **`core.dfs_paths`/`find_deps` are now memoized** (`_all_upstream`/`_reachable_to`/`_suffix_paths`,
  `core.py:843`+), added *after* this plan was written — this is the "separate, self-contained
  visited-set rewrite" this bullet used to defer. They are polynomial even on diamond DAGs, so the
  path-shaped blowup in `taskflow_downstream`/`invalidate_downstream`/`oryxflow.run(forced=...)`
  is already collapsed. The structural `requires()` memo still helps there — it is shared across
  the several `find_deps` calls in `run()`'s forced-task filter, and those walks resolve
  `requires()` via `_get_task_requires` → the memoized `_resolve_requires` — but it is no longer
  the headline win it was when this walk re-explored every path.
- **`TaskData._dep_state()` is not memoized, by design** (decision 3). It is the one recursive-
  looking method whose answer legitimately changes when a task runs.
- **`Advisor.advise()` already de-duplicates** via its own `advised` set
  (`codecheck.py:142`), so it is O(tasks) and needs nothing here — its cost was the
  fingerprint recursion it triggered per task, which this change removes.

## Rollout and rollback

- Behaviour-neutral by construction: outside an engine traversal every memo is `None`, so
  library consumers calling `t.complete()` directly are unaffected.
- Rollback without a release: `oryxflow.settings.traversal_memo = False` restores the
  un-memoized recursion process-wide, one line, no other change. The suite asserts the two modes
  agree (case 3), so the flag is a supported configuration, not a dead branch.
- Assumption worth stating once: **do not mutate `settings.check_dependencies`,
  `settings.code_version_auto` or a class's `code_version` from inside a traversal** (inside a
  task's `run()`, or between the two halves of a `preview()`). Nothing in the engine or the
  suite does; the memos would hold answers from before the change. Between traversals is always
  fine, which is what the tests do.

## Benchmark (the content of `scripts/bench_traversal.py`)

```python
"""Traversal cost of a fan-out DAG: how many times does the engine ask each question?

    python scripts/bench_traversal.py --no-memo      # baseline
    python scripts/bench_traversal.py --memo         # with the traversal-scoped memos

Reports calls/executions for the three recursive engine questions per traversal. Executions
are the metric: they are deterministic, where wall-clock on the same machine varies ~30% for
identical work. The invariant after the change is ONE execution per unique task.
"""
import sys
import time
import shutil
import collections

import pandas as pd

import oryxflow
from oryxflow import core
from oryxflow.tasks import TaskData

MARKETS = ['m{}'.format(i) for i in range(41)]     # 41 branches, as reported in the field
LEAVES = list(range(32))                           # one shared aggregator over 32 leaves
DATA = 'data-bench-traversal/'


class BLeaf(oryxflow.tasks.TaskPqPandas):
    i = oryxflow.IntParameter(default=0)
    def run(self):
        self.save(pd.DataFrame({'v': [self.i]}))


@oryxflow.requires_each(BLeaf, i=LEAVES)
class BAgg(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(self.inputLoadConcat())


@oryxflow.requires(BAgg)
class BNarr(oryxflow.tasks.TaskPqPandas):
    market = oryxflow.Parameter(default='m0')
    def run(self):
        self.save(self.inputLoad().assign(market=self.market))


@oryxflow.requires({'input': BAgg})                # the diamond: shared dep + fan-out
@oryxflow.requires_each(BNarr, market=MARKETS)
class BReport(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(self.inputLoadConcat(task='BNarr'))


# --- instrumentation: count invocations at the seam, executions at the uncached body. Before
# the change the split names don't exist, so executions == invocations and the table still lines
# up row for row.
C = collections.Counter()
_complete = TaskData.complete
_check = getattr(TaskData, '_complete_check', None)
_resolve = core._resolve_requires
_resolve_un = getattr(core, '_resolve_requires_uncached', None)
_fp = core.Task._code_fingerprint.fget
_fp_un = getattr(core.Task, '_code_fingerprint_compute', None)


def _install():
    def complete(self, cascade=True):
        C['c_call'] += 1
        return _complete(self, cascade=cascade)
    TaskData.complete = complete

    def resolve(task):
        C['r_call'] += 1
        return _resolve(task)
    core._resolve_requires = resolve
    core._spec_requires.__globals__['_resolve_requires'] = resolve

    def fingerprint(self):
        C['f_call'] += 1
        return _fp(self)
    core.Task._code_fingerprint = property(fingerprint)

    if _check is not None:
        def check(self, cascade):
            C['c_exec'] += 1
            return _check(self, cascade)
        TaskData._complete_check = check
    if _resolve_un is not None:
        def resolve_un(task):
            C['r_exec'] += 1
            return _resolve_un(task)
        core._resolve_requires_uncached = resolve_un
    if _fp_un is not None:
        def fp_un(self):
            C['f_exec'] += 1
            return _fp_un(self)
        core.Task._code_fingerprint_compute = fp_un


def measure(label, fn):
    C.clear()
    t = time.perf_counter()
    fn()
    elapsed = time.perf_counter() - t
    print('  {:<24} {:>6.2f}s  complete {:>6,}/{:>5,}  requires {:>5,}/{:>4,}  '
          'fingerprint {:>7,}/{:>5,}'.format(
              label, elapsed,
              C['c_call'], C['c_exec'] or C['c_call'],
              C['r_call'], C['r_exec'] or C['r_call'],
              C['f_call'], C['f_exec'] or C['f_call']))


def main():
    memo = '--no-memo' not in sys.argv
    oryxflow.settings.log_level = 'WARNING'
    oryxflow.settings.traversal_memo = memo
    shutil.rmtree(DATA, ignore_errors=True)
    oryxflow.set_dir(DATA)
    _install()
    flow = oryxflow.Workflow(BReport)
    n = 1 + len(MARKETS) + 1 + len(LEAVES)
    print('traversal_memo={}   {} tasks   calls/executions'.format(memo, n))
    try:
        measure('cold run()', flow.run)
        measure('no-op run()', flow.run)
        measure('recursive complete()', lambda: BReport().complete())
        measure('preview()', lambda: oryxflow.preview(BReport(), print_it=False))
        BAgg.code_version = '2'                    # invalidate one shared upstream
        measure('partial rerun', flow.run)
    finally:
        shutil.rmtree(DATA, ignore_errors=True)


if __name__ == '__main__':
    main()
```
