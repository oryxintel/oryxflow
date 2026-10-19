# Engine fixes from real-project usage (Windows/OneDrive, multi-interpreter)

Status: **planned, not implemented** (2026-10-19). Branch `fix/usage-feedback` from `main` (cb15c0c).
Execution: TDD, one commit per item, then one whole-branch review. Each item is independent.

## Context

A consumer running several oryxflow projects on a OneDrive-synced Windows machine, with one
editable install shared by a Python 3.11 and a Python 3.12 environment, reported four issues:

1. Builds crash with `PermissionError: [WinError 5]` when `state._write` replaces
   `.oryxflow-code-status.json` while sync/antivirus holds the file. A rerun always succeeds.
2. Records stamped under another Python minor version are treated as complete, and code edits
   made before the next build are silently blessed (agents had to `reset_downstream` by hand).
   A record from an older oryxflow (`v` 3) is grandfathered the same way.
3. The unused-input lint reports UNUSED INPUTS for a task that loads its keyed inputs in a
   dict comprehension: `{n: self.inputLoad(task=n) for n in ['a', 'b']}`.
4. Projects that read shared published files (a data master parquet, a lookup map) need a manual
   reset when those files change.

Maintainer decision on 4: **no engine-level tracking of external files**; a convenience
function is fine. `oryxflow.hash_files()` already is that function (see item 4).

## Item 1 -- status-file write retries on Windows

Root cause: the crash is in the build's advisory sweep over already-complete tasks
(`codecheck.Advisor.advise`, `oryxflow/codecheck.py:136`, writes at :179/:186/:240). Those
writes are bookkeeping that the next build redoes, yet an exception aborts the whole build.

Changes:
- `oryxflow/state.py:64` `_write`: on the local-fs branch, retry `os.replace` on
  `PermissionError` with backoff (about 8 attempts, ~4 s total: 0.05, 0.1, 0.2, 0.4, 0.8, 1, 1,
  1 s), then re-raise. Clean up the tmp file only after the final failure. Put the delays in a
  module constant so tests can zero them.
- `codecheck.Advisor.advise`: wrap the three `state.put_record` calls; on failure log a
  loguru warning (`"could not write code-status record for <tid>: <err>; will retry next build"`)
  and continue. Never raise from the advisory sweep.
- `oryxflow/core.py:1374`, the post-run stamp: it already swallows the error, but only at
  `logger.debug`. Raise it to `logger.warning` and say what it means: the old record stays, so this
  task reruns next build, and downstream tasks may not see this rerun until then. Do not raise:
  the output is already saved.

Tests (`tests/test_code_invalidation.py` or a new `tests/test_state.py`):
- monkeypatch `os.replace` to raise `PermissionError` twice then succeed -> record written,
  no exception, zero delays.
- always raising -> `_write` raises `PermissionError`; no `*.tmp` left in the dir.
- a build whose advisory write always fails -> `flow.run()` succeeds (warning logged).

## Item 2 -- comparable hashes across Python versions, loud re-baseline

Root cause: `codehash` hashes `ast.dump(...)` (`oryxflow/codehash.py:212, 497, 510, 587`).
`ast.dump` text differs across minor versions (3.12 added empty `type_params` fields), so records
carry `py` (`codehash.PY_TAG`) and a mismatch means "can't compare": `_code_ok`
(`oryxflow/tasks/__init__.py:155`) returns complete, then `advise` (`codecheck.py:182`)
re-stamps with the CURRENT hashes, blessing any edit made since the last run.

Evidence that a fix works: a dump that skips fields whose value is `None` or `[]` gave **0** hash
differences between 3.11 and 3.12 on 973 real `.py` files; plain `ast.dump` differed on 361.

```python
def _dump(node):
    # interpreter-stable ast.dump: omit None/[] fields (3.12 added empty type_params, etc.)
    if isinstance(node, ast.AST):
        parts = ['{}={}'.format(f, _dump(v)) for f in node._fields
                 for v in [getattr(node, f, None)] if v is not None and v != []]
        return '{}({})'.format(type(node).__name__, ', '.join(parts))
    if isinstance(node, list):
        return '[{}]'.format(', '.join(_dump(x) for x in node))
    return repr(node)
```

Changes:
- `codehash`: add `_dump` and use it at the four `ast.dump` sites. Update the `PY_TAG` comment:
  the tag becomes informational and is still stamped in records.
  (`oryxflow/evals/task.py:119` also uses `ast.dump` for an eval cache key. Leave it: evals are
  being split out into oryxeval, see `20261018-sys-split-oryxeval.md`.)
- `state.RECORD_V` 4 -> 5 with a history line ("v5: interpreter-stable AST dump").
- `_code_ok` and `advise`: only a `v` mismatch is "unverifiable"; a `py` mismatch is compared
  normally. Equal hashes -> converge silently (re-stamp `py`). Different hashes -> the normal
  code-change path: rerun, or warn under a pin or the expensive-recompute guard.
  Grep for every other `rec.get('py')` check: `codecheck.py:182`, `tasks/__init__.py:155`, and the
  `accept_code` `_restamp` writes `py` (fine).
- Loud re-baseline: `Advisor` collects the task families it re-stamps because of a `v`
  mismatch. Add `Advisor.finish()`, called by `core.build` before `RunResult` is built
  (`core.py:1467`). If any were collected, it emits ONE warning on the same channels as
  `Advisor.warn` (warnings.warn `StalenessWarning`, loguru, a `code_warning` event, and an entry
  in `RunResult.warnings`):
  `"N task(s) had code-status records from an older oryxflow (record format vX); re-stamped
  without comparing code, so edits made before this run were not detected: <families, first 5>.
  reset() any you changed since their last run."`

Pending decision: the plan above keeps the one-time silent-but-WARNED re-baseline on a `v` bump.
The alternative is treating `v` mismatch as incomplete, which is safer but forces a one-time full
recompute of every project. Default to the warning unless the maintainer says otherwise.

Tests (`tests/test_code_invalidation.py`; the existing case at :631 sets `rec['py'] = '0.0'` and
asserts a silent re-stamp -- it must change):
- `py` mismatch + unchanged code -> complete, record re-stamped with the current `PY_TAG`,
  `output_id` preserved, no warning.
- `py` mismatch + edited task code -> incomplete (reruns) in auto mode.
- `v` mismatch -> complete, re-stamped, exactly one aggregated warning in
  `RunResult.warnings` naming the family.
- `_dump` unit test: a `FunctionDef` with an explicit empty `type_params` attribute dumps
  identically to one without it (simulates the 3.11/3.12 difference on one interpreter).

## Item 3 -- unused-input lint false positive on dynamic keys

Root cause: `oryxflow/inputcheck.py:224`, the keyed branch, does a set difference against string
literals passed as `task=` or used as subscripts (`_string_keys_used`, :121). A non-literal
`task=n`, or a whole-input load the lint can't follow (`self.inputLoad().items()`, a load inside
a comprehension), names no dependency literally, so every dependency looks unused. The module's
contract is "no false positives; where usage cannot be proven, `unanalyzed`".

Change: in the keyed branch, if `run()` has any `self.inputLoad`/`inputLoadConcat`/`input` call
whose `task=` is present but not a `str` constant, or that has no `task=` (a whole-input load),
then every dependency not named literally gets `unanalyzed` with reason
`'dynamic task= key'` / `'whole-input load not tracked'` instead of `unused`. Literal matches
stay `clean`.

Tests (`tests/test_inputcheck.py`, module-level fixture tasks; neutral names only):
- `@requires({'a': SrcA, 'b': SrcB})` + `{n: self.inputLoad(task=n) for n in ['a', 'b']}`
  -> no `unused` findings.
- `@requires(SrcA, SrcB)` + `for t in ['SrcA', 'SrcB']: self.inputLoad(task=t)` -> no `unused`.
- the existing `KeyedNamesOne` still reports `SrcB` unused (recall kept for literal keys).

## Item 4 -- external files: document and harden `hash_files`

`oryxflow.hash_files(*patterns, root=None)` (`oryxflow/utils.py:214`) already exists and is
documented in `docs/docs/managing-workflows.md` ("When your task depends on something that isn't
Python"). No new function. Two gaps for the shared-data-file use case:

- **Large files are slow.** It reads every matched file whole (`read_bytes`) on every
  completeness check. Change: hash in 1 MB chunks, and keep a process-level cache
  `{abspath: (size, mtime_ns, md5)}` so an unchanged file is re-read only after a size or mtime
  change. The digest must not change for the same content: keep key + `\0` + content + `\0`,
  fed in chunks.
- **A `code_version()` method pins the task**, so edits to the task's own Python then only
  WARN instead of rerunning (`_own_code_ok`, `tasks/__init__.py:163`). The docs currently say
  the change reruns "exactly as a logic edit would", which reads as if logic edits still rerun.
  Document the recipe that keeps both:

  ```python
  def code_version(self):
      return {'data': oryxflow.hash_files('//share/published/master.parquet'),
              'code': oryxflow.codehash.task_code_hash(self)}
  ```

  This is the pattern `TaskNotebook.code_version` uses on `feat/notebook-reports`. Add a short
  "Shared data files" paragraph with it to managing-workflows.md. OneDrive/network re-syncs
  change mtimes without changing bytes; the content hash is why this does not rerun.

Tests (`tests/test_utils*.py`, wherever `hash_files` is tested now):
- same digest before and after the change for a fixed set of files (pin the literal hex).
- the cache serves an unchanged file without reading it (monkeypatch `open`/`read` to count).
- rewriting a file with identical bytes and a new mtime -> same digest.
- a task with the recipe above: editing the data file reruns it; editing `run()` reruns it.

Not doing: changing the consumer's editable install (it points at a OneDrive-synced checkout).
That is their machine. Note in the handoff that whichever branch is checked out in
that working tree is what their projects run.

## Draft CHANGELOG entries (paste under `## [Unreleased]` as each item lands)

```markdown
### Fixed
- `state._write` retries `os.replace` on `PermissionError` (Windows sync/antivirus holding
  `.oryxflow-code-status.json`) before raising, and the build's advisory sweep
  (`codecheck.Advisor.advise`) no longer aborts a build when a bookkeeping write fails -- it
  warns and the next build writes it. A failed post-run record write is now a warning, not a
  debug line.
- Code-status records from another Python minor version are now compared instead of trusted:
  `codehash` hashes an interpreter-stable AST dump, so an edit made under 3.11 to a task last run
  under 3.12 reruns it. Records from an older record format are still re-stamped without a
  comparison, but the build now warns once, listing the tasks (`RunResult.warnings`).
- Unused-input lint (`inputcheck.check_class`, `oryxflow.check_inputs`, `preview()`): a keyed load
  with a non-literal `task=` (`{n: self.inputLoad(task=n) for n in names}`) or a whole-input
  load is reported `unanalyzed`, not `unused`.

### Changed
- `state.RECORD_V` is 5. Migration: none -- every existing record is re-stamped once on its next
  build, with the warning above; `reset()` a task you edited since its last run.
- `hash_files` reads in chunks and caches each file's digest by size and mtime, so a large data
  file is not re-read on every completeness check. Digests are unchanged.
```

## Working notes for the implementer

- Worktree: `git worktree list` shows it (outside OneDrive, by rule). Never create a venv
  under OneDrive; use one outside it and run with `PYTHONPATH=<worktree>`.
- Full suite: `python -m pytest tests -q` (baseline on `main`: see `git log`, ~446 passed).
  Docs are doc-tested (phmdoctest); after editing `docs/docs/*.md`, regenerate per CLAUDE.md
  and run the docs tests.
- The pre-commit hook needs `pre-commit` on PATH.
- Public repo: no client or project names and no absolute local paths in code, tests, docs or
  commits.
- Cross-version check for item 2: compute `codehash.task_hashes` for a sample task under both a
  3.11 and a 3.12 interpreter (read-only, `PYTHONPATH` to the worktree) and assert they are equal.
