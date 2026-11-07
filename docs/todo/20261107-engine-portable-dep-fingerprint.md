# Portable fingerprint for untracked dependencies

## Context

A tracked task's completeness has two dimensions (`TaskData._code_ok`): its own code, and
`dep_state`, a fold of one identity per direct dependency (`TaskData._dep_ids`). A dependency
with a code-status record contributes its record's `output_id` (a uuid per materialization,
portable by construction). A dependency that never gets a record here -- a task untracked under
`settings.code_version_auto = False`, or an imported (`external`) task whose producer keeps no
record -- contributed, since 26.11.6, `ext:<task_id>:<newest output st_mtime_ns>`.

A downstream consumer project built its outputs on machine A and synced the data dir (including
`data/.oryxflow-code-status.json`) to machine B through a cloud drive. On B, tasks with existing,
current outputs reported `complete() == False` and reran. Machine A recorded
`ext:...:1793917952067735200`; on B the same file's mtime was `1793917952.000000000`: the sync
kept whole seconds only. FAT and zip keep 2 s, network shares and rsync without `-t` vary. The
fingerprint was not a property of the data, only of one filesystem's copy of it -- a silent
"stale/needless rerun" class of bug, and for expensive tasks a real cost.

### Design decisions

- **Content digest of the dependency's output files, not any mtime key.** The id becomes
  `ext:<task_id>:md5=<digest>`. Rejected: mtime truncated to seconds compared with 2 s
  tolerance (whole-second mtimes are unreliable in more places than syncs, and a tolerance misses
  a genuine rewrite inside the window); size + mtime (same problems). A content key also means a
  producer that rewrites byte-identical output no longer reruns its consumers -- intended.
  Maintainer-confirmed direction.
- **Reuse `hash_files`' machinery.** Its body was split into `utils._digest_files(keyed)`,
  taking `[(name, path)]`, so `tasks._output_digest` doesn't go through glob patterns (a data dir
  path containing `[` would be a glob class). `hash_files` digests are unchanged (a test pins
  one). The cache keys on (name, abspath, size, mtime_ns), so repeated checks in one process
  don't re-read; after a sync (new mtime) the file is read once and the digest is the same.
- **Names relative to the dependency's data dir** (`_resolved_dirpath()`), falling back to the
  bare file name for an output outside it, so a data dir at another absolute path (another
  machine, another user) gives the same digest. A **directory output** counts every file under
  it (recursively, sorted). An output that can't be read (missing, cloud path) gives
  `ext:<task_id>:` as before.
- **Backward compatibility without a `RECORD_V` bump.** The id is stored inside `dep_ids`, and
  `dep_state` is only a fold of it, so a format change does not change the record shape. A
  26.11.6 record (`ext:<task_id>:<digits>`) matches when the dependency's output still has that
  time: exactly, or -- when either side has no sub-second part (precision lost) -- within 2 s.
  That keeps the reported case (recorded on A, read on B after the sync) complete across the
  upgrade. The per-dependency comparison runs only when the folds differ and the record's
  `dep_ids` fold to its `dep_state` (so ids that don't belong to the fold are never trusted).
  `Advisor.advise` then re-stamps the record (preserving `output_id`, so nothing downstream
  moves) in the new form, gated on `_deps_ok(rec)` so a dep that really moved under
  `check_dependencies = False` is never blessed. This mirrors the 26.11.6 handling of the
  26.10.22 fold (`_dep_state(legacy=True)`).
- Records written by unreleased development builds between the `ext:` fold and `dep_ids`
  (no `dep_ids` field) can't be compared per dependency and rerun once. No release wrote them.
- **Rerun reason.** `codecheck._moved_deps` uses the same comparison, so an old id that still
  matches isn't named, and a genuine change reads `upstream output changed since last run:
  rewritten <task_id>`. It doesn't show old/new times (content ids have none). Follow-up:
  `preview()` doesn't show rerun reasons at all; only `RunResult.reasons` and events do.
- `task_ran` events' `datasetVersion` for such inputs changes form from the mtime to `md5=...`
  (see `20261028-sys-runlog-lineage.md`, which describes the mtime form).

### Cost

- One full read of each such dependency's output files per Python session (more precisely, per
  distinct (name, path, size, mtime) set; the cache holds 256 sets and clears when full). At
  md5 speed (~0.5-1 GB/s) a 1 GB parquet costs about 1-2 s on the first check of a session,
  then nothing. Only dependencies without a record pay it; tracked dependencies use `output_id`
  and read nothing.
- Directory outputs: one `rglob` plus a stat per file on every check (to build the cache key),
  and a read of every file on the first check.
- Before, the check was one stat per output file. Projects with very large untracked inputs can
  avoid the read entirely by tracking the producer (a `code_version`, or `code_version_auto`).

## Implementation

1. `oryxflow/utils.py`: split `hash_files` after building `keyed`: `return _digest_files(keyed)`;
   new `_digest_files(keyed)` holds the former sort + stamp cache + md5 loop.
2. `oryxflow/tasks/__init__.py`:
   - `_output_digest(task)`: `'md5=' + _digest_files([(rel name, path) ...])`, '' on any error.
   - `_SYNC_SLACK_NS = 2 * 10**9`, `_same_time(recorded, now)`: equal, or within the slack when
     either side is a whole second.
   - `_dep_ids`: the `ext:` id uses `_output_digest(d)` (`_output_mtime` stays, for old ids).
   - `_code_ok` delegates the dependency dimension to new `_deps_ok(rec, older)`: current fold
     equal -> True; `older` and legacy fold equal -> True; else per-dep `_same_dep_id` over the
     record's `dep_ids` when they fold to its `dep_state`.
   - `_same_dep_id(task_id, old, new)`: equal, or `old` is `ext:<task_id>:<digits>`, `new` is
     an `ext:` id of the same task, and `_same_time(int(digits), _output_mtime(dep))`.
3. `oryxflow/codecheck.py`:
   - `_moved_deps`: "rewritten" uses `task._same_dep_id`.
   - `Advisor.advise`: the converge branch also fires on
     `rec['dep_state'] != task._dep_state() and task._deps_ok(rec)`.
4. Tests in `tests/test_upgrade_records.py` (`TestSyncedDataDir`), and
   `test_rerun_upstream_reruns_the_tracked_task` now changes the upstream's content (an identical
   rewrite no longer reruns, by design). `tests/test_export_bind.py`
   `test_follows_a_producer_without_code_records` used to simulate a producer rewrite by
   setting the file time; it now asserts a time-only change doesn't rerun and rewrites the
   parquet with other values to show a real change does.

## Files modified

- `oryxflow/utils.py` -- `_digest_files` split out of `hash_files`.
- `oryxflow/tasks/__init__.py` -- `_output_digest`, `_same_time`, `_deps_ok`, `_same_dep_id`; content `ext:` ids.
- `oryxflow/codecheck.py` -- `_moved_deps` and `Advisor.advise` use the compat comparison.
- `tests/test_upgrade_records.py` -- `TestSyncedDataDir`; content change in the untracked-upstream test.
- `tests/test_export_bind.py` -- `test_follows_a_producer_without_code_records`: a new file time alone no longer reruns the consumer; rewritten content does.
- `CHANGELOG.md` -- `[Unreleased]` / `### Fixed` bullet.
- `CLAUDE.md` -- the "Share between projects" note on the `ext:` fold.
- `docs/todo/20261107-engine-portable-dep-fingerprint.md` -- this record.

## Verification

```bash
python -m pytest tests/test_upgrade_records.py -q        # TestSyncedDataDir: 7 tests
python -m pytest tests -q                                # full suite, nothing previously passing fails
```

`TestSyncedDataDir` covers: whole-second mtime after a sync stays complete; identical rewrite
doesn't rerun; changed content reruns with the `rewritten <task_id>` reason; a 26.11.6 mtime
record matches and is re-stamped as `md5=`; the reported case (ns recorded, whole second on the
copy) stays complete; an old record whose file moved by 5 s reruns; the digest is the same for
a copy of the data dir at another path. Four of these fail on the code before the change.
