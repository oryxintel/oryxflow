# Composable fan-out: stacking `@requires_each` with other dependencies

## Context

`@requires_each(Task, param=[...])` declares one dependency per value and is the supported way
to build a fan-out. It works for the pure case — N branches converging into one combining task —
and nothing else.

The shape it does not cover is the normal one. A combining task almost always needs the fan-out
**and** a shared, un-fanned dependency:

- the enumeration's source data — which items exist, plus their non-fanned fields
- a baseline to compare branches against (`@requires_each(ModelTrain, model=MODELS)` on a
  comparison task usually also wants the test set, or a benchmark, to score against)
- labels / metadata for rendering

A worked example, which the rest of this plan uses. `RegionNarrative` makes one expensive LLM
call per region. `ReportInput` holds the per-region drivers table (feature contributions, raw
values, percentiles, recommendation) — deliberately *not* inside the narratives, because if it
were, changing how a table is formatted would re-bill every LLM call. `Report` needs both:

```python
@oryxflow.requires({'input': ReportInput})                       # the shared half
@oryxflow.requires_each(RegionNarrative, region=cfg.REGIONS)     # the fan-out half
class Report(oryxflow.tasks.TaskMarkdown):
    def run(self):
        ...
```

That does not work today. Both orders raise, and the message is wrong:

```
TypeError: Report: defines requires() AND is decorated with @requires -- the decorator would
silently replace it. Keep one: drop the decorator to write requires() yourself (use
self.requires_grid(...) for a fan-out), or delete requires() and let the decorator declare
the dependency.
```

`Report` defines no `requires()`. The first decorator's `setattr(cls, 'requires', fn)` puts
`requires` in `cls.__dict__`, and the second decorator's `_check_requires_not_overwritten`
misreads that as hand-written. Each decorator *owns* `requires()` outright, so they cannot
compose.

The workaround is a cliff, out of all proportion to "one more dependency":

```python
@oryxflow.inherits(ReportInput)            # inherits, not requires -- nothing signposts this
class Report(oryxflow.tasks.TaskMarkdown):
    def requires(self):
        deps = {'input': self.clone_parent()}
        deps.update(self.requires_grid(RegionNarrative, region=cfg.REGIONS))
        return deps
```

It costs the author three things:

1. **You have to know to switch `requires` → `inherits`.** Only findable by reading the source.
2. **The parameter handling is handed back to you.** `@requires_each` copies the dependency's
   parameters minus the fanned ones; hand-rolling means reasoning about `clone()` overlap
   yourself — the exact bug class `@requires_each` exists to remove.
3. **Flat key namespace.** `'input'` and every region name share one dict, so a region literally
   named `input` silently replaces the shared dependency. More routinely, `run()` cannot tell
   branches from shared dependencies, so every combining task hand-writes the same bookkeeping:
   pop the keys you know, iterate the rest.

### A second bug, reachable today without stacking

The parameter-exclusion rule is already broken. A combining task that declares the fanned
parameter in its own body keeps it:

```python
@oryxflow.requires_each(Branch, market=['x', 'y'])
class Combo(oryxflow.tasks.TaskCache):
    market = oryxflow.Parameter(default='zzz')      # the mistake
```
```
params:  ['date_asof', 'market']
task_id: Combo_2020_01_01_zzz_28222f9046            # 'zzz' is in the combiner's identity
```

`@requires_each` skips the fanned names when *copying* parameters, but a class-body declaration
wins the `hasattr` check and survives. The result is one combining task per value, each
combining all branches — N identical outputs at N times the cost, cached under different ids,
with no warning.

### Intended outcome

```python
@oryxflow.requires({'input': ReportInput})
@oryxflow.requires_each(RegionNarrative, region=cfg.REGIONS)
class Report(oryxflow.tasks.TaskMarkdown):

    def run(self):
        deps = self.inputLoad(flatten=False)
        drivers = deps['input']
        for region, narrative in deps['RegionNarrative'].items():
            ...
```

Decorators stack and merge instead of replacing each other; the fan-out is addressable as a
group without hand-written bookkeeping; and the two silent-wrong-answer paths above raise.

### Design decisions

Every decision below was reviewed and confirmed with the maintainer before this plan was
written.

**1. Additive decorators via an internal spec, not a `context=` parameter.** Rejected:
`requires_each(Task, region=[...], context={'input': ReportInput})`. `**grid` is the
dependency's *parameter namespace* — every reserved keyword steals a name nobody can fan out
over, and `context` is a plausible parameter name in data-science code. It is also a second
spelling of `@requires`, which already means "fixed context dependency", and would have to stay
behaviourally identical to it forever. Instead every decorator becomes a *contributor* to one
per-class spec, resolved into a single generated `requires()`.

**2. No varargs form.** Rejected: `@requires_each(A, B, region=REGIONS)`. Once stacking exists it
adds no capability — two fan-out tasks over one grid is two stacked decorators — and it does not
dodge the problem it appears to solve: both spellings produce colliding keys, because a
single-parameter grid keys on the bare value.

**3. Collisions raise; keys are never auto-qualified.** Rejected: qualifying keys with the task
family whenever there is more than one fan-out. That makes key shape *conditional* — adding a
second fan-out silently rewrites every key of the first, and `inputLoad(task=...)` returns
differently-keyed dicts depending on how many groups you happen to have. Instead an optional
named-group form disambiguates explicitly, mirroring the `{name: Task}` form `@requires`
already has, and the resolver raises on any duplicate key. Unnamed single fan-out keeps bare
value keys exactly as today — not a breaking change.

**4. Naming is optional everywhere.** A group's name defaults to the dependency's
`task_family`, so grouping works with no naming at all; the dict form only overrides the label,
for collisions or readability.

**5. Grouping happens in the *return value*, not in `requires()`.** `requires()` and `input()`
stay flat dicts. A nested `input()` is genuinely ambiguous: `{'narrative': {'north': target}}`
is indistinguishable from a multi-`persists` dependency, which is already `{persist: target}`.
`inputLoad(flatten=False)` regroups the flat dict on the way out, using the spec. The engine
(`flatten`, `getpaths`, `find_deps`, `build`) is untouched.

**6. `flatten=` rather than a new `inputLoadEach()` accessor.** A fourth accessor is a fourth
concept; `flatten=` preserves the return contract (still a dict of loaded data, just grouped).
Also rejected: `fanout=True` — `task=` already means "select a dependency", so extending it to
accept a group name covers the same need with no new parameter.

**7. Rejected: `inputLoad(concat=True)` as an alias for `inputLoadConcat()`** — proposed and
then explicitly declined by the maintainer. It would change the return type on a flag (dict to
DataFrame) and pull `tag`/`tagkeys`/`concat_fn` onto `inputLoad` where they are meaningless when
the flag is off. CLAUDE.md already records the opposite call for the mirror method:
`outputLoadConcat` is "a *separate* method by design, not an `outputLoad` kwarg". `flatten=` is
different in kind — it does not change the contract. Do not add the alias later without
revisiting this.

**7b. `@requires` stops defining `requires()` via `clone_parent()`/`clone_parents()`.** The
resolver builds dependencies straight from the spec, so stacking two `@requires` no longer
depends on whichever decorator's `clone_parents` attribute happened to survive. The helpers
themselves stay — they are public surface and hand-written `requires()` bodies use them — they
are simply no longer load-bearing for the generated one. Confirmed with the maintainer, who does
not use the clone helpers.

**8. Callable grid values are supported.** Previously argued against, on the grounds that a
lambda in a decorator is a step up for a data-science audience and the escape hatch was rare.
Stacking inverts that: "grid depends on my own parameters" *and* "I also need a shared
dependency" has no decorator path at all otherwise, which is precisely the cliff this work
removes. Grid *names* stay static — only values are lazy — so parameter copying is unaffected.

**9. Both silent-wrong-answer paths raise, accepting the breaking change.** Declaring a fanned
parameter on the combining task, and key collisions, both currently produce a wrong DAG with no
diagnostic. Anything relying on today's behaviour is already producing wrong task ids or a
truncated DAG.

**10. `inputLoadConcat()` needs a guard.** It iterates *every* entry of `requires()`. Once a
shared dependency can sit alongside a fan-out, it gets row-stacked into the fan-out result. This
is not hypothetical — `requires(A, B)` with unrelated schemas today yields a silent union frame
(`['market','shap','o']`, 3 rows, no complaint). Default stays `flatten=True` (breaking it is not
worth it); a warning fires on the newly-reachable mistake, under an exact condition rather than
a heuristic.

**11. Bump `python_requires` to `>=3.9` and use positional-only (`/`) syntax.** `setup.py`
declares `>=3.5`, which is already inaccurate — `parameter.py`, `tasks/__init__.py`,
`targets/__init__.py`, `utils.py` and others use f-strings, which need 3.6. 3.8 reached
end-of-life in October 2024, and `install_requires` already forces 3.9 in practice (current
pandas and pyarrow both require it). Making `task_to_require` positional-only closes a latent
bug permanently: today a dependency with a parameter named `task_to_require` cannot be fanned
out over, because `requires_each(Dep, task_to_require=[...])` is a duplicate-argument
`TypeError`.

## Implementation

### 1. `core.py` — the spec

Add above `_check_requires_not_overwritten` (currently `core.py:457`).

A spec entry is one of:

```python
{'kind': 'inherit', 'tasks': (Cls, ...)  or {name: Cls}}   # params only, no dependency
{'kind': 'fixed',   'tasks': (Cls, ...)  or {name: Cls}}   # params + dependency
{'kind': 'each',    'cls': Cls, 'group': str or None, 'grid': {name: values-or-callable}}
```

```python
def _spec_entries(cls):
    """This class's own spec list (never a base class's -- copy on first write)."""
    if '_requires_spec' not in cls.__dict__:
        cls._requires_spec = list(cls.__dict__.get('_requires_spec', []))
    return cls._requires_spec


def _entry_sources(entry):
    """The task classes an entry copies parameters from."""
    if entry['kind'] == 'each':
        return [entry['cls']]
    tasks = entry['tasks']
    return list(tasks.values()) if isinstance(tasks, dict) else list(tasks)


def _add_spec(cls, entry):
    _spec_entries(cls).append(entry)
    _apply_spec(cls)
    return cls
```

### 2. `core.py` — `_apply_spec`, order-independent

Re-runs from scratch after every decorator, so decorator order cannot matter.

```python
def _apply_spec(cls):
    spec = cls.__dict__.get('_requires_spec', [])
    fanned = set()
    for entry in spec:
        if entry['kind'] == 'each':
            fanned.update(entry['grid'])

    injected = set(cls.__dict__.get('_requires_injected', ()))

    # (a) a fanned parameter declared in the class body is a mistake, not something to drop
    for name in sorted(fanned):
        if isinstance(cls.__dict__.get(name), Parameter) and name not in injected:
            raise TypeError(
                "{}: declares a '{}' parameter and also fans out over '{}'. The task the "
                "branches converge into must not carry the fanned parameter -- it would put "
                "one branch's value in the combining task's task_id. Remove the "
                "declaration.".format(cls.__name__, name, name))

    # (b) drop parameters a previous decorator injected that a later one fans out over
    for name in sorted(injected & fanned):
        delattr(cls, name)
        injected.discard(name)

    # (c) copy parameters from every entry, skipping every fanned name globally
    for entry in spec:
        for src in _entry_sources(entry):
            for pname, pobj in src.get_params():
                if pname in fanned or hasattr(cls, pname):
                    continue
                setattr(cls, pname, pobj)
                injected.add(pname)

    cls._requires_injected = injected

    if any(entry['kind'] != 'inherit' for entry in spec):
        cls.requires = _spec_requires
```

Note (c): the skip is **global**, not per-decorator. `@requires(X)` copies all of `X`'s
parameters, `@requires_each(Y, region=...)` copies all of `Y`'s minus `region`. Stacked and
handled per-decorator they disagree — if `X` also had a `region` parameter, `@requires` would
copy it onto the combining task, which is exactly what `@requires_each` exists to prevent, and
the failure is silent.

### 3. `core.py` — the resolver

```python
def _resolve_requires(task):
    """Return (deps, groups). deps is what requires() yields; groups maps a fan-out group
    name to the list of deps keys it produced."""
    spec = type(task).__dict__.get('_requires_spec') or getattr(type(task), '_requires_spec', [])
    single = len(spec) == 1

    # preserve today's shapes when there is exactly one entry
    if single and spec[0]['kind'] == 'fixed':
        tasks = spec[0]['tasks']
        if isinstance(tasks, dict):
            return {name: task.clone(cls) for name, cls in tasks.items()}, {}
        if len(tasks) == 1:
            return task.clone(tasks[0]), {}
        return [task.clone(cls) for cls in tasks], {}
    if single and spec[0]['kind'] == 'each':
        entry = spec[0]
        grid = task.requires_grid(entry['cls'], **entry['grid'])
        name = entry['group'] or entry['cls'].task_family
        return grid, {name: list(grid)}

    # mixed spec -> one flat dict; fixed deps keyed by name or task_family
    deps, groups, origin = {}, {}, {}

    def _put(key, dep, owner):
        if key in deps:
            raise ValueError(
                "{}: dependency key {!r} is produced by both {} and {}. Name one of them: "
                "@requires_each({{'<name>': {}}}, ...) or "
                "@requires({{'<name>': {}}}).".format(
                    type(task).__name__, key, origin[key], owner, owner, owner))
        deps[key] = dep
        origin[key] = owner

    for entry in spec:
        if entry['kind'] == 'inherit':
            continue
        if entry['kind'] == 'fixed':
            tasks = entry['tasks']
            pairs = tasks.items() if isinstance(tasks, dict) \
                else [(cls.task_family, cls) for cls in tasks]
            for name, cls in pairs:
                _put(name, task.clone(cls), cls.task_family)
        else:
            grid = task.requires_grid(entry['cls'], **entry['grid'])
            name = entry['group'] or entry['cls'].task_family
            for key, dep in grid.items():
                _put(key, dep, entry['cls'].task_family)
            groups[name] = list(grid)
    return deps, groups


def _spec_requires(_self):
    return _resolve_requires(_self)[0]


_spec_requires._oryxflow_generated = True
```

`_spec_requires` is a single shared function object, so the `_oryxflow_generated` marker is set
once.

### 4. `core.py:457` — fix `_check_requires_not_overwritten`

Only fire for a genuinely hand-written `requires()`:

```python
def _check_requires_not_overwritten(task_cls, decorator):
    """A hand-written ``requires()`` would be silently replaced by the decorator's."""
    existing = task_cls.__dict__.get('requires')
    if existing is not None and not getattr(existing, '_oryxflow_generated', False):
        raise TypeError(
            "{}: defines requires() AND is decorated with @{} -- ...".format(...))   # unchanged
```

Message body stays as-is; it is correct now that it only fires for the real case.

### 5. `core.py:308` `requires_grid()` — callable values

At the top of the loop that validates values, resolve callables against `self`:

```python
        resolved = {}
        for name in names:
            values = grid[name]
            if callable(values):
                values = values(self)
            if not isinstance(values, (list, tuple)):
                raise ValueError(
                    '{}: requires_grid values must be a list of values to fan out over, got {} '
                    'for {}'.format(self.task_family, repr(values), name))
            resolved[name] = values
```

then use `resolved` in the `itertools.product`. Add to the docstring: the callable receives this
task and may read its **parameters**; it may not read its inputs (`self.inputLoad()` inside
`requires()` recurses infinitely, because `input()` is defined as `getpaths(self.requires())`).

### 6. `core.py:467,514,554` — decorators become contributors

`inherits.__call__`: keep the `clone_parent`/`clone_parents` helpers exactly as they are (public
surface), but replace the parameter-copying loop with
`return _add_spec(task_that_inherits, {'kind': 'inherit', 'tasks': self.tasks_to_inherit or self.kw_tasks_to_inherit})`
after installing the helpers.

`requires.__call__`:

```python
    def __call__(self, task_that_requires):
        _check_requires_not_overwritten(task_that_requires, 'requires')
        tasks = self.tasks_to_require or self.kw_tasks_to_require
        inherits(*self.tasks_to_require, **self.kw_tasks_to_require)(task_that_requires)
        # the inherits call above appended an 'inherit' entry; upgrade it in place
        _spec_entries(task_that_requires)[-1]['kind'] = 'fixed'
        _apply_spec(task_that_requires)
        return task_that_requires
```

It no longer defines `requires()` via `clone_parent()`/`clone_parents()` — the resolver builds
the dependencies directly from the spec, so stacking two `@requires` no longer depends on which
one's `clone_parents` survived.

`requires_each.__init__` takes the task positional-only (see decision 11) and accepts an
optional single-entry `{name: Cls}` dict:

```python
class requires_each:
    def __init__(self, task_to_require, /, *extra, **grid):
        if extra:
            raise TypeError(
                'requires_each takes exactly one task (or one {name: task} dict). For two '
                'fan-outs, stack two @requires_each decorators.')
        group = None
        if isinstance(task_to_require, dict):
            if len(task_to_require) != 1:
                raise TypeError(
                    'requires_each takes one named task, e.g. '
                    "@requires_each({'narrative': RegionNarrative}, region=[...])")
            group, task_to_require = list(task_to_require.items())[0]
        if not grid:
            raise TypeError(...)            # unchanged
        for name, values in grid.items():
            if not callable(values) and not isinstance(values, (list, tuple)):
                raise TypeError(...)        # unchanged, plus the callable exemption
        self.task_to_require, self.group, self.grid = task_to_require, group, grid

    def __call__(self, task_that_requires):
        _check_requires_not_overwritten(task_that_requires, 'requires_each')
        return _add_spec(task_that_requires, {'kind': 'each', 'cls': self.task_to_require,
                                              'group': self.group, 'grid': self.grid})
```

### 7. `__init__.py:331,363` — `dict_inherits` / `dict_requires`

Same conversion. `dict_inherits.__call__` keeps installing `clone_parents_dict` and then calls
`core._add_spec(cls, {'kind': 'inherit', 'tasks': self.tasks_to_inherit})`. `dict_requires.__call__`
calls `_check_requires_not_overwritten`, delegates to `dict_inherits`, upgrades the last entry's
`kind` to `'fixed'`, re-applies, and no longer sets `requires`.

`__init__.py:446 requires_each(...)` becomes
`def requires_each(task_to_require, /, *extra, **grid)` and forwards to
`core.requires_each`. Extend its docstring with the named-group form, the
stacking form, and the callable form; replace the closing paragraph ("write `requires()`
yourself and call `requires_grid`") — that escape hatch is now for cases the decorators still do
not cover, not for a dynamic grid.

### 8. `tasks/__init__.py:224` — `inputLoad(flatten=True)`

Add `flatten=True` to the signature. Extend the `task=` lookup to accept a group name, and
regroup on the way out:

```python
    def inputLoad(self, keys=None, task=None, cached=False, as_dict=False, flatten=True):
        groups = {}
        try:
            from oryxflow.core import _resolve_requires
            _, groups = _resolve_requires(self)
        except Exception:
            pass                                  # hand-written requires(): no groups
        if task is not None and task in groups:
            # select a whole fan-out group
            return {k: self.inputLoad(keys=keys, task=k, cached=cached, as_dict=as_dict)
                    for k in groups[task]}
        ...                                       # existing body, unchanged
        if not flatten and isinstance(data, dict) and groups:
            data = _regroup(data, groups)
        return data
```

`_regroup` (module-level helper in `tasks/__init__.py`): keys belonging to a group nest under
the group name, everything else stays at the top level. A non-dict `data` (single dependency, or
positional list) is returned unchanged — `flatten=False` is a no-op there.

### 9. `tasks/__init__.py:281` — `inputLoadConcat(task=None, flatten=True)`

- `task=<group name>` — concatenate only that group's dependencies.
- `task=<dependency key>` — concatenate that one dependency.
- `flatten=False` — return `{name: DataFrame}`: each group concatenated within itself, each
  ungrouped dependency its own entry.
- No selector, and the spec holds **both** a fan-out group and a non-fan-out dependency — warn:

```python
            warnings.warn(
                "inputLoadConcat() is stacking the fan-out branches together with {} shared "
                "dependency/dependencies ({}). Pass task='{}' to concatenate just the fan-out, "
                "or flatten=False to get one frame per group.".format(
                    len(shared), ', '.join(map(repr, shared)), first_group),
                UserWarning, stacklevel=2)
```

The condition is exact (read off the spec), not a guess, and it can only fire on a shape that
was unreachable before this change.

### 10. `setup.py:33` — minimum Python

```python
    python_requires='>=3.9',
```

and add the version classifiers the package currently lacks, so PyPI and resolvers see the real
floor:

```python
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
```

This is a metadata correction, not a support drop: the declared `>=3.5` never held (f-strings
are used throughout the package), and `install_requires` already pins the real floor via pandas
and pyarrow. Mention it in `CHANGELOG.md` under **Changed** so it is not a surprise.

Ship it in the **same commit** as the feature rather than splitting it out — step 6's
positional-only `requires_each(task_to_require, /, ...)` does not parse below 3.8, so a commit
with the feature and not the bump would declare support for interpreters that cannot import the
package. Splitting would mean ordering the bump first as its own release, which buys nothing.

### 11. `CLAUDE.md`

Update the **Fan-out (one dep per value)** bullet: decorators stack; group naming; callable
grids; `inputLoad(task=<group>)` / `flatten=False`; `inputLoadConcat(task=)`. Update the
**Task-body idiom** three-tier paragraph to mention `flatten=False` and note that grouping is a
return-value shape only — `requires()`/`input()` stay flat.

### 12. `docs/docs/advtasksdyn.md`

Two additions and one rewrite.

**(a) New section, after the model-comparison example: "Combining a fan-out with shared
context".** The `Report` / `ReportInput` / `RegionNarrative` example from the Context section
above, with real captured `preview()` output, the `run()` body using
`inputLoad(flatten=False)`, and the named-group form shown as the disambiguation tool.

**(b) The reset argument, stated as the reason to use `requires_each` at all.** It is currently
undersold — the page reads as though the decorator is tidier. It is not: tasks spawned by a
nested `Workflow` inside `run()` are invisible to `core.find_deps`, so a targeted reset silently
skips them and you get fresh inputs wrapped in stale outputs, with a green run and no warning.
Cross-link `advparam.md`'s "Avoid building a flow inside a task".

**(c) Rewrite the "Fully Dynamic" section.** It currently says this "doesn't work yet, and it's
actually quite rare that you need it", which is both inaccurate (the generator-`run()` form does
work) and the wrong framing. Replace with the pattern, named: **fan out over the superset and
let branches no-op.** When the enumeration is plain domain data but the *usable* subset is
data-dependent, keep the DAG static over the full list and have each branch check for its own
data and save a placeholder without doing the expensive work. Empty branches cost nothing, the
DAG stays visible to `preview()` and reachable by reset. Then state the real limits: a grid
callable sees parameters, not inputs; a generator `run()` can yield tasks but they are invisible
to `preview()` and unreachable by `traverse()`, so reset cannot target them.

### 13. `docs/docs/advparam.md`

In "Avoid building a flow inside a task", add the shared-context case to the `requires_each`
fix, so the page does not imply the decorator only handles the pure fan-out.

### 14. `CHANGELOG.md`

- **Added** — stacking `@requires`/`@inherits`/`@requires_each`; named fan-out groups; callable
  grid values; `inputLoad(flatten=)`, `inputLoad(task=<group>)`, `inputLoadConcat(task=, flatten=)`.
- **Changed / BREAKING** — declaring a fanned-out parameter on the combining task now raises
  (previously kept it, corrupting the combining task's `task_id`); duplicate dependency keys now
  raise (previously one dependency silently replaced another).
- **Changed** — `python_requires` raised from `>=3.5` to `>=3.9`, correcting metadata that never
  held (the package has used f-strings, which need 3.6, for some time) and matching the floor
  pandas and pyarrow already impose.
- **Fixed** — `_check_requires_not_overwritten` no longer misreports a decorator-generated
  `requires()` as hand-written; `inputLoadConcat()` warns when it would stack a shared
  dependency in with the fan-out branches.

## Files modified

| File | Change |
| --- | --- |
| `oryxflow/core.py` | spec accumulator (`_spec_entries`, `_add_spec`, `_entry_sources`), `_apply_spec`, `_resolve_requires`, `_spec_requires`; `_check_requires_not_overwritten` marker check; callable grid values in `requires_grid`; `inherits`/`requires`/`requires_each` become spec contributors; `requires_each` gains `*args` + named-group form |
| `oryxflow/__init__.py` | `dict_inherits`/`dict_requires` become spec contributors; `requires_each(*args, **grid)` wrapper + docstring |
| `oryxflow/tasks/__init__.py` | `inputLoad(flatten=)` + group-aware `task=`; `_regroup` helper; `inputLoadConcat(task=, flatten=)` + shared-dependency warning |
| `setup.py` | `python_requires` `>=3.5` -> `>=3.9` (the old floor never held); version classifiers added |
| `tests/test_main.py` | new tests, listed under Verification |
| `CLAUDE.md` | fan-out bullet + task-body idiom |
| `docs/docs/advtasksdyn.md` | shared-context section; reset argument; "Fully Dynamic" rewritten as the superset pattern |
| `docs/docs/advparam.md` | shared-context case in the anti-pattern fix |
| `CHANGELOG.md` | Added / Changed(BREAKING) / Fixed |

## Verification

### Tests to add (`tests/test_main.py`)

1. `test_requires_each_stacks_with_requires` — the `Report` shape. Assert `requires()` has
   `1 + len(REGIONS)` keys, that `'input'` is among them, that the combining task's parameters
   do **not** include `region`, and that `preview()` lists every branch.
2. `test_requires_each_stacks_either_order` — the same two decorators reversed produce an equal
   `requires()` dict and equal `task_id`.
3. `test_requires_each_stacks_two_fanouts` — two `@requires_each` over *different* grids compose;
   both groups present.
4. `test_requires_each_declared_fanned_param_raises` — the `Combo`/`market='zzz'` case raises
   `TypeError`.
5. `test_requires_each_key_collision_raises` — two unnamed fan-outs over the same grid raise
   `ValueError`; naming one of them via `{'chart': Chart}` resolves it and yields distinct keys.
6. `test_requires_each_callable_grid` — `region=lambda self: REGIONS[self.sector]` produces the
   right branches for two different `sector` values.
7. `test_input_load_flatten_false` — `{'input': ..., 'RegionNarrative': {region: ...}}`; and
   `inputLoad(task='RegionNarrative')` returns the group dict.
8. `test_input_load_concat_shared_dep_warns` — no-selector call warns; `task=<group>` does not
   and returns only the branches.
9. `test_requires_handwritten_still_raises` — a hand-written `requires()` plus any decorator
   still raises `TypeError` (the guard must not be defeated by the marker).

### Backwards compatibility to hold

Single-decorator shapes must be byte-identical to today. Confirmed current behaviour to assert
against:

```
requires(Ctx, Other)                     -> list  [Ctx(), Other()]
requires({'ctx':.., 'other':..})         -> dict  {'ctx':.., 'other':..}
requires_each(Branch, market=['x','y'])  -> dict  {'x':.., 'y':..}
requires(Ctx)                            -> Ctx()          (single task, not a list)
```

### Commands

```bash
python -c "import sys; assert sys.version_info >= (3, 9), sys.version"   # positional-only syntax
python -m pytest tests/test_main.py tests/test_workflow.py \
    tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
python scripts/build_docs.py --check
```

Baseline to hold: **167 passing** before this change; the new tests above are additive, so the
target is 167 + the number added, with no pre-existing test modified. If an existing test does
need to change, that is a signal a shape regressed — investigate before rebaselining.
`build_docs.py --check` must still report `OK: site built to ./site`.

A benign `UserWarning: datatable failed` and sklearn convergence warnings are expected. Two
tests (`test_functional_Flow`, `test_tasks_with_dependencies_outputloadall`) have been observed
to fail intermittently and pass on rerun and in isolation — consistent with the Windows
filesystem `rmtree` race that `_rmtree_robust` documents. Do not treat a single such failure as
caused by this change; rerun before investigating.

## Implementation notes (divergences from the plan as built)

**1. Naming a group qualifies its dependency keys; `groups` carries labels, not a key list.**
The plan had `groups` as `{group name: [dependency keys]}` and treated the group name as a pure
label. That does not actually resolve a collision — the whole point of decision 3 and test 5.
Two fan-outs over `region=REGIONS` both produce keys `north`/`south`/`east` in one flat dict, and
naming the group leaves those keys untouched, so the resolver still raised.

Built instead: an explicitly named group **prefixes** its dependency keys with the name
(`chart_north`), and `groups` maps `{group name: {dependency key: branch label}}`. The label stays
the bare value, so `inputLoad(flatten=False)` yields `{'chart': {'north': ...}}` for a named group
and `{'RegionNarrative': {'north': ...}}` for an unnamed one — the same shape either way. An
unnamed group is unchanged from today (bare value keys), so decision 3's "key shape must not be
conditional" still holds: it is conditional on *naming*, which is explicit and local, not on how
many groups happen to exist.

The single-`'each'` fast path in `_resolve_requires` was dropped as dead code — for a lone unnamed
fan-out the general loop already produces exactly `grid`. The single-`'fixed'` fast path stays,
because that one really does have distinct shapes (single task / list / dict) to preserve.

**2. `inputLoad` reads groups through `core._requires_groups`, not a bare `try/except`.**
Step 8 wrapped the `_resolve_requires` call in `except Exception: pass` to tolerate a hand-written
`requires()`. That would also have swallowed the collision `ValueError` that decision 9 exists to
raise. `_requires_groups(task)` returns `{}` when the class has no spec — the precise condition —
and lets every other error through. `self.requires()` would raise on the same input anyway.

**3. Test 9 folded into the existing test rather than added separately.**
`test_requires_decorator_conflict` already asserted that a hand-written `requires()` plus a
decorator raises, for all three decorators. What it did not cover is the complement, which is the
thing the `_oryxflow_generated` marker could break: that stacking two decorators is *not* a clash.
That assertion was added there instead of duplicating the negative case in a new test. Eight tests
added rather than nine; baseline 167 -> **175 passing**, no existing test modified.

**4. `python_requires` shipped alongside a calver bump to `26.7.28`** in the same `setup.py` edit,
matching how previous feature commits in this repo carry their version bump (`74055a0`).
