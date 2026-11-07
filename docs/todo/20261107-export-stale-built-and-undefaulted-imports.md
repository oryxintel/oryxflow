# Export: stale `built` entries and imported parameters without a default

Status: implemented (see "Implementation notes" at the end).

## Context

`tasks_export.py` is the contract between a producer project and its consumers. Each exported
class lists `built`: the parameter value sets whose output exists, and the docs tell consumers
to pin a build with `exported.X.bind(**exported.X.built[i])`. Two downstream consumer projects
hit silent or far-away failures:

**A. `built` lists entries the class can no longer select.** A producer dropped a parameter
`source_run` from `ReturnsMarket`. Its old outputs are still on disk, and `generate()` re-checks the
previous file's entries with the *previous file's* class (which still has `source_run`), finds the
files and keeps the entries. The regenerated file then lists

```python
class ReturnsMarket(oryxflow.tasks.TaskPqPandas):
    refresh = oryxflow.parameter.DateParameter(default=datetime.date(2026, 10, 18))
    built = [
        {'source_run': 'm1', 'refresh': datetime.date(2026, 7, 18)},
        {'refresh': datetime.date(2026, 10, 18), 'prod': True},
    ]
```

and a consumer following the docs gets
`TypeError: ReturnsMarket.bind(): no parameter source_run -- it has: refresh`. The same happens for
a renamed parameter (the old name lingers in old entries) and, worse, an added one: an entry
without the new parameter binds without error but names a task_id that has no file.

**B. `FlowImport` binds a parameter that has no default.** Files written before 26.11.6 (when
"exported parameters need a default" arrived) can hold `period = oryxflow.parameter.Parameter()`.
`FlowImport` marked every parameter bound (`_oryxflow_bound`), so `@requires(exported.X.bind(sector='retail'))`
stopped copying `period` onto the requiring task: the consumer class silently lost the
parameter and the failure (`MissingParameterException`) came at instantiation, far from the
cause.

### Design decisions

- **A: drop, don't mark.** An entry whose significant parameter names differ from the current
  class's is left out of `built`, and `generate()` warns once with what changed:
  `2 built entries dropped for ReturnsMarket: parameters no longer on the class: source_run` (and
  `parameters they lack: sector` for an added one). Rejected: keeping them with `'stale': True`
  and having `bind()` ignore the key. That changes the generated text (the contract every
  producer commits), and `bind(**stale_entry)` would still be wrong: with the extra key
  ignored it silently selects a *different* build (the current class's defaults for the
  missing parameter), which is the silent-wrong-result class we fix first. Dropping keeps
  "every listed entry binds and points at a file" as an invariant and needs no `bind()` change.
  The outputs stay on disk; the warning is the producer's cue to tell consumers pinned to an
  old build.
- **Compared by parameter names, not task_id or file existence under the new class.** Names
  are what `bind()` checks; a same-named parameter whose type changed is out of scope.
- **The warning reaches `RunResult.warnings`** through `FlowExport.notices`, returned by
  `auto_export`. It is told once naturally: the rewritten file no longer holds the entries.
  The event is `export_warning` (not `export_failed`, which stays for a failed update, told
  apart by `export.FAILED_PREFIX`).
- **B: leave such a parameter unbound, and warn at import.** Binding fixes a parameter to the
  producer's prod value; a parameter without a default has no prod value, so binding it fixes
  it to nothing. Leaving it unbound restores the pre-26.11.6 behavior for exactly those
  parameters (it copies through from the requiring task), so old consumers keep working while
  the warning (`X: parameter period has no default, so it is not fixed on import and copies
  through from the task that requires it -- re-run the producer on oryxflow >= 26.11.6 to
  write defaults`) points at the fix. Rejected: raising at import (breaks consumers of a
  producer they don't control) and binding with a warning (keeps the far-away failure).
- **C (partial bind against `built`) is deferred**: not needed for either fix.
- No change to the generated `tasks_export.py` text format.

## Implementation

1. `oryxflow/export.py` `FlowExport.generate()`, loop over loaded exported classes: compute
   `current` = the class's significant parameter names; for each task from `_still_built(old, ...)`
   skip it when `set(_str_params(t)) != current`, collecting its names; afterwards
   `self._notice(_stale_message(name, stale, current))`.
2. `_stale_message(name, stale, current)` (module level): names gone (`union(stale) - current`)
   and lacking (`current - intersection(stale)`).
3. `FlowExport.__init__`: `self.notices = []`; `_notice(msg)` appends and `warnings.warn`s.
4. `auto_export` returns `export.notices` on success; the failure message starts with the new
   constant `FAILED_PREFIX = 'oryxflow: could not update'`.
5. `oryxflow/__init__.py` `run()`: event `export_failed` for messages starting with
   `FAILED_PREFIX`, else `export_warning`.
6. `FlowImport.__init__`: `undefaulted` = params without `has_task_value()`; warn naming class
   and params; `_oryxflow_bound = frozenset(params) - set(undefaulted)`.

The carried-over branch (classes whose module was not imported) needs nothing: their entries
are checked with the very class they were written with.

## Files modified

- `oryxflow/export.py` - stale-entry drop + notice, `FAILED_PREFIX`, FlowImport unbound
  undefaulted params.
- `oryxflow/__init__.py` - `export_warning` vs `export_failed` event.
- `tests/test_export_stale_built.py` - removed / renamed / added parameter, unchanged class,
  old-style file import.
- `CHANGELOG.md`, `CLAUDE.md` ("Share between projects").

## Verification

```bash
python -m pytest tests/test_export_stale_built.py tests/test_export_bind.py \
    tests/test_export_defaults.py tests/test_external_data.py -q
python -m pytest tests -q
```

The new tests fail on the previous code (5 of 7) and pass after; the full suite holds its
baseline.
