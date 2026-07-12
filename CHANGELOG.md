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
- `Task.code_version` (str or int, default `None`): bump it when a task's logic changes and the
  task **and everything downstream** reruns on the next `run()`, overwriting in place. No version
  set anywhere → behavior is unchanged (feature inert); first-time adoption grandfathers existing
  output instead of invalidating it. Records live in `<dirpath>/.oryxflow-code-status.json` and travel with
  the data dir.
- Staleness advisory: each run hashes the task's module and transitively imported project-local
  files (AST-normalized — comment/docstring/formatting edits never warn). Code changed without a
  bump → `StalenessWarning` (a `UserWarning` subclass, shown on every occurrence, visible without
  `enable_logging()`), a loguru record, a `code_warning` event, and `RunResult.warnings`.
- `oryxflow.accept_code(task_or_cls)` / `accept_code()`: acknowledge an output-equivalent code
  change by re-stamping stored hashes without rerunning (the third exit of every warning: bump /
  accept / reset).
- `TaskData.keep_versions` (default `False`): with `code_version` set, outputs live under a
  readable `.../<Task>/v<version>/` segment so old versions survive bumps.
- Event stream `oryxflow.events`: every run appends `run_started` / `task_ran` / `task_failed` /
  `run_finished` / `code_warning` / `code_accepted` / `task_log` events to
  `.oryxflow/events.jsonl` (stable head; earlier months offload to `events-YYYYMM.jsonl`,
  immutable). Plain JSONL — `tail`/`grep`/`jq` work; writes are async and never fail a run;
  disable with `settings.events = False`. Query via `oryxflow.events.status()` (session-start:
  pending warnings, last run per family, recent failures), `events.runs(task_family=, flow=,
  last=)`, `events.iter_events()` — all return data and print nothing; `events.print_status()` prints
  the status summary (the session-start orientation call for scripts and `python -c`).
- `RunResult.run_id`, `RunResult.reasons` (`{task_id: 'output missing' | 'code change (a -> b)' |
  'upstream rerun'}`), `RunResult.warnings`. `MultiRunResult` gains aggregate
  `.ran`/`.complete`/`.failed`/`.reasons`/`.warnings` across flows. `task_ran` events carry
  params, code fingerprint, source hashes, git SHA/dirty, duration and the rerun reason;
  `WorkflowMulti` stamps each per-flow build's events with its flow name.
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
