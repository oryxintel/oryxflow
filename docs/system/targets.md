# Task identity, completion, and on-disk layout

How oryxflow decides "is this task already done?" and where output lives. The short version:
completion is **output existence at a deterministic path**, and that path is derived from
`task_id`, which is computed with **luigi's exact algorithm** — so outputs produced by the old
luigi-based oryxflow are still recognized as complete.

## The completion check

A task is complete iff its `output()` target(s) exist. There is no separate state DB or
timestamp comparison — existence on disk (or in the in-memory cache for `TaskCache`) *is* the
signal. The engine skips complete tasks when walking the DAG (`core.py`, `build()`).

`output()` builds its target(s) from `_getpath` (`oryxflow/tasks/__init__.py`, `_getpath` at
~line 86, `output` at ~line 116).

## task_id — the identity

Computed once in `Task.__init__` (`oryxflow/core.py`, `self.task_id = task_id_str(...)` at
~line 219) from the **significant** params only (`to_str_params(only_significant=True)`).

`task_id_str` (`oryxflow/core.py` ~line 80) produces:

```
{task_family}_{param_summary}_{md5(sorted_params_json)[:10]}
```

Invariants worth protecting:

- `task_id.split('_')[0]` **must** equal the task family — it's the directory name (see path
  layout below, and `_getpath` ~line 100).
- The hash input is `json.dumps(params, separators=(',', ':'), sort_keys=True)` then MD5,
  truncated to 10 hex chars. The tuning constants live at `core.py:26-29`
  (`TASK_ID_INCLUDE_PARAMS=3`, `TASK_ID_TRUNCATE_PARAMS=16`, `TASK_ID_TRUNCATE_HASH=10`,
  invalid-char regex `[^A-Za-z0-9_]`). `settings.set_parameter_len` (`settings.py:18-21`) mutates
  the first two.
- Insignificant params (`significant=False`) are excluded from the hash, so two tasks differing
  only in such a param share a `task_id` (and thus a path) but remain distinct cached instances.

Many tests hard-code ids like `Task1__99914b932b`. Changing the algorithm rebaselines those —
do it only intentionally.

## On-disk path layout

`_getpath` (`oryxflow/tasks/__init__.py` ~line 86) assembles:

```
{dirpath}/{task_id.split('_')[0]}/{task_id}-{key}.{ext}
```

- `dirpath` = the task's per-flow `path` if set, else `settings.dirpath` (~line 89-93).
- `{key}-` segment is omitted when `settings.save_with_param` is off / `save_attrib` is False
  (~line 101-102).
- Cloud storage (fsspec) reroutes the same relative path under `settings.cloud_fs_prefix`
  (~line 109-112).

## luigi on-disk compatibility

**Question this answers: are tasks completed under the old luigi-based oryxflow still complete
after the decouple?** Yes, given the conditions below.

`task_id_str` is byte-for-byte luigi's `luigi.task.task_id_str` (same JSON serialization, same
MD5, same constants), and the path convention `{dir}/{family}/{task_id}-{key}.{ext}` is the
same one oryxflow used in the luigi era. This was a deliberate constraint of the decouple: the
`task_id` algorithm and the `task_id.split('_')[0] == task_family` directory convention were kept
byte-stable on purpose.

So the same params produce the same `task_id`, the same folder, the same filename → the old
output file satisfies the new completion check.

Compatibility holds **iff**:

1. **Parameter serialization matches luigi's.** The hash is over serialized *significant* param
   values, so each `Parameter.serialize()` (`oryxflow/parameter.py`) must yield the same string
   luigi's equivalent produced. The trimmed param set (`Parameter`, `Int/Float/Bool/Date/Dict/
   List/Enum`) was modeled on luigi's; any divergence in a `serialize()` shifts that task's id
   and orphans old output.
2. **Same `dirpath`** (data dir unchanged) and **same `save_with_param`** — these shape the path
   prefix and filename, not the id.

To spot-check for a real project: generate a `task_id` for a known task and confirm a folder of
that name already exists under the data dir.

## Related

- Instance memoization (`Register.__call__`, `core.py:123`) keys off *all* params and is what
  carries per-flow `path`/`flows` to upstream tasks — separate concern from `task_id`, but it's
  why `Task(**same_params)` returns one object. See CLAUDE.md "Instance memoization is
  load-bearing".
- d6tflow2 stable contract: `task_id`/`task_family`, `to_str_params(only_significant=…)`,
  `external`/`persist` semantics must stay stable (CLAUDE.md).
