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
### Added
- `@oryxflow.requires_each(task, **grid)` — declare one dependency **per value** instead of one
  dependency: `@oryxflow.requires_each(ModelTrain, model=MODELS)` on the task that combines them.
  Like `@oryxflow.requires` it copies the dependency's parameters onto the decorated task, minus
  the ones being fanned out (those differ per branch, so the combining task must not carry them),
  and it defines `requires()` as a `requires_grid` over the values. Naming several parameters fans out
  over their cartesian product. Use it instead of hand-writing
  `{v: Task(param=v, shared=self.shared) for v in values}`, which only reaches the branches with
  the parameters you remember to forward.
- `@oryxflow.requires`, `@oryxflow.inherits` and `@oryxflow.requires_each` now **stack** on the same
  task, in any order and any number. The normal combining task needs the fan-out *and* a shared
  dependency that is deliberately not fanned out — the table the branches were built from, a
  baseline to score them against, labels to render with:
  `@oryxflow.requires({'input': ReportInput})` above
  `@oryxflow.requires_each(RegionNarrative, region=REGIONS)`. Previously each decorator owned
  `requires()` outright, so the second one raised and the only way through was `@oryxflow.inherits`
  plus a hand-written `requires()`. The parameter rule holds across all of them: the combining task
  gets every dependency's parameters except the fanned-out ones.
- `@oryxflow.requires_each` accepts a single-entry `{name: Task}` dict to name the fan-out group;
  the group defaults to the dependency's own task family. A named group qualifies its dependency
  keys with that name (`chart_north`), which is how two fan-outs over the same values are
  disambiguated. Unnamed groups keep bare value keys, so existing tasks are unaffected.
- `@oryxflow.requires_each` and `Task.requires_grid` accept a **callable** grid value —
  `region=lambda self: REGIONS[self.sector]` — for a fan-out computed from the task's own
  parameters, which previously forced a hand-written `requires()`. The callable sees the task's
  parameters, not its inputs.
- `inputLoad(flatten=False)` groups a fan-out's branches under one key
  (`{'input': df, 'RegionNarrative': {'north': ..., 'south': ...}}`), so a task that mixes a fan-out
  with shared dependencies no longer has to pop the keys it recognises and assume the rest are
  branches. `inputLoad(task='<group>')` and `inputLoadConcat(task='<group>')` select just the
  branches; `inputLoadConcat(flatten=False)` returns one DataFrame per group.

### Changed
- BREAKING: a task decorated with `@oryxflow.requires_each(Dep, x=[...])` that also declares its own
  `x = oryxflow.Parameter(...)` now raises `TypeError` at class definition. The declaration used to
  survive, putting one branch's value into the combining task's `task_id` — so you got one combining
  task *per value*, each combining all the branches, cached under different ids at N times the cost,
  with no warning. Migration: delete the declaration; the combining task is the point the branches
  converge into and must not carry the fanned parameter.
- BREAKING: two dependencies resolving to the same key now raise `ValueError` from `requires()`
  instead of one silently replacing the other (previously reachable when a fan-out value collided
  with a named dependency). Migration: name one of them —
  `@oryxflow.requires_each({'chart': Chart}, region=REGIONS)` or
  `@oryxflow.requires({'input': ReportInput})`.
- `python_requires` raised to `>=3.9` — up from `>=3.5`, which never held: the package has used
  f-strings (3.6+) throughout for some time, and `install_requires` already imposes 3.9 in practice
  via pandas and pyarrow. PyPI version classifiers added to match. This corrects the metadata; it
  does not drop support for any interpreter the package actually ran on.
- BREAKING: `oryxflow.utils.requires_grid(task_cls, param, values, **base)` is now the
  `Task.requires_grid(cls, **grid)` method — same job, done properly. As a free function it had no
  `self`, so it could not carry the calling task's parameters down to the branches: every shared
  parameter had to be repeated in its `base` kwargs, and one left out was silently missing from the
  children (they got the default instead of the flow's value — a wrong result, not an error). The
  method clones per branch, so parameters propagate exactly as they do through `clone()`. It also
  fans out over several parameters at once — `self.requires_grid(ModelTrain, model=MODELS,
  horizon=[1, 5, 20])` gives the cartesian product. Keys are the value itself for one parameter,
  `name_value` pairs joined with `_` for several, and are what `inputLoad(task=...)` selects on.
  Migration: `requires_grid(ModelTrain, 'model', MODELS)` becomes
  `self.requires_grid(ModelTrain, model=MODELS)` inside `requires()`, and any parameter you were
  passing through `base` can be deleted — it is carried automatically.

### Fixed
- Decorating a task with two dependency decorators no longer raises
  `"<Task>: defines requires() AND is decorated with @requires"` when the task defines no
  `requires()` at all. The check now distinguishes a hand-written `requires()` (still an error —
  the decorator would silently replace it) from a decorator-generated one.
- `inputLoadConcat()` now warns when it would row-stack a shared dependency in with a fan-out's
  branches, which produces a union frame across unrelated schemas. Pass `task='<group>'` to
  concatenate just the branches, or `flatten=False` for one frame per group.
- BREAKING: declaring a Parameter named `path` or `flows` now raises `ValueError` at class
  definition instead of failing silently. `path` is a keyword-only argument the engine uses for the
  flow's data directory, so `MyTask(path='a.csv')` never reached a Parameter of that name — it kept
  its **default**, meaning every value mapped to the same task, and that default was then used as
  the output directory (`x.csv/MyTask/...`). Migration: rename the parameter (`file`, `filename`).
- BREAKING: decorating a task that defines its own `requires()` with `@oryxflow.requires` /
  `@oryxflow.requires_each` now raises `TypeError`. The decorator assigns `requires` after the class
  body is evaluated, so the hand-written method was silently discarded and the task ran with
  whatever the decorator declared. Migration: keep one — drop the decorator and write `requires()`
  (with `self.requires_grid(...)` for a fan-out), or delete the method.
- `inputLoadConcat()` / `concat_iter()` warn when a tag column would overwrite an existing column
  whose values differ from the tag — previously real per-row data (a date column, a category) was
  silently replaced by one scalar parameter value. Re-tagging with the value already present is
  unchanged and silent, since that is how each level of a multi-level aggregation legitimately
  rewrites the level below's tag columns. Silence it with `tagkeys=[...]` or `tag=False`.
- `preview()` / `oryxflow.utils.print_tree()` now show parameters for **every** task in the tree, not
  just the root. A positional-argument slip made the recursion pass `clip_params` as `show_params`,
  so every child rendered as `[TaskName- (PENDING)]` — in a fan-out over a parameter grid the
  branches were indistinguishable. `show_params=False` now also reaches the children.
- The `RuntimeError` raised by `oryxflow.run()` / `Workflow.run()` on failure now names the failing
  task **and its parameters** instead of only `Exception found running flow, check trace` — e.g.
  `Exception found running flow: ModelTrain(model=forest, seed=7): ValueError: training diverged`.
  Up to three root-cause failures are listed. The original exception is still chained via `from`.

## [26.7.26] - 2026-07-26
### Changed
- BREAKING: `TaskAggregator` is now a `requires()`-based group node instead of a task that yields
  its members from `run()`. Because the group is a regular DAG node, it works with `Workflow` /
  `WorkflowMulti` (previously every call raised `UnknownParameterException: ... unknown parameter
  flows`), `preview()` expands it to show each member, and per-flow `path`/`env`,
  `reset_upstream()` and `FlowExport` reach its members. The group still saves nothing of its own
  and is complete when every task it requires is complete. The old form now raises a
  `RuntimeError` at construction naming the fix. Migration: move the members from `yield`
  statements in `run()` into `requires()` (or `@oryxflow.requires`) and leave `run()` empty —
  `class Agg(oryxflow.tasks.TaskAggregator): def run(self): yield T1(); yield T2()` becomes
  `class Agg(oryxflow.tasks.TaskAggregator): def requires(self): return [T1(), T2()]`.

## [26.7.21] - 2026-07-21
### Security
- Releases are now published to PyPI via GitHub Actions **Trusted Publishing** (OIDC) instead of a
  stored API token, and every uploaded file carries a PyPI-recorded **attestation** (PEP 740 /
  Sigstore) proving it was built from this repository by CI. Verify on the PyPI file detail page
  for this release. No install-side change — `pip install oryxflow` is unaffected.

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
### Added
- Initial release of `oryxflow`: the self-contained task engine (`Task`, `requires`/`inherits`,
  the parameter set, `Workflow`/`WorkflowMulti`, targets and task I/O formats), with no external
  workflow-engine dependency.
