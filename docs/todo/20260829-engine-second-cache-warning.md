# Warn before building a second cache in a subdirectory

## Context

oryxflow resolves its output directory relative to the **current working directory** —
`settings.dirpath` defaults to `data/`, and `set_dir()` is only for a genuinely non-default
location. That is the right model and this plan does not change it.

The exposure it creates: run a flow from the repo root, then run it again from a subdirectory, and
the second run finds an empty `data/` beside it, reports every task incomplete, and recomputes
everything. Nothing is wrong — the cache did exactly what it was told — but the work is paid for
twice and there is no signal that it happened.

For an ordinary pipeline that costs CPU. It is not only an LLM problem: a task that calls a metered
API, a paid data vendor, or a long-running query pays real money for the second run, and those
pipelines are common. The failure is quiet in every case, because "task not complete" looks
identical whether the output was never built or was built somewhere else.

This came up while removing `oryxflow.set_dir(HERE / 'data')` from the eval scaffold (see
`docs/todo/20260829-evals-real-project-integration.md`). That template anchored its cache to the
eval directory specifically to avoid re-billing paid API calls. The anchor was the wrong fix —
it mutates a process global at import time, and it diverges from the library for a problem the
library should handle — but the underlying risk it was defending against is real and general.

### Design decisions

- **Warn, do not relocate.** Rejected: searching upward for an existing `data/` and using it, the
  way `git` finds `.git` or `pytest` finds its rootdir. That would silently change where existing
  projects read and write based on directory contents, which is a much worse failure than the one
  it fixes — a flow that quietly starts reading a *different* cache is harder to diagnose than one
  that quietly rebuilds. The resolution rule stays exactly as it is; only an advisory is added.
- **Advisory, on the preview/first-run path, never inside the hot loop.** This follows the
  precedent set by the unused-input lint (`docs/todo/20260803-engine-unused-input-lint.md`): the
  execution path carries no advisory work. Emit it once per process when the directory is first
  resolved, not per task and not per completeness check.
- **One ancestor walk, bounded.** Stop at the filesystem root or at a repository boundary
  (`.git`), whichever comes first. This is a handful of `stat` calls once per process.
- **Only warn when the local directory is absent or empty.** An existing populated `data/` here is
  a deliberate second project, not a mistake, and warning about it every run would be noise people
  learn to ignore.
- **Suppressible.** A settings flag, because a monorepo with genuinely independent projects at
  several depths is a legitimate layout that would otherwise warn forever.

## Implementation

1. **`oryxflow/settings.py`** — add `warn_nested_dir = True` beside the other advisory settings, so
   the check can be turned off per project without touching code.

2. **`oryxflow/__init__.py`, `set_dir()`** — after the directory is resolved (both the `dir is None`
   and the explicit-`dir` branches), call a new helper before returning. `set_dir` is the single
   place the directory becomes real, which makes it the right seam; it already runs `mkdir`, so the
   check costs nothing extra in walk depth.

3. **New helper, `oryxflow/utils.py`** — `warn_if_nested_data_dir(dirpath)`:
   - return immediately when `settings.warn_nested_dir` is false;
   - return when `dirpath` exists and is non-empty (a deliberate second project);
   - walk parents of `dirpath.resolve().parent`, stopping at the filesystem root or the first
     directory containing `.git`;
   - at each level, look for a directory with the same *name* as `dirpath.name` that exists and is
     non-empty;
   - on a hit, emit one WARNING through `oryxflow.log.logger` and return. Never raise.

   The message names both paths and the fix, in the style the rest of the library uses for
   advisories — what happened, why it matters, what to do:

   ```
   an oryxflow output directory already exists at <ancestor>, and this run will build a
   second one at <here>. Tasks completed there will be rebuilt from scratch, and any paid
   call they make will be paid for again. Run from <ancestor's parent>, or set
   oryxflow.settings.warn_nested_dir = False if two separate caches are intended.
   ```

   Emit it once per process — a module-level `_warned` set keyed on the resolved path, so a
   re-entrant flow (a task's `run()` calling `oryxflow.run()`) does not repeat it.

4. **`docs/docs/run.md`** — a short subsection under the existing material on where outputs live:
   what the warning means and the two ways to answer it. User-facing voice: what the reader gets
   and what to type, not how the walk works.

5. **`CHANGELOG.md`** under `## [Unreleased]` / `### Added` — name the setting
   (`settings.warn_nested_dir`) in backticks, since that is the symbol someone will grep for.

6. **Plugin repo, `skills/oryxflow/SKILL.md`** — a behavioural rule for the agent, which is the
   other half of this and catches the case the library cannot see (two *different* directories both
   with populated caches): if a task the session already ran successfully now reports incomplete,
   stop and check the working directory before re-running. Re-running is the expensive action, so
   the check belongs before it, not after.

## Files modified

| File | Change |
| --- | --- |
| `oryxflow/settings.py` | `warn_nested_dir = True` |
| `oryxflow/__init__.py` | `set_dir()` calls the check once the path is resolved |
| `oryxflow/utils.py` | `warn_if_nested_data_dir()` — bounded ancestor walk, one warning per process |
| `docs/docs/run.md` | What the warning means and how to answer it |
| `CHANGELOG.md` | `### Added` entry naming `settings.warn_nested_dir` |
| `tests/test_nested_dir_warning.py` | NEW — see below |
| *(plugin repo)* `skills/oryxflow/SKILL.md` | Agent rule: an already-run task reporting incomplete means check the directory first |

## Verification

New test file, using a loguru sink appended to a list — pytest's `caplog` will not see these
records, because loguru does not go through stdlib `logging`:

1. **Warns on a nested empty directory.** Populate `<tmp>/data/`, then `set_dir()` from
   `<tmp>/sub/`. Exactly one WARNING, naming both paths.
2. **Silent when there is no ancestor.** `set_dir()` in an isolated tmp directory emits nothing.
3. **Silent when the local directory is already populated.** Both `<tmp>/data/` and
   `<tmp>/sub/data/` non-empty — a deliberate second project, no warning.
4. **Silent when suppressed.** `settings.warn_nested_dir = False` emits nothing.
5. **Once per process.** Two `set_dir()` calls resolving to the same path produce one record.
6. **Stops at the repository boundary.** A populated `data/` above a `.git` directory is not
   reported from below it.

Then the existing baseline must hold unchanged — this adds an advisory and changes no resolution
rule:

```bash
python -m pytest tests/ -q          # 114 core + 207 evals/code_version, plus the 6 new
```

## Implementation notes (divergences from the plan as built)

1. **The `.git` boundary is checked at the starting directory too**, not only at each ancestor. The
   plan only bounded the walk. Without the extra check, a flow run at a repository root
   (`<repo>/data` beside `<repo>/.git`) looks one level above the repo and warns about a *sibling*
   project's `data/` — the boundary never gets a chance to fire. Inside the walk the order is
   candidate-then-boundary at each level, which is what keeps the "stops at the repository
   boundary" test passing.
2. **One call site, not two.** The plan asked for the check in both branches of `set_dir()`. Both
   branches converge on the same resolved `dirpath`, so a single call after the if/else covers them
   identically.
