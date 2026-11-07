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

## [26.11.6] - 2026-11-06
### Added
- `oryxflow.codehash.strip_docstrings`, the public name of the docstring normalizer that code hashes use.
- `task_ran` and `task_failed` events carry lineage in OpenLineage vocabulary, kept flat: `project` (the job namespace: the project folder's name), `inputs` and, on `task_ran`, `outputs`. Each entry is one dataset `{namespace, name, datasetVersion}`: `namespace` is the project that owns it, `name` its path relative to that project's root (`None` for a dependency without file outputs), and `datasetVersion` the `output_id` of that materialization (`None` when the task's code is not tracked). Inputs also carry the dependency's `task_id` and `family`. An input's `datasetVersion` equals the `datasetVersion` in the dependency's own `task_ran` outputs, across `FlowImport` too, so `jq '.inputs[] | {name, datasetVersion}'` answers "which build fed this", and the log converts to OpenLineage RunEvents without re-running. Events written before this change have none of these fields. Design: `docs/todo/20261028-sys-runlog-lineage.md`.
- `oryxflow.utils.excel_write(..., format_float=None)` (also a per-sheet option, and passed through `save(formatted=True)`): the number format of float columns that no `formats` key or glob matches, e.g. `format_float='pct'` for a sheet of rates. Any name or code `formats` accepts; a mistyped name raises `KeyError`. `None` keeps the `'float'` preset. `oryxflow.report.resolve_formats` gains the matching `float_default` argument.
- `bind(**values)` on a task loaded with `FlowImport` overrides its exported defaults where you require it: `@oryxflow.requires(exported.ReturnsMarket.bind(refresh='2026-07-18'))` (compare with last quarter). A `built` entry works as is (`X.bind(**X.built[0])`; its `prod` mark is ignored). Design: a bound parameter stays on the imported task (it is part of that task's id, which locates the producer's file) but is not copied onto the requiring task (`core._apply_spec` skips `_oryxflow_bound`) nor passed to it by `Task.clone()`, so it does not enter the consumer's flow parameters or any downstream task_id. Reruns follow the input through the dependency fold (`TaskData._dep_state`): a new bound value, a new exported default, or the producer recomputing that build reruns the consumer; the exact input of every run is recorded in the `task_ran` event's `inputs`.
- `Workflow(..., export=False)` and `WorkflowMulti(..., export=False)`: runs of that flow never update `tasks_export.py`, so an experiment flow cannot add its parameter values to `built`.
- A run warns when a project module it did not import marks a class `export = True` that `tasks_export.py` does not list yet (`FlowExport.unloaded`).
- `Workflow.get_task()` (and so `flow.outputLoad()`, `flow.run()`, `flow.preview()`) accepts a task instance, keeping its own parameter values: `flow.outputLoad(tasks.X(segment='a'))` for a task with a parameter the flow doesn't set. Before: `TypeError: 'X' object is not callable`.
- `preview()` marks the parameters fixed on an imported task (its exported defaults or `bind()`): `Returns[bound: refresh=2026-07-18, sector=office]-{...}`, apart from the values that came through the flow.
- `TaskNotebook.publish`: a path pattern (or list) filled with the task's parameters, such as `publish = 'reports/render/viz-{market}-performance.html'`. Every run also writes the report there (a `.ipynb` pattern gets the executed notebook), through a temporary file. The task counts as complete only while every published copy exists, so a deleted copy is rebuilt. Two reports of one run that would publish to the same file raise `ValueError` naming both.
- `TaskNotebook.report_title` (pattern like `publish`) sets the html `<title>`. Default: the published html's file name, else the task family. Before, every report's title was "Notebook".
- `TaskNotebook.template_deps`: what else the report's code identity watches. Default `'auto'`: the project modules the template's code cells import, and the project modules those import. A list of file globs watches those files instead (`['auto', 'text/*.md']` keeps the imports too); `None` watches the template only.
- `oryxflow.report.default_inputs(task, **params)`: one line after the parameters cell makes a template run both under `TaskNotebook` (it returns the injected `oryxflow_inputs`) and by hand in Jupyter (it builds `task`, a class plus parameters or an instance, and returns its inputs; `FileNotFoundError` when they are not built yet).
- `oryxflow.tasks.notebook.NotebookCellError`, raised when a template cell fails: a `papermill.PapermillExecutionError` subclass whose message is two lines -- the template, the cell (`In[20]`), its failing source line, the exception, and the saved `.failed.ipynb` -- without terminal color codes. The kernel's full traceback, also without color codes, is in `.traceback` and in the chained cause (so in `TaskFailure.traceback` and the `task_failed` event). `NotebookCellError.failed_path` is the saved notebook.
- `oryxflow.report.FORMATS['pct1']`, the same as `'pct'` (one decimal), next to `pct0` and `pct2`.
- A template's parameters cell can be a code cell whose first line is `# parameters` instead of one tagged `parameters` (tags are hard to set outside Jupyter). The template file is not changed.
### Changed
- BREAKING: an exported task's defaults are its prod values. `FlowImport` binds every imported parameter to its exported default, so `@oryxflow.requires(exported.X)` copies no parameter onto your task: before, the producer's parameters passed through the flow and entered your task ids. A same-named parameter of your task is no longer passed to the imported one either. Your task reruns when the producer changes a default (a new quarter) or recomputes that build. Other values: `exported.X.bind(sector='retail')`, or `exported.X(sector=self.sector)` in your own `requires()` for a value that varies with your task; `@oryxflow.requires_each(exported.X, sector=[...])` fans out over one. Migration: remove the producer's parameters from your `Workflow` params / `flow_params.py`; where you chose a non-default value, `bind()` it. Your tasks' ids change once, so they recompute once.
- BREAKING: an exported task (`export = True`) needs a default for every parameter, the value consumers should get. `flow.run()` stops before building with `ValueError: <Task>.<param>: exported parameters need a default -- the value consumers should get (prod)`, and so does `FlowExport.generate()`. Parameters named `prod` or `bind` can't be exported (they clash with `built` and `bind()`). Migration: give each exported parameter its prod value as default (`date_asof = oryxflow.Parameter(default='2026Q1')`); `flow_params.py` overrides are for experiments, run with `Workflow(..., env='dev')`. When prod spans several values of a dimension (sectors), export an aggregator that holds them all.
- `built` in `tasks_export.py` marks the entry whose values are the defaults with `'prod': True`; `external missing output` messages show it as `(prod)`.
- A run in the published data folder that writes a non-default build of an exported task because the run asked for it (`Workflow(X, {'sector': 'retail'})`) warns: `X: experiment written to the prod data folder (non-default sector=retail); run experiments with Workflow(..., env='dev')`. A build another task needs (an aggregator over every sector) doesn't warn. An `env=` run beside the published folder no longer warns that `tasks_export.py` keeps pointing consumers at the published folder.
- `state.RECORD_V` is 6. A code-status record written by 26.10.22 (v5), or by 26.10.21 (v4) under the same Python minor version, is compared with the code exactly: the code is re-hashed with that version's rules (`codehash.rules_of`, `codecheck.comparable_record`), so an edit made before or after upgrading reruns the task, and an unchanged task is re-stamped without rerunning. Before (26.10.22), every older record was trusted and re-stamped silently on the first build that touched it, which blessed edits made after the upgrade to tasks that build did not reach. A record that can't be compared (an older format, or v4 from another Python) is still kept without rerunning, but the build now says so: one `StalenessWarning` naming every such task, in `RunResult.warnings` and the log, and a `code_warning` event per task (`kind: 'old_record'`) that `events.print_status()` lists until it is rerun or accepted. Migration: none for this upgrade. Records that 26.10.22 already re-stamped blindly can't be checked: `reset()` any task you edited since its last run under 26.10.21. Don't go back: once a data dir has run on this version, don't run it on 26.10.22 again (another branch, another machine). 26.10.22 treats these records as an older format, trusts and re-stamps them without comparing code, so edits made meanwhile go undetected -- the same blind spot this release fixes.
- Class attributes `export`, `export_source` and `built` are left out of the code hash (`codehash._METADATA_ATTRS`), like `code_version`: adding or removing `export = True` no longer reruns the task and everything downstream. Records from 26.10.22 that hashed them are compared with the old rules (above), so nothing reruns on upgrade.
- BREAKING: `tasks_export.py` lists each class's parameters by name, and `built` entries hold Python values with their keys sorted (`{'debug': False, 'refresh': datetime.date(2026, 10, 18)}`; a value with no literal form, such as an enum, stays a string). Before, parameters followed module import order, so the file changed depending on which script ran, and `built` values were strings (`'False'`). Existing files are rewritten in the new form on the next producer run; the task ids are unchanged. Migration: compare `built` values with Python values (`b['debug'] is False`), not strings.
- Adding a `code_version()` method to a task that was tracked automatically reruns it once. Before, its first value was adopted without rerunning, so the recipe `{'data': hash_files(path), 'code': ...}` never noticed that the file had changed since the cached run. Adding a plain `code_version = '1'` pin stays free.
- A consumer of an imported task whose producer keeps no code-status record (its code tracking is off) reruns when the producer rewrites that output (by file time); before, it never reran for those. Consumers of such producers rerun once after upgrading.
- `tasks_export.py` update failures are in `RunResult.warnings`, the log and an `export_failed` event, not only a `UserWarning`.
- `TaskNotebook` reruns when a project module its template imports changes (for example `viz/report.py` after `from viz import report`). Before, only the template's cells and the task's own Python counted, so editing a plotting or text helper left a stale report while the run showed success. Comments and docstrings don't count, nor do installed packages, nor modules that define tasks or a flow (a template imports `tasks` to run by hand; a task edit already reaches the report through its data). Upgrading reruns every report whose template imports a project module (such as `viz/`, `utils/` or `cfg.py`) once, on the first run, even if nothing changed: expected, no action needed. Reports whose template imports none keep their cached output. The rerun reason names what changed: `code change (template dep viz/report.py)`, `code change (template cells)` or `code change (task code)`, for reports made by this version or later (older reports show the two code tokens).
- BREAKING: `oryxflow.report.inputLoad(task=...)` on a dependency with several outputs and no `keys` returns `oryxflow.report.Outputs`, a list of the outputs in `persists` order as `self.inputLoad()` returns, that also takes an output's name (`out['series']`, `.keys()`, `.items()`). Before, it returned a dict, so `a, b = rpt.inputLoad(task='x')` silently unpacked the output NAMES. Migration: `out['name']` keeps working; replace iteration over the names (`for k in out`, `set(out)`) with `out.keys()`.
- Two `@oryxflow.requires_each` fan-outs of different tasks over the same values are keyed by task family (`RegionChart_north`, and `inputLoad(flatten=False)['RegionChart']['north']`) instead of raising `ValueError: dependency key ... is produced by both`. Fan-outs whose values don't overlap keep the bare value keys; the same task fanned out twice over the same values still raises.
- `oryxflow.utils.excel_write`: a sheet spec's `formats` is merged over the shared `formats=` (the sheet's keys win, and its globs are tried first; a `None` value drops a shared column format). Before, it replaced them. `formats=False` on a sheet still writes it without number formats.
- `TaskNotebook` no longer makes papermill log `Passed unknown parameter: <name>` for each task parameter the template does not declare (a report inherits every parameter of its upstream band). Names added by `notebook_parameters()` still warn. The kernel runs with `PYDEVD_DISABLE_FILE_VALIDATION=1` unless you set it, which silences the debugger's "frozen modules" note.
- `oryxflow.utils.excel_write`: a column named in a workbook-level `highlight`, `colorscale`, `wrap_columns`, `group_starts` or `id_columns` applies to the sheets that have it. Before, a sheet without the column raised `KeyError`. A name no sheet has still raises, and so does a sheet spec's own option naming a column that sheet lacks.
- `oryxflow.report.table` shows a named index as ordinary leading columns headed by its name, so `formats` and `rename` can address it. Before, pandas put the name on its own header row with a blank band under the column headers.
- `oryxflow.report.table` right-aligns columns of numbers stored with object dtype (a transposed frame), as it does float and int columns.
- `excel_write` takes an open binary file handle as `path` (the atomic `.tmp` + `os.replace` recipe is in the Excel docs). The reports guide says which Python runs the `python3` kernel, how to pick another with `kernel_name`, and that the task's Python counts only with `code_version_auto` on.
- `pip install "oryxflow[evals]"` installs `oryxeval` (Python 3.10+; nothing on 3.9).
### Removed
- BREAKING: `oryxflow.evals` moved to its own package, `oryxeval` (same names; existing eval caches stay valid when only the import lines change). Migration: `pip install oryxeval`, then change only the import lines: `import oryxflow.evals as ev` -> `import oryxeval as ev`, `from oryxflow.evals.<module> import X` -> `from oryxeval.<module> import X`. Keep `import oryxflow` and leave every function body as it is: `code_version()`, `case()` and their helpers are hashed into the cache key, so any edit there (even `oryxflow.hash_files` -> `ev.hash_files`) re-runs every arm. The next run reuses the cache but prints StalenessWarnings; clear them once, from `evals/`: `python -c "import oryxflow; oryxflow.accept_code()"` (re-stamps both stages of every cached arm; the `accept_code(<Eval>)` the warning names covers only the scoring stage). New evals use `ev.hash_files`.
### Fixed
- Code-status records written by a NEWER oryxflow (record `v` above `oryxflow.state.RECORD_V`) are no longer re-stamped: the build's advisory sweep and `accept_code` leave them as they are, the task is kept without rerunning, and one `StalenessWarning` (event kind `newer_record`) names the tasks. Before, an older library rewrote them in its own format and silently blessed any edit made since. `oryxflow.state.is_newer(rec)` is the check. Releases up to 26.10.22 still re-stamp newer records: do not run a data folder a newer oryxflow built with them.
- `TaskNotebook` html reports show dollar amounts as text: `$1.2bn to $3bn` was rendered as a LaTeX formula. The html no longer loads MathJax; set `html_mathjax = True` on the task to render formulas.
- A task whose dependency check moved without any upstream task running in the build reports `upstream output changed since last run`, or `dependency check re-based (record from an older oryxflow)` on the first run after upgrading (`RunResult.reasons`, the `task_ran` event). Before, both read `upstream rerun`, pointing at an upstream that hadn't run. The reason names the input that moved: `upstream output changed since last run: now Returns_2026_10_18_... (was Returns_2026_07_18_...)` when an imported task is bound to other parameters, `...: rewritten <task_id>` when a dependency's output was rewritten (records carry a new `dep_ids` field; a record written before it gives the plain text).
- A run whose flow imports another project's tasks no longer counts those imported classes as this project's when it updates `tasks_export.py` (which could drop entries when both projects share a data-dir parent).
- Imported (`external`) tasks are no longer code-checked by the consumer: no false `StalenessWarning` comparing the export stub with the producer's record (with the expensive-recompute text for a short task), and the consumer's advisory sweep and `accept_code()` never write records into the producer's data dir.
- The code-change warning for a task with automatic tracking off (`settings.code_version_auto = False`) says so, instead of naming the expensive-recompute guard.
- A tracked task (a `TaskNotebook`, or one with a `code_version`) whose dependency is not tracked (`settings.code_version_auto = False` and no `code_version`) reruns when that dependency recomputes. Before, the dependency kept no code-status record, so `TaskData._dep_state` saw no change and the tracked task stayed stale with a green run unless its own output was deleted. An untracked dependency now counts by its output file's modification time. A record written by 26.10.22 matches the old fold once and is re-stamped, so upgrading reruns nothing.
- Two tasks sharing an upstream whose output was missing both rerun. Before, `core.build` visited the first, which reran the upstream, and the second then looked complete and was skipped: stale, with a green run, depending only on dependency order. Now a complete task whose upstream ran in the same build reruns (when `settings.check_dependencies` is on, the default).
- `reset_downstream()`, `invalidate_downstream()` and `run(forced=...)` delete every task in the band that has output on disk. Before, they skipped a task that looked incomplete only because an upstream output was missing, and its stale files were kept.
- A `@requires_each` grid computed from the task's own value of the fanned parameter (`period=lambda t: [prior(t.period)]`, "the same loader at the previous period") raises a `TypeError` that says what to write. Before, it raised a bare `AttributeError` from the lambda. The task can't carry the parameter it fans out over, by design. Write `requires()` by hand with `@oryxflow.inherits` and `self.clone(Loader, period=prior(self.period))`.
- `TaskNotebook` runs a template that has no kernelspec in its metadata (one written with `nbformat`), using `kernel_name`. Before, papermill failed with `AttributeError: kernelspec`.
- `excel_write` with the same option both shared and on a sheet spec (`excel_write(p, {'a': {'df': df, 'formats': {...}}}, formats={...})`) raised `TypeError: dict() got multiple values for keyword argument`.

## [26.10.22] - 2026-10-22
### Added
- Sharing outputs between projects. A task with `export = True` is part of the project's interface: every `run()` that includes one rewrites `tasks_export.py` at the project root (next to `data/`) with a read-only copy of each exported task -- docstring, output format, `persists`, parameters with defaults, `external = True`, the data path relative to the file, and `built`, the parameter values whose output exists. The file is only rewritten when it changes; `run(export=False)` skips it, and a failure to write it only warns.
- `FlowImport('<project dir or export file>')` loads another project's `tasks_export.py`; its tasks are attributes (`exported.SectorReturns`, all in `.tasks`). `@oryxflow.requires` them like your own: parameters pass through the flow, and your task reruns when the other project recomputes. Each export loads under its own module name, so two projects with same-named files don't mix.
### Changed
- BREAKING: `FlowExport(tasks=None, flows=None, save=False, path_export=None)` writes only tasks marked `export = True` (before: every task unless `export = False`), one class per task with the class's defaults (before: one per task instance, so a fanned-out flow repeated a class), paths relative to the export file and in posix form (a Windows path was written with backslashes, which Python reads as escapes), and `generate()` returns the source. The `path_export` default is `tasks_export.py` next to the data dir. Migration: add `export = True` to the tasks to share; `flow.run()` now writes the file, so drop explicit `FlowExport` calls.
- BREAKING: `FlowImport(path, module='tasks_export')` replaces `FlowImport(path, module, path_data)`: the export file carries its data path, and the tasks are attributes. It raises load errors (it printed "Module ... not found." and returned an empty object) and warns when the file was not generated by oryxflow. Migration: `oryxflow.FlowImport('../project1').TaskName` instead of `FlowImport(path='../project1', module='tasks_export', path_data='data').tasks.TaskName` with `path=dirpath` on the workflow.
- A run that meets two different task classes with the same task id (same name and parameters, e.g. an imported task and one of yours) raises a `ValueError` naming both; before, the engine silently treated them as one task. A flow's `path=` / `env=` no longer re-points tasks loaded with `FlowImport` (they read the other project's data folder). A class redefined in the same module (a re-run notebook cell) is not affected.
- An external task with no output fails with `external missing output (looked for <path>); built: <values> -- this task is produced outside this flow: ...` instead of a bare `external missing output` (`TaskFailure.reason`, the `RuntimeError` from `run()`, the `task_failed` event).
- BREAKING: `state.RECORD_V` is 5: code fingerprints use an interpreter-stable AST dump (`codehash._dump`), so they compare across Python versions, and `codehash.PY_TAG` and the records' `py` field are gone. On the first build after upgrading, every existing code-status record is trusted and re-stamped without comparing code, so an edit made before the upgrade and not yet rerun is not detected. Migration: `reset()` any task you edited but have not rerun since its last run; after that first build, detection is exact again. Upgrade every machine that shares a data dir together: an older oryxflow and this one re-stamp each other's records and neither detects edits.
- `hash_files` reads files in 1 MB chunks and caches the digest by each file's size and modification time, so a large data file is not re-read on every completeness check. Digests are unchanged. The docs show the recipe for a task that reads a shared data file: return `{'data': hash_files(path), 'code': oryxflow.codehash.task_code_hash(self)}` from `code_version()` so edits to `run()` still rerun it.
- BREAKING: a task with `task_group` writes under `<data dir>/group=<name>/`. Before, the folder was created at the root of the drive (`/group=<name>/`, `C:\group=<name>\`), outside the data dir. Migration: move an existing `group=<name>` folder from the drive root into the data dir, or let the task rerun.
### Removed
- The `export` install extra (`oryxflow[export]`, Jinja2): `FlowExport` no longer needs it.
### Fixed
- `state._write` retries `os.replace` on `PermissionError` (Windows: a sync client or virus scanner briefly holding `.oryxflow-code-status.json`) for about 4.5 s before raising (after a write gives up, writes in the next minute try once, so a read-only data dir does not stall every write). A failed record write in the build's sweep over complete tasks (`codecheck.Advisor.advise`) now warns instead of aborting the build -- the next build writes it -- and a failed write after a task ran is a warning instead of a debug line.
- Code edits made under one Python version to a task last run under another are detected (the task reruns). Before, a record from another interpreter was treated as complete and silently re-stamped with the edited code's fingerprint.
- Unused-input lint (`inputcheck.check_class`, `Workflow.check_inputs`, `preview()`): with keyed dependencies, a non-literal `task=` (`{n: self.inputLoad(task=n) for n in names}`) or a whole-input load used other than through `data['key']` is reported `unanalyzed`, not `unused`.

## [26.10.21] - 2026-10-21
### Added
- `oryxflow.utils.excel_write(path, data, **options)` writes finished-looking Excel files: styled header, filter arrows, frozen header, number formats and auto-sized columns. A sheet can also be a spec dict with `title`, `notes`, `highlight`, `colorscale`, `banded`, `wrap_columns`, `group_starts`, `id_columns` and `freeze`.
- `ExcelPandasTarget.save(..., formatted=True)` and `ExcelPandasSheetsTarget.save(..., formatted=True)` (so `self.save(..., formatted=True, formats={'*_pct': 'pct'})` in `TaskExcelPandas` and `TaskExcelPandasSingle`) write through `excel_write`. The default `formatted=False` is unchanged.
- `oryxflow.report`: the format presets `FORMATS` (`'pct'`, `'int'`, `'thousands'`, `'date'`, ...), `fmt(value, spec)` and `excel_code(spec)`. A misspelled preset name raises `KeyError` listing the valid names.
- `oryxflow.tasks.notebook.TaskNotebook` runs a parameterized template notebook with papermill and saves the executed `.ipynb` plus a code-free `.html` report (via `oryxflow.targets.notebook.NotebookTarget`). It reruns when its upstream data, parameters, task code or the template's cells change, not when the template's saved outputs change. Needs the new `notebook` extra: `pip install "oryxflow[notebook]"`.
- `oryxflow.report.table(df, formats=...)` (a pandas Styler), `format_frame(df, formats)`, `inputs_of(task)` and `inputLoad(spec=None, task=None, keys=None)` for report notebooks. `table` takes the same `formats=` dict as the Excel writer.

## [26.10.8] - 2026-10-08
### Changed
- BREAKING: `core.task_id_str` caps the readable parameter summary in a `task_id` (and so in
  every output file name) at 80 characters (`core.TASK_ID_TRUNCATE_SUMMARY`, set with
  `settings.set_parameter_len(summary=...)`). Before, only each value was truncated (64 chars, up to
  20 values, a `ListParameter` counting its whole list), so a task inheriting several long or list
  parameters got a 269-character path -- past the Windows 260 limit, and past what the Excel
  desktop app opens (about 218). Uniqueness is unchanged: the 10-char hash still covers the full
  parameters. Tasks whose summary was already 80 characters or less keep their `task_id`.
  Migration: none required -- tasks with a longer summary get a new `task_id`, so they recompute
  once on the next run (their old output files and code-status records are left orphaned; delete
  them by hand if you want the space back).

## [26.10.5] - 2026-10-05
### Added
- `oryxflow.evals`: **a cell is now two cached stages -- the model calls, then the scoring.**
  `TaskEval` generates an `<Eval>Outputs` task (`TaskJson`, one record per case x repeat: output,
  inputs, timing, metrics, attributes) that the scoring task requires. Editing an evaluator,
  changing a judge rubric or swapping the judge model re-scores the stored outputs with ZERO calls
  to the model under test; editing what the arm reads re-runs both. Before, one `code_version()`
  keyed both, so a scorer change either re-billed every call or served stale scores. A subclass's
  `code_version()` keeps its meaning (what the ARM reads) and now keys the model-call stage only;
  the new `TaskEval.scorer_version()` keys the scoring stage and defaults to every evaluator's code
  and configuration (`as_spec()`). A typed output is rebuilt with `model_validate`, so
  `ctx.output.attr` scorers are unchanged. Evaluators that read the run's span tree
  (`HasMatchingSpan`, `ToolCorrectness`, `TrajectoryMatch`, `ArgumentCorrectness`, `MaxToolCalls`,
  `MaxModelRequests`, or any with `needs_trace = True`) score in the model-call stage, because spans
  are not stored.
- `oryxflow.evals`: `sweep(rescore=True)` / `ev.cli --rescore` -- re-run only the scorers over the
  stored outputs (after a transient judge failure, say). The bill prints `re-scoring N arms from
  stored outputs: no model calls` and counts an LLM judge's calls separately.
- `oryxflow.evals`: `baseline=` on `sweep()` (or a `baseline` attribute on a `TaskEval`; default:
  an arm named `baseline`). The verdict then reports every arm's paired difference from it with its
  interval, and names no winner -- mixed results need a written judgement, not a pass/fail.
- `oryxflow.evals`: `EvalResult.side_by_side(path=None, all=False, template=None)` -- every arm's
  output per case as markdown, by default only the cases where the arms disagree on a column the
  verdict reads, plus failures. Rendered with Jinja from the stored outputs (no model call);
  `template=` takes a path or a string.
- `oryxflow.evals`: `load_cases()` also takes a list of row dicts, routed exactly like a file's
  rows -- for a probe that keeps a handful of cases inline.
- `oryxflow.evals`: `ev.cli --side-by-side` writes `EvalResult.side_by_side()` to the eval module's
  own `results/<date>-<arms>-side-by-side.md`, wherever the run was launched from.
- `oryxflow.evals`: `ev.cli` warns when launched from a directory other than the eval script's own
  -- the cache and relative paths resolve against the working directory, so such a run reads and
  writes a different cache.
- `oryxflow.codehash.callable_hashes(*fns)` / `callable_code_hash(*fns)` -- the function-level
  sibling of `task_hashes`: a function's own source plus the closure of the module-level symbols it
  references. What lets two stages of one class own disjoint code identities.
- `settings.warn_nested_dir` (default `True`) — a one-time warning when the output directory
  resolved for a run is empty but a directory of the same name already exists in a parent. The
  output directory has always resolved against the current working directory, so running a flow
  from a subdirectory finds an empty `data/` beside it, reports every task incomplete and
  recomputes everything — re-paying for any metered API call those tasks make, with no signal
  that it happened. The warning names both directories and the fix; resolution is unchanged and
  nothing is relocated (searching upward for an existing cache and silently using it was
  rejected: a flow quietly reading a *different* cache is worse than one quietly rebuilding).
  Emitted from `set_dir()` only, never inside the execution loop, and suppressed when the local
  directory already holds output (a deliberate second project) or by
  `oryxflow.settings.warn_nested_dir = False`.
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
- `oryxflow.evals`: the `evals` extra now requires `pydantic-evals>=2` (for the tool-call
  evaluators, `CaseLifecycle` and Windows-safe report output) and adds `jinja2` (for
  `side_by_side()`).
- `oryxflow.evals`: `reset=True` / `--reset` discards BOTH stages, model calls included. Use
  `rescore` to re-run only the scorers.
- `oryxflow.evals`: the function form's arm identity now also follows what the target CALLS
  (`codehash.callable_code_hash`), not only the target's own body -- editing a project-local helper
  or prompt loader re-runs the arms that use it.
- `oryxflow.evals`: `rep` is read from pydantic-evals' own `"<name> [i/N]"` naming when outputs
  are stored, instead of being re-derived from row order (`TaskEval._assign_reps` is now a no-op
  kept as an override point).
- `requires_each`: the "declares a parameter and also fans out over it" `TypeError` now names the
  legitimate case it used to steer wrong - a task that compares its own value with others
  (`sector` vs `sector_compare`) - and points to `self.requires_grid(...)` in `requires()` rather
  than only "Remove the declaration". The `requires_each` docstring shows that form with
  `inputLoadConcat(tagkeys=[...])`. Message and docs only; behavior unchanged.
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
- `oryxflow.evals`: a `TaskEval` subclass now re-runs when its case set changes. Only the
  function form folded the cases into its cache key, so editing `cases.csv` under the class form
  served the previous cases' results.
- `oryxflow.evals`: under pandas 3 the `<field>_preview` columns were never written, because text
  columns get a `str` dtype instead of `object`.
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
