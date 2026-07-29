# Fan-out: `derive=` — per-branch parameters computed from the fanned values

## Context

`@requires_each(Dep, region=REGIONS)` gives one branch per region and copies `Dep`'s parameters
onto the combining task minus `region`. That covers the case where the branches differ *only* in
the fanned value.

Real fan-outs usually have a second thing that differs per branch and is a **deterministic
function of the fanned value** — a per-region source URL, a per-model hyper-parameter set, a
per-file schema version, a row threshold that varies by market. There is currently nowhere good
to put it:

- **Look it up inside the branch's `run()`** (`url = URLS[self.region]`). The lookup is then
  invisible to the engine: the branch's `task_id` is built from its parameters, and `url` is not
  one, so editing `URLS['north']` does **not** invalidate the north branch. You get the old
  output back on the next run, silently. (`code_version` bumping invalidates *every* branch, not
  the one that changed — the opposite problem.)
- **Declare it a parameter and hand-write `requires()`**:

  ```python
  def requires(self):
      return {r: RegionLoad(region=r, source_url=URLS[r]) for r in cfg.REGIONS}
  ```

  This is the exact form `requires_each` / `requires_grid` exist to remove — it carries only the
  parameters someone remembered to list, so everything the flow passed down (`dt_start`, `env`
  settings, an upstream `model`) is silently dropped from the branches.
- **Fan out over it as a second grid parameter** (`region=REGIONS, source_url=URLS.values()`).
  Wrong shape: `requires_grid` takes the cartesian product, so that is `len(REGIONS)²` branches,
  all but the diagonal nonsense.

So `derive=` closes the gap: a per-branch parameter whose value is computed from the fanned
values, landing in the branch's parameters — and therefore in the branch's `task_id`, so a change
to the mapping invalidates exactly the branches it affects and nothing else.

```python
# cfg.py
REGIONS = ['north', 'south', 'east']
SOURCE = {'north': 'https://.../n.csv', 'south': 'https://.../s.csv', 'east': 'https://.../e.csv'}

class RegionLoad(oryxflow.tasks.TaskPqPandas):
    region = oryxflow.Parameter()
    source_url = oryxflow.Parameter(default='')

    def run(self):
        self.save(fetch(self.source_url))

@oryxflow.requires_each(RegionLoad, region=cfg.REGIONS,
                        derive={'source_url': lambda v: cfg.SOURCE[v['region']]})
class RegionCombine(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(self.inputLoadConcat())
```

Edit `cfg.SOURCE['south']` and only the south branch (and its downstream) is incomplete.

### Design decisions

- **`derive=` is a keyword-only argument holding `{parameter name: callable}`** — not another
  entry in `**grid`. Grid keys are fanned over (cartesian product); derived keys are not, and
  conflating them in one mapping would make which-is-which depend on the value's type.
  *Consequence, accepted:* a parameter literally named `derive` (or `cls`) cannot be fanned out
  over — it collides with `requires_grid`'s own arguments. `requires_each` raises naming it, so
  it is loud rather than silent.
- **The callable takes the fanned-values dict, not the values positionally and not the task.**
  `lambda v: SOURCE[v['region']]`. Three reasons: it does not couple the callable's arity to how
  many parameters are fanned out (so adding a second fanned parameter does not break every
  derive callable); there is one calling convention to learn rather than one per arity; and the
  multi-parameter case is real (`lambda v: TUNING[v['model']][v['horizon']]`).
- **No `self`.** A grid *value* may already be a callable taking the task
  (`region=lambda self: REGIONS[self.sector]`) — that is where a task parameter belongs. Keeping
  derive callables a pure function of the fanned values is what makes them deterministic, and
  determinism is what makes the derived value legitimate in the branch's `task_id`. A derive
  callable also cannot read another derived value; compose in plain Python if you need that.
- **Where the derived name appears** — this is the whole semantic, so it is spelled out:

  | | derived name |
  | --- | --- |
  | the `requires()` dictionary **key** | **excluded** — the key stays the fanned value (`'north'`), because the derived value is a function of it and adding it would only make `inputLoad(task='north')` unwriteable |
  | the **branch** task's `task_id` | **included** — it is a real `Parameter` on the branch class. This is the point of the feature |
  | the **combining** task's parameters | **excluded** — same reason fanned names are: the combiner is where branches converge and must not carry anything they differ on |

- **Everything that can fail at class-definition time does.** A derived name that is not a
  `Parameter` on the dependency class is the dangerous one: `clone()` builds its kwargs from
  `cls.get_params()`, so an unknown name is **silently dropped** and the branch runs with the
  default. That raises `TypeError` at decoration.
- **Rejected:** constants in `derive=` (`derive={'x': 5}`) — a value that is the same for every
  branch is just a parameter, and allowing it means two calling conventions; a derive callable
  receiving `self`; putting the derived value in the dependency key.

## Implementation

1. **`oryxflow/core.py`** — new module-level helper next to `_entry_sources` (~line 493):

   ```python
   def _check_derive(cls, derive, fanned, where):
       """Validate a ``derive=`` spec against the dependency class; return it as a dict.

       Every failure here is silent if unchecked -- most of all a name that is not a Parameter
       of ``cls``, because ``clone()`` builds its kwargs from ``cls.get_params()`` and drops
       anything else, so the branch would quietly run with the default.
       """
       if not derive:
           return {}
       if not isinstance(derive, dict):
           raise TypeError(
               '{}: derive= must be a dict of {{parameter name: function of the fanned '
               'values}}, got {}'.format(where, repr(derive)))
       params = dict(cls.get_params())
       for name, fn in sorted(derive.items()):
           if not callable(fn):
               raise TypeError(
                   '{}: derive[{!r}] must be a function of the fanned values -- '
                   'lambda v: LOOKUP[v[<fanned parameter>]] -- got {}'.format(
                       where, name, repr(fn)))
           if name in fanned:
               raise ValueError(
                   "{}: '{}' is both fanned out over and derived -- it can only be one. "
                   'Drop it from one of them.'.format(where, name))
           if name not in params:
               raise TypeError(
                   "{}: derive[{!r}] has no matching parameter on {} -- the derived value "
                   'would be silently dropped and the branch would run with the default. '
                   'Declare {} = oryxflow.Parameter() on {}.'.format(
                       where, name, cls.__name__, name, cls.__name__))
       return derive
   ```

2. **`oryxflow/core.py`: `Task.requires_grid`** (line 308) — signature becomes
   `def requires_grid(self, cls=None, derive=None, **grid):`. After `resolved` is built and
   before the product loop:

   ```python
   derive = _check_derive(cls, derive, set(names), self.task_family)
   ```

   and inside the loop, after `seen[key] = values`:

   ```python
   # a copy per callable: a callable that mutates its argument must not corrupt the key
   # bookkeeping or another branch's values
   extra = {n: fn(dict(values)) for n, fn in derive.items()}
   out[key] = self.clone(cls, **values, **extra)
   ```

   Extend the docstring with the `derive=` paragraph and the three-row rule (key excludes it,
   branch `task_id` includes it).

3. **`oryxflow/core.py`: `_apply_spec`** (line 501) — the exclusion set is now fanned **plus**
   derived. Replace the `fanned` accumulation with:

   ```python
   fanned, derived = set(), set()
   for entry in spec:
       if entry['kind'] == 'each':
           fanned.update(entry['grid'])
           derived.update(entry.get('derive') or ())
   excluded = fanned | derived
   ```

   Step (a) iterates `sorted(excluded)` and picks the message by which set the name is in (the
   existing wording for fanned; a parallel "derives '<name>' per branch" wording for derived).
   Steps (b) and (c) use `excluded` in place of `fanned`. The global-across-the-whole-spec
   property is unchanged and applies to derived names for the same reason: `@requires(X)` copying
   back an `X.source_url` that `@requires_each` deliberately excluded would be silent.

4. **`oryxflow/core.py`: `_resolve_requires`** (line 594) — pass the entry's derive through:

   ```python
   grid = task.requires_grid(entry['cls'], derive=entry.get('derive'), **entry['grid'])
   ```

5. **`oryxflow/core.py`: `requires_each.__init__`** (line 710) — signature becomes
   `def __init__(self, task_to_require, /, *extra, derive=None, **grid):`. After the existing
   grid-value validation loop, add the reserved-name guard and the derive validation (which needs
   the unwrapped class, so it goes after the `{name: Cls}` unpacking):

   ```python
   for name in grid:
       if name in ('cls', 'derive'):
           raise TypeError(
               "requires_each cannot fan out over a parameter named '{}' -- it collides with "
               "requires_grid()'s own argument. Rename the parameter.".format(name))
   self.derive = _check_derive(task_to_require, derive, set(grid),
                               '@requires_each({})'.format(task_to_require.__name__))
   ```

   `__call__` adds `'derive': self.derive` to the spec entry.

6. **`oryxflow/__init__.py`: `requires_each`** (line 437) — mirror the signature
   (`*extra, derive=None, **grid`), pass `derive=derive` through to `core.requires_each`, and
   document it in the user-facing docstring (`Args:` entry + a short example), staying on
   benefits: what changing the lookup does to the cache.

7. **`docs/docs/advtasksdyn.md`** — a `### A parameter that varies per branch` subsection under
   the fan-out material: the problem (the lookup inside `run()` is invisible to the cache), the
   `derive=` form, and the one rule (the derived value is in the branch's identity, not in the
   dependency key and not on the combining task).

8. **`CLAUDE.md`** — extend the fan-out paragraph with `derive=` and its exclusion semantics
   (derived names join fanned names in the global exclusion).

9. **`CHANGELOG.md`** — an `### Added` line under `## [Unreleased]`.

10. **`tests/test_main.py`** — in the fan-out test class, after
    `test_requires_grid_key_collision_raises`:

    - `test_requires_each_derive` — the derived value reaches the branch; the dependency key is
      still the bare fanned value; the derived value is in the branch's `task_id` and changing
      the lookup changes it; the combining task has no such parameter.
    - `test_requires_each_derive_multi_param` — two fanned parameters, callable reads
      `v['model']` / `v['horizon']`.
    - `test_requires_each_derive_declared_on_combiner_raises` — `TypeError`.
    - `test_requires_each_derive_validation` — non-dict, non-callable, name also fanned, name
      not a parameter of the dependency; plus the reserved `derive`/`cls` grid name.
    - `test_requires_grid_derive` — the hand-written `requires()` form.
    - one end-to-end run asserting the branch actually used the derived value.

## Files modified

- `oryxflow/core.py` — `_check_derive` helper; `requires_grid(derive=)`; `_apply_spec` excludes
  derived names as well as fanned; `_resolve_requires` passes derive through;
  `requires_each(derive=)` + reserved grid-name guard.
- `oryxflow/__init__.py` — `requires_each(derive=)` wrapper signature and user-facing docs.
- `docs/docs/advtasksdyn.md` — the per-branch-parameter section.
- `CLAUDE.md` — fan-out paragraph covers `derive=`.
- `CHANGELOG.md` — `Unreleased / Added` entry.
- `tests/test_main.py` — six tests.

## Verification

```bash
python -m pytest tests/test_main.py tests/test_workflow.py \
    tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
```

Baseline before this change: **177 passing**. After: **183 passing** (six added, none changed —
`derive=` defaults to `None`, so every existing fan-out resolves through the same code path).

Then the docs build, which compiles the tested pages:

```bash
python scripts/build_docs.py --check
```

End-to-end sanity check (run from the repo root, deletes its own directory):

```python
import oryxflow, pandas as pd
oryxflow.set_dir('data-derive/')
URLS = {'north': 'n.csv', 'south': 's.csv'}

class L(oryxflow.tasks.TaskPqPandas):
    region = oryxflow.Parameter(default='north')
    source = oryxflow.Parameter(default='')
    def run(self): self.save(pd.DataFrame({'region': [self.region], 'source': [self.source]}))

@oryxflow.requires_each(L, region=list(URLS), derive={'source': lambda v: URLS[v['region']]})
class C(oryxflow.tasks.TaskPqPandas):
    def run(self): self.save(self.inputLoadConcat())

oryxflow.Workflow(C).run()
print(oryxflow.Workflow(C).outputLoad())
# expects two rows: north/n.csv and south/s.csv
print(sorted(C().requires()))                      # ['north', 'south'] -- keys unchanged
print('source' in dict(C.get_params()))            # False -- not on the combining task
```

## Implementation notes (divergences from the plan as built)

Built as planned (183 passing at that point), then two changes on review:

1. **`derive` and `cls` are reserved parameter names, rejected at class definition** — added to
   `Register.RESERVED_PARAM_NAMES` alongside `path`/`flows`, so declaring
   `derive = oryxflow.Parameter(...)` raises `ValueError` where it is written.

   The plan instead had `requires_each` raise on a *fanned parameter named* `derive`/`cls`
   (step 5's reserved-name guard), and accepted "a parameter literally named `derive` cannot be
   fanned out over" as a design cost. That was the wrong place for the check, for two reasons.
   Half of it was **dead code**: `requires_each(task, /, *extra, derive=None, **grid)` binds a
   `derive=` keyword to the named argument, so `'derive'` can never appear in `grid` and that
   branch was unreachable. And the trap is not specific to fanning out — `cls` is also a
   `clone()` argument, so `self.clone(cls=Other)` on a task with a `cls` parameter is broken
   whether or not a fan-out is involved. That is the same shape as `path`/`flows` (a name the
   library already uses around a task, silently shadowed), so it belongs in the same one place.
   The guard in `requires_each` was deleted rather than kept alongside.

2. **Fanned names are validated against the dependency class too, not just derived ones**
   (`_check_derive` became `_check_grid`, doing both halves). The plan validated only `derive=`
   names, on the grounds that `clone()` silently drops a name the target has no parameter for.
   That reasoning applies unchanged to the *fanned* names, and the consequence there is worse:
   `@requires_each(RegionLoad, sector=[...])` where `RegionLoad` has no `sector` produced one
   dependency key per value all resolving to the **same** memoized task, so
   `inputLoadConcat()` returned N copies of one branch's output tagged as if they were N
   branches — a wrong answer with no error. Validating derived names while leaving fanned names
   unchecked would have been an odd asymmetry in a helper that already had the parameter dict in
   hand. It is checked at class definition for the decorator (the dependency class is fully
   defined by the time it is decorated with) and at `requires()` time for `requires_grid`.

Both are breaking changes and carry `BREAKING:`/`Migration:` bullets in `CHANGELOG.md`. Final
baseline: **184 passing** (the two changes added one test and folded the reserved-name assertion
into it).
