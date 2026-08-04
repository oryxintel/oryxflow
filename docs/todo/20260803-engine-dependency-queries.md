# Discoverable reverse-dependency queries on `Workflow` (`dependents` / `dependencies`)

## Context

An agent (or a data scientist) working in a flow repeatedly needs to answer **"what depends on
this task?"** — e.g. *"why does the quarterly report still drag a slow historical-backtest task
into a cold build, and by how many routes?"*. The capability to answer that already exists in the
library (`core.find_deps`, the same walk `reset_downstream` uses), but it is **unreachable at the
moment of asking**, so the walk gets hand-rolled with `grep` and a manual recursion over
`requires()` instead. That shortcut is chosen in about one second, on whatever is visible in the
tool surface — and today the graph query is not visible.

Concretely, the failure mode observed (paraphrased, sanitized):

- **`grep` can't tell a real edge from a mention.** `grep -rn "SlowUpstreamTask"` returns the
  `@requires` line, the import, a re-export in `tasks.py`, and every docstring —
  indistinguishable. It also can't tell whether an edge is reachable from *this flow's* root,
  which was the actual question.
- **So the walk gets hand-rolled.** ~40 lines of recursion over `task.requires()`. It fails first
  because `get_task()` raises `MissingParameterException` on a mid-DAG task in a fanned-out flow
  (its params are internal to the DAG, not supplied by flow params), and because `requires()`
  returns four different shapes (single task, list, dict, nested list) that must be normalized by
  hand. The hand-rolled `seen` set is keyed well enough for one answer and wrong in general.
- **The library already did all of it.** `core.find_deps(root, "SlowUpstreamTask")` — one call,
  by **family string**, so it never instantiates the mid-DAG task and sidesteps the
  `MissingParameterException` entirely. But it is module-level, absent from `Workflow`'s methods,
  absent from the skill docs read at session start, and the public wrapper is named
  `taskflow_downstream(task, task_downstream)` whose signature reads as the *opposite* of what it
  does (`task` is the upstream target; `task_downstream` is the root).

The fix is not more prose telling people the decorators are the DAG (that already exists, and the
walk still got hand-rolled). The fix is to make the graph query **cheaper than the text search at
the moment of asking**: a method, on the object already in scope (`flow`), sitting next to
`flow.reset_downstream(X)`, accepting the class you already have.

### Design decisions

- **`flow.dependents(X)` / `flow.dependencies(X)` as `Workflow` methods** (and `WorkflowMulti`
  with a `flow=` selector), not just better-documented module functions. Discoverability is the
  whole point: the method must be reachable from `flow.` autocomplete next to `reset_downstream`.
  Module-level `taskflow_*` stay as-is (used internally by `invalidate_*`).
- **`dependents` is the reverse lookup** — "what depends on `X`" = every task on a path from the
  flow **root** down to `X`'s family (the band that recomputes if `X` changes). This is exactly
  `find_deps(root, X_family)`. `dependencies` is the forward lookup — "what `X` depends on" = its
  upstream cone (`taskflow_upstream`).
- **Accept a class OR a family string OR an instance** (`_family_str`). `find_deps` takes a family
  *string*, which is precisely what sidesteps `MissingParameterException` on a fanned-out task —
  but you have to *know* that. The method normalizes internally so a caller who reaches for the
  class (the natural thing) is not punished for it. `.task_family` is a class attribute (set by
  the `Register` metaclass), so a bare class works without instantiation — same trick
  `reset_downstream` already relies on.
- **`paths=True` yields ordered `root -> ... -> X` sequences, deduped.** `find_deps` returns a
  *set*, which answers "is `X` reachable and what's between" but destroys path structure — you
  can't learn there is *exactly one route* (the finding that closed the real investigation) vs.
  many. `find_paths` (new) returns a list of ordered task lists. The old `dfs_paths` name promised
  paths but flattened; it stays as a back-compat generator.
- **Memoize the walk.** `dfs_paths` re-explored shared subgraphs with no memoization —
  exponential on a diamond-heavy DAG (dozens of tasks reachable from one report root). The set
  walk (`find_deps`) is now memoized per node → polynomial. `find_paths` memoizes shared
  path-suffixes; the number of *distinct paths* can still be large by nature (that is the
  question being asked), but shared work is not repeated.
- **Naming.** The honest public surface is the method names `dependents` / `dependencies`.
  `taskflow_downstream(task, task_downstream)` keeps its confusing-but-stable signature as a
  back-compat alias; its docstring is corrected to say plainly which argument is which.
- **Discoverability line in the skill docs** (plugin `reference.md`, `SKILL.md`) and the library
  `CLAUDE.md` public-API section — the cheapest, highest-yield item: one line naming the methods
  next to the reset ones.

### Explicitly deferred (not in this change)

- **A lint for declared inputs never read in `run()`.** The real underlying bug in the motivating
  case was a task that *declared* an upstream dependency and never read its data in `run()` — a
  real edge whose output was silently discarded, dragging a heavy upstream refit into every cold
  build. **No graph query catches this**: `dependents()` correctly reports the edge as legitimate,
  because it *is* a declared edge. Catching it needs static analysis (AST: which `inputLoad()`
  targets are unpacked into names, and are those names referenced in `run()`). Higher-value but
  orthogonal; specced separately in `20260803-engine-unused-input-lint.md`.
- `transitive=False` (direct-only), `instances=True` / class-collapsing, and a reset dry-run —
  none blocked the motivating case. Add only on request.

## Implementation

### 1. `core.py` — memoized graph walk with path structure

Replace the naive recursion (`core.py:843-857`, `dfs_paths` + `find_deps`) with a memoized set
walk, a memoized path walk, and back-compat shims. `_get_task_requires` (core.py:839) is unchanged.

```python
def _get_task_requires(task):
    return set(flatten(task.requires()))


def _all_upstream(root):
    """Every task reachable upstream of ``root`` (root included). Memoized by task_id."""
    out = {}
    stack = [root]
    while stack:
        t = stack.pop()
        if t.task_id in out:
            continue
        out[t.task_id] = t
        stack.extend(_get_task_requires(t))
    return set(out.values())


def _reachable_to(node, goal_family, cache):
    """Set of tasks on any path from ``node`` down to ``goal_family`` (node included when it is
    on such a path). Memoized per node -> polynomial even on diamond-heavy DAGs."""
    if node.task_id in cache:
        return cache[node.task_id]
    cache[node.task_id] = set()          # re-entry / cycle guard
    acc = set()
    for child in _get_task_requires(node):
        acc |= _reachable_to(child, goal_family, cache)
    if acc or node.task_family == goal_family:
        acc = acc | {node}
    cache[node.task_id] = acc
    return acc


def _suffix_paths(node, goal_family, cache):
    """List of ordered paths (each a list of tasks, ``node`` first) from ``node`` down to every
    occurrence of ``goal_family``. Memoized per node -> shared suffixes computed once."""
    if node.task_id in cache:
        return cache[node.task_id]
    cache[node.task_id] = []             # re-entry / cycle guard
    paths = []
    if node.task_family == goal_family:
        paths.append([node])
    for child in _get_task_requires(node):
        for sub in _suffix_paths(child, goal_family, cache):
            paths.append([node] + sub)
    cache[node.task_id] = paths
    return paths


def dfs_paths(start_task, goal_task_family, path=None):
    """Back-compat generator: yield tasks on paths from ``start_task`` to ``goal_task_family``
    (``goal_task_family=None`` yields the whole upstream DAG). Superseded by ``find_paths``
    (ordered paths) and ``find_deps`` (deduped set); both are memoized."""
    if goal_task_family is None:
        for t in _all_upstream(start_task):
            yield t
        return
    for p in _suffix_paths(start_task, goal_task_family, {}):
        for t in p:
            yield t


def find_deps(task, upstream_task_family):
    """Set of all tasks on all paths between ``task`` (the downstream root) and
    ``upstream_task_family``. ``upstream_task_family=None`` returns the whole upstream DAG."""
    if upstream_task_family is None:
        return _all_upstream(task)
    return _reachable_to(task, upstream_task_family, {})


def find_paths(task, upstream_task_family):
    """Ordered dependency paths (``task`` -> ... -> ``upstream_task_family``), deduped, root
    first. Returns a list of lists of task instances; empty if the family is unreachable."""
    seen = set()
    out = []
    for p in _suffix_paths(task, upstream_task_family, {}):
        key = tuple(t.task_id for t in p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out
```

Behavior preserved: `find_deps(task, fam)` returns the same set as before (union of all path
members ending at any occurrence of `fam`); `find_deps(task, None)` returns the whole upstream
DAG (matching the old flattened all-nodes yield). `dfs_paths` keeps its signature (the `path`
recursion-seed arg is retained but no longer needed).

### 2. `__init__.py` — `_family_str` helper + `find_paths` re-export

Near `_as_families` (core.py-adjacent helpers, `__init__.py:243`):

```python
def _family_str(x):
    """Normalize a task class / instance / family string to a family string. A class or instance
    exposes ``.task_family`` (a class attribute), so neither needs instantiating — that is what
    lets a fanned-out / DAG-internal family be looked up by name."""
    return x if isinstance(x, str) else x.task_family
```

Re-export `find_paths` alongside the existing `from oryxflow.core import ...` so
`oryxflow.find_paths` exists for advanced callers.

### 3. `__init__.py` — `Workflow.dependents` / `Workflow.dependencies`

Add next to `reset_downstream` / `reset_upstream` (after `__init__.py:697`):

```python
    def dependents(self, task, root=None, paths=False):
        """What (transitively) depends on ``task``, within this flow — the reverse lookup.

        Every task on a path from the flow root down to ``task``'s family: the band that would
        recompute if ``task`` changed. Same graph machinery as ``reset_downstream``, so the answer
        agrees with what a reset would invalidate.

        Args:
            task (class, str, instance): the family to look up. A CLASS or family STRING is
                preferred — neither is instantiated, so this works for fanned-out / DAG-internal
                families that ``get_task()`` cannot build.
            root (class, instance): flow root to scope the search from (default: the flow's
                default task). Only tasks reachable from ``root`` are in scope.
            paths (bool): if True, return the ordered ``root -> ... -> task`` paths (a list of
                lists of task instances) instead of the deduped set — use it to see HOW MANY
                distinct routes reach ``task``, not merely that it is reached.

        Returns: a set of task instances (``paths=False``), or a list of ordered paths
            (``paths=True``). Both the target family and the root are included.
        """
        root_inst = self.get_task(root)
        family = _family_str(task)
        if paths:
            return core.find_paths(root_inst, family)
        return core.find_deps(root_inst, family)

    def dependencies(self, task=None, target=None, paths=False):
        """What ``task`` depends on, within this flow — the forward lookup.

        With no ``target``, the whole upstream cone of ``task`` (default: the flow's default
        task). With ``target``, narrows to the band of tasks on all paths between ``task`` and
        ``target`` (identical to ``dependents(target, root=task)`` — the band is one thing viewed
        from either end).

        Args:
            task (class, str, instance): downstream anchor (default: the flow's default task).
                Must resolve with the flow's params when ``target`` is not given (it is walked
                directly); when ``target`` is given it may also be a bare class/string.
            target (class, str, instance): optional upstream family to stop at.
            paths (bool): with ``target``, return ordered ``task -> ... -> target`` paths instead
                of the set. Without ``target``, ``paths`` is ignored (a full upstream cone has no
                single goal to order toward).

        Returns: a set of task instances, or (``paths=True`` with ``target``) a list of paths.
        """
        if target is None:
            return set(taskflow_upstream(self.get_task(task)))
        anchor = self.get_task(task)
        family = _family_str(target)
        if paths:
            return core.find_paths(anchor, family)
        return core.find_deps(anchor, family)
```

`get_task(None)` already resolves the default task, so `dependents(X)` scopes from the flow root
with no extra argument, and `dependencies()` walks up from the default task.

### 4. `__init__.py` — `WorkflowMulti.dependents` / `.dependencies`

Mirror `reset_downstream` (`__init__.py:971`): a `flow=` selector picks one flow, else a dict
keyed by flow name.

```python
    def dependents(self, task, root=None, flow=None, paths=False):
        if flow is not None:
            return self.workflow_objs[flow].dependents(task, root=root, paths=paths)
        return {exp: self.workflow_objs[exp].dependents(task, root=root, paths=paths)
                for exp in self.params.keys()}

    def dependencies(self, task=None, target=None, flow=None, paths=False):
        if flow is not None:
            return self.workflow_objs[flow].dependencies(task, target=target, paths=paths)
        return {exp: self.workflow_objs[exp].dependencies(task, target=target, paths=paths)
                for exp in self.params.keys()}
```

### 5. `__init__.py` — correct `taskflow_downstream` docstring

Leave the signature (back-compat) but fix the docstring to name which arg is the root and which
is the target family, and point readers at `Workflow.dependents` as the discoverable form.

### 6. Docs

- Library `CLAUDE.md`: one line under the Workflow-objects method list naming
  `dependents` / `dependencies`.
- Plugin `skills/oryxflow/reference.md` (the reset block, ~line 406) and `SKILL.md`: add the
  two query methods next to `reset_downstream`, with the family-string / `paths=` notes.

## Files modified

- `oryxflow/core.py` — memoized `_all_upstream`, `_reachable_to`, `_suffix_paths`; rewritten
  `dfs_paths`, `find_deps`; new `find_paths`.
- `oryxflow/__init__.py` — `_family_str`; re-export `find_paths`; `Workflow.dependents` /
  `.dependencies`; `WorkflowMulti.dependents` / `.dependencies`; corrected
  `taskflow_downstream` docstring.
- `tests/test_main.py` — `dependents` / `dependencies` tests (set, paths, family-string on a
  fanned-out mid-DAG task, WorkflowMulti selector).
- `CLAUDE.md` — one-line method mention.
- (plugin repo) `skills/oryxflow/reference.md`, `skills/oryxflow/SKILL.md` — discoverability
  lines.

## Verification

```bash
python -m pytest tests/test_main.py tests/test_workflow.py \
    tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
```

Baseline to hold: **86 passing** before, **+ new tests** after. New tests assert:

- `flow.dependents(MidFamily)` returns the band between the root and the fanned-out mid family
  (root + mids + top), given `MidFamily` only as a **class** and as a **string** — proving the
  no-instantiation path (the mid family has a DAG-internal param and cannot be `get_task()`-built).
- `flow.dependents(Mid, paths=True)` returns ordered `root -> ... -> Mid` paths, and their count
  equals the number of distinct routes (one per fanned branch).
- `flow.dependencies()` equals `taskflow_upstream(default)`; `flow.dependencies(top, target=leaf)`
  equals `flow.dependents(leaf, root=top)`.
- `WorkflowMulti.dependents(X, flow='A')` returns a set; without `flow=` returns a per-flow dict.
