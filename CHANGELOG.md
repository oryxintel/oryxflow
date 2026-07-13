# Changelog

All notable changes to **oryxflow** are recorded here. This file is read by humans *and* by AI
coding agents diagnosing regressions after an upgrade, so the format is load-bearing:

- Newest first. One `## [version] - YYYY-MM-DD` heading per release; version is calver `YY.M.D`
  matching `setup.py` / `oryxflow.__version__`. Unreleased work goes under `## [Unreleased]`.
- Group bullets under `### Added` / `### Changed` / `### Deprecated` / `### Removed` /
  `### Fixed` / `### Security` (Keep a Changelog: https://keepachangelog.com/).
- **Every breaking change is a bullet that STARTS with the literal token `BREAKING:`** and carries
  a same-bullet `Migration:` clause with the old→new fix.
- **Name the actual symbol in backticks** (`` `Task.persist` ``, `` `RunResult.summary()` ``), never
  prose. Agents grep this file for the symbol in their traceback.

## [Unreleased]

## [26.7.12] - 2026-07-12
### Added
- Automatic code invalidation, on by default (`settings.code_version_auto = True`): every task
  derives its code identity from the AST hash of its own class plus the project-local symbols it
  transitively references (`codehash.task_hashes`, `'<relpath>::<symbol>'` granularity), so a
  real logic edit (in the task **or a helper it calls**) reruns the task and everything
  downstream on the next `run()`, overwriting in place — while editing an unrelated sibling task
  in the same file reruns nothing (one monolithic `tasks.py` stays cheap). References to other
  Task classes are dependency wiring, never a code dependency (a pinned upstream's unbumped edit
  can't ripple through `requires()` mentions); unresolvable constructs degrade conservatively to
  whole-module granularity. No attribute to maintain, and comment/docstring/formatting edits
  never rerun (AST normalization). Existing
  caches are grandfathered on first contact (baseline stamped, zero reruns). Set
  `settings.code_version_auto = False` for explicit-only tracking. The functional API is covered
  automatically (auto is ambient, no per-task surface). Records live in
  `<dirpath>/.oryxflow-code-status.json` and travel with the data dir.
- `Task.code_version` (str or int, default `None`): a per-task **pin** that suspends automatic
  tracking of that task's own logic — it recomputes only on a deliberate bump (the task and
  everything downstream), for expensive tasks where a refactor-triggered recompute must be a
  decision, or logic the hash can't see. Records are mode-aware (they store both the token and
  the `source_hashes` as of the last materialization), and the `code_version` line itself is
  stripped by the AST normalization (typing it in / deleting / bumping it is a token change,
  never a source change), so pinning/unpinning unchanged code never recomputes ("just resumes"),
  an edit masked during a pinned-unbumped window is caught the moment the pin comes off, and
  pinning in the same edit as a logic change forces a rerun instead of blessing stale output.
- Dependency propagation folds **output identity** (`output_id`, fresh per actual
  materialization, preserved across re-stamps and `accept_code`): downstream reruns exactly when
  an upstream rematerialized — pin toggles and accepts never ripple, and a `reset()`+rerun
  upstream propagates downstream even across separate builds.
- Staleness advisory for pinned tasks: code changed without a bump → cached output is reused and
  the run warns via `StalenessWarning` (a `UserWarning` subclass, visible without
  `enable_logging()`), a loguru record, a `code_warning` event, and `RunResult.warnings`. The
  printed/logged channels dedupe per process on the message — parameterized instances of one
  family produce identical text, and a `WorkflowMulti` run is one build per flow over shared
  upstreams, so per-task dedupe would still flood stdout — re-arming when the condition changes
  or the affected tasks rerun/are accepted; `RunResult.warnings` lists each distinct message once
  per run (`MultiRunResult.warnings` dedupes across flows), and only the event stream records
  every occurrence.
- `oryxflow.accept_code(task)` / `accept_code()`: acknowledge an output-equivalent code change
  without rerunning. With a task instance it re-stamps the task **and its entire upstream dep
  tree** (post-order), stamping a fresh baseline record for outputs that have none yet (this is
  what clears the `output predates current code` mtime-guard warning after an upgrade);
  `Workflow.accept_code(task=None)` / `WorkflowMulti.accept_code(task=None, flow=None)` wrap it;
  called bare they cover **every imported task family that resolves with the flow's parameters**
  (a multi-final pipeline is fully blessed in one call, from a fresh process — no prior run
  needed), and a list of tasks is accepted everywhere (on `WorkflowMulti` prefer the flow
  method — the module-level bulk
  form doesn't know the flows' parameters). Prints a one-line summary of what it re-stamped (or
  that nothing was accepted). The tree walk is fault-isolated: a task whose `requires()`/
  `output()` raises is skipped and reported instead of aborting the walk (a broken `requires()`
  also can't poison the node's own blessing). Never touches `output_id`, so accepting never
  triggers downstream recomputes.
- `TaskData.keep_versions` (default `False`): with `code_version` set, outputs live under a
  readable `.../<Task>/v<version>/` segment so old versions survive bumps (explicit pins only;
  auto-tracked tasks overwrite in place).
- Expensive-recompute guard (`settings.code_version_auto_expensive_s`, default 600): an
  auto-tracked task whose last materialization (recorded as `duration_s`) took longer is held
  complete when its code changes and the run warns (`StalenessWarning`, all channels) with the
  three exits — `reset()` to recompute, `accept_code` if output-equivalent, or pin with
  `code_version` — so a refactor can't silently burn a long run. `None`/`0` disables the guard.
- Records carry schema/interpreter tags (`state.RECORD_V`, `py`): a record with a
  different/missing `v` or Python minor is treated as unverifiable — complete, then silently
  re-stamped (grandfather trust level, `output_id` preserved) — never a mass rerun after an
  upgrade.
- `build()` mtime-revalidates code hashes at most once per module per build
  (`codehash.freeze()`/`unfreeze()`), keeping the auto-hash overhead on small DAGs low.
- Event stream `oryxflow.events`: every run appends `run_started` / `task_ran` / `task_failed` /
  `run_finished` / `code_warning` / `code_accepted` / `task_log` events to
  `.oryxflow/events.jsonl` (stable head; earlier months offload to `events-YYYYMM.jsonl`,
  immutable). Plain JSONL — `tail`/`grep`/`jq` work; writes are async and never fail a run;
  disable with `settings.events = False`. Query via `oryxflow.events.status()` (session-start:
  pending warnings, last run per family, recent failures), `events.runs(task_family=, flow=,
  last=)`, `events.iter_events()` — all return data and print nothing; `events.print_status()` prints
  the status summary (the session-start orientation call for scripts and `python -c`).
- `RunResult.run_id`, `RunResult.reasons` (`{task_id: 'output missing' |
  'code change (auto: <file>::<symbol>)' | 'code change (a -> b)' | 'upstream rerun'}`),
  `RunResult.warnings`. `MultiRunResult` gains aggregate
  `.ran`/`.complete`/`.failed`/`.reasons`/`.warnings` across flows. `task_ran` events carry
  params, code fingerprint, source hashes, `auto` flag, git SHA/dirty, duration and the rerun
  reason; `WorkflowMulti` stamps each per-flow build's events with its flow name.
- Task-authored `self.logger.*(...)` lines are captured as `task_log` events during a build
  (works with logging disabled), so in-run scalars become queryable memory.
- New settings: `settings.events`, `settings.eventspath`, `settings.state_filename`.

### Changed
- `settings.db` (unused) renamed to `settings.state_filename` (the per-data-dir record file
  name, `.oryxflow-code-status.json`).

## [26.7.11] - 2026-07-11
### Changed
- Documentation rewrite and PyPI packaging updates; no API changes.

## [26.6.6] - 2026-06-06
### Changed
- BREAKING: package renamed `d6tflow` -> `oryxflow`. Migration: replace `import d6tflow` with
  `import oryxflow` (and `from d6tflow...` -> `from oryxflow...`); the public API names are
  otherwise unchanged.
