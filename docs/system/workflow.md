# Completion cascade, reset, and the Workflow objects

How "is this task done?" propagates through the DAG, and what `reset` / `reset_*` actually
invalidate. The two facts that surprise people:

1. **Completion cascades upstream by default** — a task is incomplete if anything *above* it is
   incomplete, even when its own output still exists.
2. **`reset(task)` invalidates only that one task** — not its downstream dependents. The cascade
   in (1) is what makes a single upstream `reset` enough to force a full rebuild on the next run.

## Completion cascades upstream (TaskData)

There are two `complete()` implementations:

- **`core.Task.complete()`** (`oryxflow/core.py`, `complete` ~line 289) — base; checks **only its
  own** outputs (`all(output.exists())`). No cascade. Only hit by tasks that don't subclass
  `TaskData`.
- **`TaskData.complete(cascade=True)`** (`oryxflow/tasks/__init__.py`, `complete` ~line 75) — what
  all real tasks use. Checks its own outputs **and**, when
  `settings.check_dependencies` is on (default `True`) and the task isn't `external`, requires
  `all(t.complete() for t in flatten(self.requires()))`. Each `t.complete()` is itself a
  `TaskData.complete()`, so this **recurses over the entire upstream DAG**.

Consequence: with the default `check_dependencies=True`, a downstream task whose own file exists
still reports **incomplete** if any ancestor's output is missing. Set
`oryxflow.settings.check_dependencies=False` (`settings.py:11`) to make `complete()` check only
the task itself — then a present output is "done" regardless of upstream state. User-facing docs:
`docs/docs/run.md`.

## How the engine uses it

`build()` (`oryxflow/core.py`, `build` ~line 454; inner `_process` ~line 475) checks
`task.complete()` **before** recursing into deps (~line 480). Because `TaskData.complete()`
already cascades, a stale-upstream task reports incomplete here, so `_process` proceeds to
process its `requires()` and the whole chain gets rebuilt in dependency order. (If
`check_dependencies=False`, the top task short-circuits as complete and the run no-ops, leaving
stale downstream outputs in place.)

## reset only invalidates the named task

- `Task.reset(confirm)` → `invalidate()` (`oryxflow/tasks/__init__.py`, `reset` ~line 50) — deletes
  **that task's** output only. No downstream walk.
- `Workflow.reset(task)` (`oryxflow/__init__.py` ~line 503) — thin wrapper: `get_task(task).reset()`.
- `invalidate()` deletes the data outputs **and** the task's metadata files (the `saveMeta`
  pickle and `saveMetaJson` json), via `_invalidate_meta` (`tasks/__init__.py` ~line 75). Metadata
  lives *outside* `output()` (separate path: `_getpath('meta').with_suffix('.pickle'|'.json')`,
  `_get_meta_path` ~line 365), so it has to be cleaned up explicitly here. Missing meta files are
  ignored (`FileNotFoundError` swallowed), so a reset is safe whether or not meta was ever saved.
- To invalidate dependents too, use `reset_downstream` / `reset_upstream`
  (`__init__.py` ~line 507 / ~line 522), which delegate to `invalidate_downstream` /
  `invalidate_upstream`. `reset_downstream(task)` with `task_downstream=None` resolves the
  downstream target to the workflow's `default_task` (via `get_task(None)`).

**Practical upshot:** with the default `check_dependencies=True`, `flow.reset(UpstreamTask)`
followed by `flow.run()` rebuilds the whole downstream chain — the cascade forces it. You only
*need* `reset_downstream` when `check_dependencies=False`, or when you want the downstream outputs
physically deleted rather than just recomputed.

## WorkflowMulti reset iterates flows, not tasks

`WorkflowMulti.reset(task=None, flow=None, confirm=False)` (`oryxflow/__init__.py` ~line 724):

- `flow=...` → delegates to that one flow's `Workflow.reset`.
- `flow=None` → asks for confirmation **once**, then resets across **every** flow:
  `{self.workflow_objs[exp_name].reset(task, confirm=False) for exp_name in self.params.keys()}`.

So it covers all flows, but each call still only resets the single `task` passed (per the section
above). `reset_downstream` / `reset_upstream` on `WorkflowMulti` (~line 734 / ~line 744) fan out
the same way. The construction of one `Workflow` per experiment happens in `__init__`
(~line 596: `{k: Workflow(...) for k, v in self.params.items()}`).

Note the return value is a **set comprehension** — results are deduped/unordered, not a per-flow
dict. Don't rely on it to report what happened per flow.

## Related

- Completion is *output existence*, and the path is derived from `task_id` — see
  [targets.md](targets.md) for identity and on-disk layout.
- `check_dependencies` is the single switch separating "cascade" from "check self only"; it's
  mutable global state in `settings.py`, so a project's `cfg` may flip it.
