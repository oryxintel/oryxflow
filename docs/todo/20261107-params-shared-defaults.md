# Shared parameter defaults: one Parameter object on many classes, conflicting upstream defaults

Status: implemented (see "Implementation notes" at the end).

## Context

26.11.6 made an exported task's defaults its prod values: `run()` stops with
`ValueError: <Task>.<param>: exported parameters need a default` when an exported parameter has
none. Downstream projects kept their prod values in a `flow_params.py` passed through
`Workflow(params=...)`, with the same parameter (`refresh`, `period`) declared on about ten
upstream classes in several modules. A default on the one exported class doesn't reach its
upstream tasks: each class declares its own `refresh`, and `@requires` only copies a parameter
the requiring class doesn't already have.

Two projects independently converged on the same fix: a module of module-level Parameter objects

```python
# params_prod.py
refresh = oryxflow.DateParameter(default=datetime.date(2026, 11, 5))
```

assigned on every declaring class (`refresh = params_prod.refresh`). It works, but nothing said it
was supported. They also hit:

1. Two classes declaring the same name with different defaults, both required by one task: the
   result was silent and order-dependent.
2. `BoolParameter()` has an implicit `False` default, so it passed the "needs a default" check
   although prod was `True` (only ever supplied through the workflow params). The published prod
   build would have been the `False` one.

### Design decisions

- **A shared Parameter object is safe, and is already what `@requires` does.** `Parameter` holds
  only `_default`, `significant`, `description`, `positional`, `_counter` (and `_default_explicit`,
  below) -- all set in `__init__` and never mutated afterwards. `_apply_spec` injects the
  upstream's Parameter object itself (`setattr(cls, pname, pobj)`), so every `@requires` chain
  already shares objects. `bind()` copies before changing a default (`copy.copy`). The task_id is
  built from parameter names and serialized values only, so it is unaffected. The one visible
  effect: `get_params()` sorts by `_counter` (creation order), so a shared object created early in
  a `params_prod` module sorts first -- this only matters for positional construction
  (`Task('2026-11-05')`), which nobody should rely on; `tasks_export.py` sorts by name.
- **No `shared_param()` helper.** A plain module-level Parameter is already the whole pattern; a
  helper would add a name to learn without adding behavior. Left to the maintainer.
- **Conflicting upstream defaults warn, not raise**, at class definition only (no run-time cost).
  What happens is defined (the first source in spec order wins and `clone()` hands its value to the
  other upstream), but invisible: the second upstream silently runs with a value that is not its
  own default. Raising would break existing projects whose deliberate choice happens to work. The
  warning is a `UserWarning` subclass, `ParameterDefaultConflictWarning`, so it can be filtered or
  turned into an error. Not warned: the same object, equal defaults (compared serialized), either
  side without a default (the requiring task must then be given a value), a parameter declared in
  the requiring class body (an explicit choice), bound parameters of imported tasks (never copied).
  Deduped per (name, first source, other source) on the class, because `_apply_spec` reruns after
  every stacked decorator.
- **An exported `BoolParameter` needs an explicit default** (BREAKING, small). The implicit `False`
  is exactly the silent-wrong-prod case. `Parameter.__init__` records `_default_explicit`;
  `export._check_defaults` rejects a parameter whose default was filled in. Non-exported tasks are
  untouched. Generated `tasks_export.py` classes always write the default, and `bind()` sets the
  flag on its copy.
- **The `ValueError` names the fix** for the `Workflow(params=...)` case: make the value the
  default, via one shared Parameter object when several classes declare it, and drop it from the
  workflow params.

## Implementation

1. `oryxflow/parameter.py` `Parameter.__init__`: `self._default_explicit = default is not _no_value`
   (before `BoolParameter` fills in `False`).
2. `oryxflow/export.py` `_check_defaults`: `_DEFAULT_HINT` appended to the missing-default message;
   a second `ValueError` ("need an explicit default") when `_default_explicit` is False. `bind()`
   sets `_default_explicit = True` on its copy.
3. `oryxflow/core.py` `_apply_spec` step (c) records every `(source, Parameter)` per name in spec
   order; new step (d) `_warn_default_conflicts(cls, sources, injected)` emits
   `ParameterDefaultConflictWarning` (defined next to it).
4. `tests/test_shared_params.py`: shared object across three classes in a `@requires` chain
   (defaults flow, `Workflow` params override, the object is unchanged, task ids equal per-class
   declarations); conflict warns once with both upstreams and defaults (plain and stacked
   decorators), first wins and is passed on; no warning for shared / equal / declared; implicit vs
   explicit exported `BoolParameter`; the message hint.

## Files modified

- `oryxflow/parameter.py` -- `_default_explicit`.
- `oryxflow/export.py` -- `_check_defaults` messages + explicit-default rule; `bind()` flag.
- `oryxflow/core.py` -- `ParameterDefaultConflictWarning`, `_warn_default_conflicts`, `_apply_spec` (c)/(d).
- `tests/test_shared_params.py` -- new.
- `CHANGELOG.md`, `CLAUDE.md` -- entries.

## Verification

```bash
python -m pytest tests/test_shared_params.py -q        # 9 passed
python -m pytest tests -q                              # nothing previously passing fails
```

## Implementation notes (divergences from the plan as built)

None; this record was written with the implementation. The user-doc recipe ("a parameter shared
by many tasks") lives in the separate docs repo.
