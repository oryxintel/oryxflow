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
- `code_version` may now be declared as a **method** instead of a constant token: whatever it
  returns is folded into that task's code identity, so a task can invalidate on bytes that are not
  Python — a `.sql` file it executes, a prompt template it renders, a `.yaml` config it loads, a
  resolved model snapshot id. Automatic tracking (`settings.code_version_auto`) hashes the AST of
  your Python and is blind to all of those, so until now the only escapes were pinning a constant
  `code_version` and remembering to bump it, or `reset()` by hand — and forgetting either one
  silently served a stale output. Precedence is unchanged: an explicit `code_version`, constant or
  method, overrides automatic tracking for that task, and no existing task changes behavior.
  Two constraints, documented rather than enforced: the method runs on **every completeness check**
  (memoized per traversal), so its body must stay local and cheap — never a network call; resolve
  remote values once outside the task and pass them in as a `Parameter`. And it must be
  deterministic — a `code_version()` returning a timestamp reruns the task every run, which is the
  declared consequence, not a bug. Editing the method body no longer fires a spurious stale-code
  warning: `_is_code_version_stmt()` (`oryxflow/codehash.py`) now strips a `code_version`
  `FunctionDef` from the advisory AST hash, as it already stripped a class-body `code_version = ...`
  pin.
- `oryxflow.hash_files(*patterns, root=None)` — a stable digest of the **content** of every file
  matching these glob patterns, for use inside a task's `code_version()` when the bytes that
  determine its result are files (SQL, templates, config) rather than Python. Paths resolve relative
  to `root` (default: the current working directory) and are sorted, so the digest does not depend
  on filesystem enumeration order, and a rename registers as a change. Raises `FileNotFoundError`
  when a pattern matches nothing: a typo'd path that silently hashed to "no files" would report
  every run as unchanged, which is the failure this exists to prevent.
- `oryxflow.evals` — an LLM-eval harness over
  [pydantic-evals](https://pydantic.dev/docs/ai/evals/), installed with the new `evals` extra
  (`pip install oryxflow[evals]`; pydantic-evals, scipy, typer). Nothing in it is imported by
  `oryxflow/__init__.py`, so a user without the extra is unaffected; import it explicitly as
  `import oryxflow.evals as ev`. pydantic-evals keeps scoring — cases, evaluators, repeats,
  concurrency, retries, the success/failure split — and oryxflow adds the matrix and the cache:
  one parquet per arm, so a re-run of an unchanged arm makes zero calls and editing the target
  function or the case file re-runs only the cells that read it.
  - `ev.sweep(target, cases=..., metric=..., **arms)` runs the grid and returns an
    `ev.EvalResult` (`.df`, `.verdict()`, `.report()`, `.best()`). `target` may be a plain async
    function — no class and no scaffold for a first eval — or an `ev.TaskEval` subclass. Any
    keyword holding a list is an axis. The projected call count, the cached/new split and a cost
    estimate print **before** anything is billed, and a swept parameter the function cannot
    accept raises rather than running N identical cells labelled as branches. `watch=`
    (glob patterns, or a callable) folds files the target **reads** — a prompt template, a
    `.sql` file — into the cache key, so editing one re-runs the arms that read it; without it
    a prompt sits outside the key entirely and an edit re-runs nothing.
  - `ev.TaskEval` is the class form (a `TaskPqPandas` with `persists = ['cases']`): declare
    `dataset`, `metric`, `guardrail`, `slices` and one `case()` coroutine. `preflight()` makes
    one real call before the sweep, so a credential failure costs one call instead of a matrix;
    above `max_failure_rate` (default 20%) the run raises `ev.EvalFailureRateError` and **saves
    nothing**, because a parquet of exceptions is indistinguishable from a measurement.
    `concurrency` is `significant=False`, so it cannot fragment the cache.
  - `ev.load_cases(path, inputs=..., expected=...)` reads cases from `.csv` / `.yaml` / `.jsonl`,
    routing columns by name; a `@path/to/file.md` value is replaced by that file's text.
    `synthetic` cases are reported beside the headline and never in it, and `holdout` cases are
    withheld from every arm with the count stated.
  - `ev.Metric(label, column, where=, higher_is_better=, budget=, coverage=)` is the number a
    sweep is judged on. `ev.rate_ci` / `ev.delta_ci` (scipy, clustered by case) put a confidence
    interval on it. The verdict names **no winner** when the delta interval spans zero, never
    says "wins" unqualified when a guardrail budget broke, flags a metric that is identically 0%
    or 100% in every arm, and flags a denominator that moved between arms.
  - `ev.git_tree(ref, paths, dest=, expect_absent=)` materializes a baseline arm from a git ref
    (byte-exact, and the only mechanism that can restore a *deleted* file); `ev.git_sha(ref)` is
    the resolved SHA to use as that arm's `code_version()`. `ev.Variant(old, new)` is the
    anchored-replacement probe for a rewrite that has not shipped, and raises when its anchor is
    gone rather than silently becoming a no-op.
  - `ev.cli(TaskEvalSubclass)` builds a Typer command line whose flags are **derived** from the
    task's Parameters, so adding a parameter adds a repeatable flag and the two cannot drift.
    Built-ins: `--repeats --concurrency --reset --check --csv --yes`; `--check` runs `preflight()`
    only and exits. `ev.cli` / `ev.build_app` resolve on first use, so `import oryxflow.evals`
    does not pay for Typer.

### Changed
- `state.RECORD_V` is now `4`: the code fingerprint folds in the resolved `code_version` token, and
  freshness records store that **resolved** token rather than the raw attribute. Records written at
  `v3` are treated as unverifiable and silently re-stamped by `build()`'s advisory sweep — a
  one-time re-baseline, never a mass rerun, exactly as the v2→v3 transition did.
- `oryxflow.evals`: **judge validation.** `ev.judge_alignment(df, judge, human)` reports a
  judge's TPR, TNR and Cohen's kappa against human labels, and `ev.corrected_rate(observed,
  alignment)` applies the Rogan-Gladen correction -- the standard estimator for a
  proportion measured with an imperfect test, which is what an LLM judge is. Declare it on
  the metric (`ev.Metric('pairing', 'pairing_holds', human='pairing_human')`) and the
  verdict prints the alignment and the corrected rate per arm. Kappa is reported *and
  labelled* as the one to read, because raw agreement inflates under class imbalance: on a
  90%-pass set a judge that passes everything scores 90% agreement and has learned nothing
  (kappa 0). The correction is REFUSED when `TPR + TNR <= 1`, where the estimator's
  denominator collapses and the "correction" is amplified noise. This matters beyond
  tidiness: an imperfect judge pulls every rate toward its own error floor, so it shrinks
  the very difference a sweep exists to detect -- a true 85% vs 55% reads as 78% vs 60%
  through a judge that is 88% accurate.
- `oryxflow.evals`: a **guardrail inside budget on the point estimate but not on its
  interval** now says so (`the interval for prod reaches 25% -- at this sample size the
  guardrail is not demonstrated, only not yet violated`). At n=12, `0% (0/12)` is entirely
  consistent with a true rate above a 5% budget, and the bound is the thing to act on.
- `oryxflow.evals`: a **lone arm scoring 100%** is flagged as saturated rather than
  reported as a win -- a case set that never fails cannot show a regression or an
  improvement, so the response is harder cases. (Multi-arm saturation was already caught
  by the dead-metric check.)
- `oryxflow.evals`: `ev.PromptArm` -- where one arm READS its prompt files and the cache
  key that MATCHES them, in one object (`.dir()` and `.code_version()`). Written by hand
  those are two places that must agree, and when they drift the failure is silent: a
  baseline arm reading a checked-out tree but keyed on a hash of the LIVE files re-runs
  when a live prompt is edited, and does *not* re-run when it is pointed at a different
  ref. A working-tree arm is keyed on the content of its `globs`, a `ref` arm on the
  resolved sha; both hand back the same layout via `subdir=`, so a loader never has to
  know which arm it got. An arm with no key at all is refused at construction.
- `oryxflow.evals`: the cost confirmation now offers the one-call preflight **at the
  prompt** -- `Run 56 new calls for X? (y/n/c=check one call first)`. `c` spends one call,
  reports whether the wiring works, and asks again. A `--check` flag only helps someone who
  already knows it exists, and the person who needs it most is running the eval for the
  first time. Deliberately still *offered* rather than run automatically before the bill: a
  tool that bills money should not make its first call unasked, however cheap.
- `oryxflow.evals`: `sweep()` accepts `evaluators=` and passes them to the dataset it generates.
  The target function is handed only `inputs`, so scoring against a case's `expected_output` — the
  shape every classifier eval has — previously required building a `Dataset` by hand and passing
  both `cases=` and `dataset=`.
- `oryxflow.evals`: a sweep axis with a **single value** no longer appears in the arm name. It is
  identical in every arm, so it added width to every row of every table and distinguished nothing:
  `model_id=['gemini-3.5-flash'], prompt_version=['prod','preship']` now names the arms `prod` and
  `preship` rather than `model_id_gemini-3.5-flash_prompt_version_prod`. When *no* axis varies the
  full name is kept, since a lone cell called `default` says less.
- `oryxflow.evals`: `verdict()` no longer reprints the call-count header that `sweep()` already
  emitted as the bill, and "Weakest slices" lists only slices actually **below** the arm's own
  rate — a 100% slice under that heading reads as a warning about something that is fine.

### Fixed
- `oryxflow.evals`: the per-case frame was discarding most of what the report carried.
  `_to_frame()` read only assertions, scores, metadata and the output, so **six** things
  went on the floor. Now columns: `case.metrics` (what `increment_eval_metric` recorded --
  token counts, retries, anything a task measures about its own call), `case.attributes`
  (`set_eval_attribute` -- a resolved model snapshot, say), `case.expected_output`
  (flattened to `expected` / `expected_*`), `case.labels`, `case.total_duration`, and
  `report.report_evaluator_failures`. The `expected_*` recovery is the one that changes
  how code reads: a confusion matrix or a per-label breakdown is now a crosstab of two
  frame columns instead of joining the answer key back on by case name.
- `oryxflow.evals`: `EvalResult.verdict()` reports **measured usage** when the task
  recorded it -- `measured usage: input tokens 9,612, output tokens 2,720` -- read from
  the conventional `input_tokens` / `output_tokens` / `total_tokens` / `cost` columns.
  Before this, cost could only ever be the pre-run estimate, because the real numbers
  pydantic-evals collected were being thrown away. Reported by name rather than inferred
  from any numeric column: guessing which column is a token count eventually sums
  something that is not one.
- `oryxflow.evals`: an evaluator that **raised** is no longer indistinguishable from one
  that declined to score. `ReportCase.evaluator_failures` was read by nothing, so a judge
  crashing on every case (a missing API key, say) left its column absent and the metric
  reported `n/a` with no hint that anything was wrong. The frame now carries
  `evaluator_errors` / `evaluator_error` and the verdict prints them above the rate.
  `case.labels` was likewise dropped and is now a column like assertions and scores.
- `oryxflow.evals`: a sync `case()` (or a sync `sweep()` target) no longer silently
  serializes the run. `concurrency` is scheduled as coroutines by pydantic-evals, so a
  blocking case holds the event loop and the cases run one at a time while the setting
  still reads as honored -- same numbers, ~5x the wall clock (8 half-second cases at
  `concurrency=4`: 1.0s async, 4.9s sync). It now emits a `RuntimeWarning` naming the
  task and the ignored concurrency. Nothing about oryxflow itself is async; the
  requirement comes from the eval runner, and `concurrency=1` is the quiet way to opt out.
- `oryxflow.evals`: the dead-metric flag no longer fires on a **guardrail sitting inside
  its budget in every arm**. `0%` false-positives on both sides is the outcome a guardrail
  exists to confirm, and reporting it as "this usually measures the harness" teaches the
  reader to ignore the one line that matters when it is real. A guardrail identically
  *over* budget is still flagged.
- `oryxflow.evals`: declining the cost confirmation on the `ev.cli` path exits 0 with a
  one-line message instead of raising through to a traceback. Choosing not to spend is the
  outcome the confirmation exists to make easy.

- `oryxflow.evals`: a `Metric` no longer counts an **unmeasured** row as a failed one. A row whose
  metric column is null (an LLM judge that grades only some cases, or one switched off with
  `--judge-model none`) is now excluded from the rate exactly as an errored row is, instead of
  being coerced to `False`. Previously that both deflated the numerator and padded the
  denominator: a prompt passing 2 of 2 judged rows out of 6 reported `33%`, and a metric with no
  verdicts at all reported a confident `0%` with a confidence interval. `Metric.compute()` now
  counts only measured rows, a wholly unmeasured metric returns `NaN` and renders `n/a`, and
  `Metric.unmeasured()` / the verdict state how many rows carried no verdict. New
  `Metric.eligible()` is the denominator before that filter.
- `oryxflow.evals`: `sweep()` no longer silently overwrites a case metadata column named `arm` (or
  one named after a sweep axis) with its own cell label. `arm` is the natural word for a dataset
  that compares, say, an `outline` arm with a `guidelines` arm, and the tag destroyed it: any
  `slices=('arm',)` then reported the arm label back with no error and a plausible-looking table.
  The clash is knowable from the cases alone, so it now raises **before any call is made**, naming
  the key and the fix. `arm` joins `RESERVED_METADATA`.

- Automatic code invalidation (`settings.code_version_auto`) no longer raises `RuntimeError:
  dictionary changed size during iteration` when a task's import graph includes a package inserted
  into `sys.path` at run time and a lazy import fires mid-hash. `oryxflow/codehash.py` now snapshots
  `sys.modules` before iterating it, which is also the correct semantics: the hash describes the
  module set as it stood when the traversal began, which is what `codehash.freeze()` already
  brackets `build()` to guarantee.

## [26.8.2] - 2026-08-02
### Added
- `Workflow.dependents(task, root=None, paths=False)` and `Workflow.dependencies(task=None,
  target=None, paths=False)` (plus `WorkflowMulti` variants with a `flow=` selector) — ask the DAG
  "what depends on this task" / "what does it depend on" instead of grepping or hand-rolling a walk
  over `requires()`. `task` may be a **class, family string, or instance**; a class/string is *not*
  instantiated, so it works for fanned-out / DAG-internal families that `get_task()` can't build.
  `paths=True` returns the ordered `root→task` routes (a list of task lists) instead of the deduped
  set. Backed by `core.find_deps` (set) and the new `core.find_paths` (ordered, re-exported as
  `oryxflow.find_paths`); both are now memoized, so the walk is polynomial on diamond-heavy DAGs
  instead of exponential. `dependents(X)` is the discoverable, correctly-named form of the
  confusingly-argued `taskflow_downstream(task, task_downstream)` (kept as an alias).
- `Workflow.check_inputs(tasks=None, raise_on_unused=False, include_clean=False)` — static AST lint
  that reports a declared `@oryxflow.requires` dependency whose data `run()` **loads and never
  reads**. Such a dead dependency is invisible to every dependency query (the edge is real, only
  the data is dead) yet still forces its whole upstream band on every cold build. `preview()`
  surfaces these automatically in an `UNUSED INPUTS` block, deduped per family; `run()` does not
  lint, keeping the execution path free (call `check_inputs()` explicitly for CI). Three verdicts
  — `unused` / `clean` / `unanalyzed` (a shape
  it can't prove is `unanalyzed`, never silently `clean`); outer unpack elements are dependencies,
  inner are that dep's `persists` (a top-level `_` is a finding, an inner `_` is normal). Suppress a
  deliberately-unused dependency with a `# oryxflow: input-unused` comment. New module
  `oryxflow/inputcheck.py`.
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
- `@oryxflow.requires_each` and `Task.requires_grid` accept `derive={'name': fn}` — a further
  parameter set **per branch** from that branch's fanned values, for the setting that follows from
  the value the branch was built for:
  `@oryxflow.requires_each(RegionLoad, region=list(SOURCE), derive={'source': lambda v: SOURCE[v['region']]})`.
  Each function is handed that branch's values (`v['region']`) and its result is passed to the branch
  as a parameter, so it counts towards the branch's `task_id`: editing one entry in `SOURCE`
  invalidates exactly that branch. Previously the only places to put such a lookup were the branch's
  `run()` — where it is invisible to the cache, so changing it silently returned the old output — or
  a hand-written `requires()`, which drops every parameter you forget to forward. Derived names stay
  out of the dependency keys (`inputLoad(task='north')` is unchanged) and off the combining task, for
  the same reason fanned names are.
- `inputLoad(flatten=False)` groups a fan-out's branches under one key
  (`{'input': df, 'RegionNarrative': {'north': ..., 'south': ...}}`), so a task that mixes a fan-out
  with shared dependencies no longer has to pop the keys it recognises and assume the rest are
  branches. `inputLoad(task='<group>')` and `inputLoadConcat(task='<group>')` select just the
  branches; `inputLoadConcat(flatten=False)` returns one DataFrame per group.

### Changed
- Traversal-scoped memoization makes no-op re-runs and `preview()` near-instant on wide fan-out
  DAGs. Three engine questions used to recurse over each task's whole upstream closure once per
  **path** through the DAG, not once per task: `TaskData.complete(cascade=True)`,
  `_resolve_requires()`, and `Task._code_fingerprint`. On a 41-branch fan-out over a shared
  aggregator (75 tasks) a no-op `run()` did 1,428 completeness checks, 586 `requires()`
  resolutions and 8,439 fingerprint evaluations; `preview()` did 5,552 / 873 / 13,685. A new
  per-traversal memo (`oryxflow.core.traversal_scope`, opened by `build()`, `preview()`,
  `Workflow.complete()`, the `taskflow_*` walks, `dependents`/`dependencies` and `accept_code`)
  collapses each to **one execution per unique task** — the same no-op `run()` now does 75 / 43 / 75.
  Behaviour is unchanged: completeness answers are dropped whenever a task materializes
  (`save()`), is invalidated (`reset()`/`invalidate()`), or runs inside a build; the code/DAG-shape
  memos live for the traversal (the engine already forbids code changes mid-build, per
  `codehash.freeze()`). A bare `Task.complete()` outside any traversal is unmemoized, exactly as
  before. With cloud storage this also cuts the per-object existence API calls by the same factor.
- BREAKING: `cls` and `derive` join `path` and `flows` as **reserved parameter names** — declaring
  `derive = oryxflow.Parameter(...)` (or `cls`) on a task now raises `ValueError` at class
  definition. Both are arguments of `Task.clone()` / `Task.requires_grid()`, so the argument
  shadows the parameter: `self.clone(cls=Other)` and
  `@oryxflow.requires_each(Dep, derive={...})` would bind to the argument and the parameter would
  never receive a value. Migration: rename the parameter (`derive_features`, `model_cls`).
- BREAKING: fanning out over a name the dependency has no parameter for now raises `TypeError`
  (`@oryxflow.requires_each(RegionLoad, sector=[...])` where `RegionLoad` has no `sector`). It used
  to produce one dependency key per value all pointing at the **same** task, because `clone()`
  builds its kwargs from the target's `get_params()` and drops the rest — so `inputLoadConcat()`
  returned N copies of one branch's output, tagged as if they were different branches. The error
  lists the parameters the dependency does have. Migration: declare the parameter on the
  dependency, or fan out over one it has.
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
