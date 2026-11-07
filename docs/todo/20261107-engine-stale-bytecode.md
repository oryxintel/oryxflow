# Stale bytecode: a same-second, same-size edit ran the old code and was recorded as built by the new code

## Context

CPython validates a cached `__pycache__/*.pyc` against its source by two numbers stored in the
pyc header: the source's mtime in **whole seconds** and the source's size. A coding agent that
rewrites a task module in the same second as the module's last compile, at the same size
(`FACTOR = 1` -> `FACTOR = 2`), leaves a pyc that still passes that check. The next process
imports the OLD bytecode.

oryxflow does not look at bytecode; it hashes the source text (`codehash.task_hashes`). So it
sees the edit, reruns the task, but the task runs the old code. `core.build()` then stamped the
code-status record with the NEW source hashes over that output. Every later run compares the
record to the source, finds them equal, and keeps the stale output forever: a silent wrong
result, the worst class of bug for this library.

The bug was found in a downstream fork's test suite, where it showed as intermittent failures of
tests that rewrite a module and re-import it within one second, once Python started fast enough
to do both in the same second.

### Design decisions

- **Detect, don't prevent.** oryxflow cannot control how the user's interpreter imports. It can
  tell, after the fact, that the bytecode a process may have used predates the source's last
  change: the pyc header's whole-second mtime and size match the source (so it passed the check)
  but the pyc file itself was written before the source's last write (`pyc st_mtime_ns <
  source st_mtime_ns`). A fresh pyc is always written after the source it was compiled from.
- **Hash-based pycs (PEP 552) are skipped.** A flags field other than 0 means the pyc is
  validated by content (checked) or not at all (unchecked, the user's explicit choice); neither
  is the timestamp race.
- **Check only at record time, only for tasks that ran, only files already hashed.** The check
  runs in `build()` just before the record is written, over the task's module plus the files its
  hash keys name: a stat of each and a 16-byte read of each pyc. Cached (skipped) tasks pay
  nothing.
- **Never-matching hash instead of skipping the record.** A stale run's record gets an extra key
  `'<stale bytecode>': 'rerun'`, so the record never equals the current hashes and the next build
  redoes the task, and so everything downstream through the dependency fold. Skipping the record
  would leave the previous record in place, which could match the restored source in edge cases;
  the explicit marker also shows up in the rerun reason (`code change (auto: <stale bytecode>)`).
- **Remove the stale pyc and warn once per file per process.** Removing it guarantees the next
  import compiles the source. The warning is a `UserWarning` and a loguru warning.
- **The test suite writes no bytecode** for the modules it creates (`tests/conftest.py` sets
  `sys.dont_write_bytecode`), because the race lives in the test helpers too. The stale-bytecode
  test turns it back on for itself.
- Known limit, unchanged: code edited inside a live process (a notebook kernel) after import is
  not reloaded; restart the kernel.

## Implementation

1. `oryxflow/codehash.py` `stale_bytecode(path)` (before `file_hash`): stat the source, read the
   first 16 bytes of `importlib.util.cache_from_source(path)`, stat the pyc. Return False on any
   `OSError`/`ValueError`/`NotImplementedError`, a short header or nonzero flags. Return True iff
   header mtime == `int(src.st_mtime) & 0xFFFFFFFF`, header size == `src.st_size & 0xFFFFFFFF`,
   and `pyc.st_mtime_ns < src.st_mtime_ns`.
2. `oryxflow/codecheck.py` `stale_bytecode_files(task, hashes)` (before `hashes_of`): collect
   `inspect.getsourcefile(type(task))` and `root_for(task) / <rel>` for each hash key's file part
   (skipping `*`); keep `.py` files where `stale_bytecode` is True; remove each one's pyc; warn
   once per file (`_stale_said`): `oryxflow: <file>.py changed in the same second as its last
   compile, at the same size, so Python may have run its OLD code for <Task>. Its cached bytecode
   is removed; the next run redoes the task with the current code`. Return the stale list.
3. `oryxflow/core.py` `build()`, right after `hashes = _codecheck.hashes_of(task) ...`: when
   `fp is not None and _codecheck.stale_bytecode_files(task, hashes)`, set
   `hashes = dict(hashes, **{'<stale bytecode>': 'rerun'})` before `make_record`.
4. `tests/conftest.py` (new): `sys.dont_write_bytecode = True`.
5. `tests/test_stale_bytecode.py` (new): the cross-process case simulated in one process
   (import through the cache, same-second same-size edit with `os.utime`, re-import gets the old
   code, run warns, re-import gets the new code and the run redoes `Up` and `Down`), plus unit
   cases for `stale_bytecode`: fresh/missing cache, hash-checked pyc, and a source mtime
   truncated to the whole second by a file sync.

## Files modified

- `oryxflow/codehash.py` - `stale_bytecode()`.
- `oryxflow/codecheck.py` - `stale_bytecode_files()`, imports `importlib.util`, `inspect`.
- `oryxflow/core.py` - `build()` marks a stale run's record.
- `tests/conftest.py` - new; no bytecode for generated test modules.
- `tests/test_stale_bytecode.py` - new.
- `CHANGELOG.md` - `### Fixed` bullet.

## Verification

```bash
python -m pytest tests/test_stale_bytecode.py -q          # 4 passed
for i in $(seq 20); do python -m pytest tests/test_stale_bytecode.py -q || break; done
python -m pytest tests -q                                  # no previously passing test fails
```

With `codecheck.stale_bytecode_files` patched to return `[]`, the main test fails (no warning,
and the stale output would be kept).

## Review notes (known edges, not changed)

- A task defined in a script run as `__main__` is executed from source, but a stale pyc left by
  an earlier `import` of the same file would still be reported: one spurious warning and one
  extra rerun, after which the pyc is gone. Checking the module's `__cached__` would avoid it.
- A pyc whose own mtime was truncated to the whole second by a copy or sync (while the source
  kept sub-second precision) can be reported once the same way; it heals after one rerun.
- On file systems with whole-second (or coarser) mtimes the race is not detectable; that is no
  worse than before.
- A task held complete by the expensive-recompute guard (`code_version_auto_expensive_s`) is not
  rerun by the marker; it warns that `<stale bytecode>` changed, like any held code change.
