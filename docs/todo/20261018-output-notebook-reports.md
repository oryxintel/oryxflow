# Notebook reports as tasks: parameterized templates executed with papermill, plus shared table formatting

## Context

Analyses built on oryxflow almost always end in a **report notebook**: load a few task outputs,
format some tables, draw some charts, write a paragraph of findings around the numbers, and
publish the executed notebook as a standalone HTML file that gets emailed or dropped in a
channel. The pipeline up to the last task is cached, invalidated on code changes and
reproducible. The report is not. Today, the report step is:

1. copy a template notebook to `viz-<topic>.ipynb` at the project root,
2. hand-edit the copy (task names, the list of things it covers, a start date),
3. run `jupyter nbconvert --to notebook --execute --inplace viz-<topic>.ipynb`, then
   `jupyter nbconvert viz-<topic>.ipynb --to html --output-dir reports/render --no-input --no-prompt --template classic`.

(This is the recipe the oryxflow Claude Code plugin teaches. The three nbconvert flags have to be
remembered, and if one is dropped the raw working notebook gets published, code and all.)

**A real case.** A downstream consumer project produced two report notebooks, one on
*industrial* property subtypes and one on *residential* subtypes. Both had the same 17-cell
structure: an intro, a helpers cell, a headline cell that loads the data, five numbered sections
(rent growth, NOI growth, total returns, cumulative return, cap rates, property counts), then data
notes. Comparing them cell by cell:

| cell | what it holds | line similarity between the two notebooks |
| --- | --- | --- |
| 1 | imports, chart palette + `plt.rcParams` block, `pct()` formatter, `line_chart()`, `on_grid()`, `annualized()`, `metric_table()` (~100 lines) | **0.97**: three comment/decimal differences, all accidental drift |
| 3 | parameters in disguise: `SECTORS = cfg.SECTORS_INDUSTRIAL`, the subtype names and colours, `flow.outputLoad(tasks.GrowthReturnsIndustrial)` / `tasks.CapRatesIndustrial`, a chart start rule | 0.50 |
| 5-15 | the same chart and table calls with different series | 0.65-0.86 |
| 0, 11, 16 | prose: intro, a comparison paragraph, data notes | 0.37-0.67 |

So most of each notebook is copy-paste. The helpers were already drifting apart: one copy rounds
cap-rate axis labels to 1 dp and the other to 2 dp. Neither notebook is part of the DAG, which has
three consequences:

- **Stale reports.** Nothing reruns a report when its inputs change. The README warning "re-execute
  whenever upstream data or task code changed" is all that prevents publishing last week's numbers.
- **Undeclared reads.** The notebook calls `flow.outputLoad(tasks.X)` for whatever it needs.
  `flow.dependents(X)` does not know the report exists, and editing X does not mark the report
  stale.
- **Formatting is reinvented in every notebook.** Each project writes its own `pct = lambda x, d=1:
  '-' if pd.isna(x) else f'{x*100:.{d}f}%'`, and tables are DataFrames of pre-formatted strings.
  A private sibling project's report template uses the other common idiom, which the module should
  standardize: `df.style.format(formatter={'chg_pct': '{:+.1%}', 'value': '{:.1f}'}).hide(axis='index')`,
  `'{:,.0f}'` for counts, a `df_style(df, col_rename=, col_format={fmt: [cols]}, col_sel=,
  link_fun=, hide_axis=True)` helper, and html links in a symbol column. That same project
  executes templates with `papermill.execute_notebook(src, dst, parameters=params)` and exports
  with nbconvert's `HTMLExporter` (`exclude_input=True`, `exclude_input_prompt=True`,
  `exclude_output_prompt=True`). That is the execution path this plan adopts.

**Intended outcome.** A report is a task. You write one template notebook with a papermill
`parameters` cell and declare a `TaskNotebook` subclass that names the template and its upstream
tasks. oryxflow then:

- executes the template with the task's parameters and the paths of its declared inputs,
- saves the executed `.ipynb` and a code-free HTML report as the task's output,
- reruns the report when a parameter, an upstream output, the task's Python, or the template's
  cells change, and skips it otherwise.

The two subtype notebooks above become one template and one task with a
`sector_group = ChoiceParameter(choices=['industrial', 'residential'])`. The helpers cell is
replaced by imports from a shared formatting module, `oryxflow.report`. That module also works in
any ordinary notebook, and its format presets are designed to be shared with the separately
planned Excel writer in `oryxflow.utils`.

### Design decisions

**1. Where it lives (recommended):**

| piece | location | why |
| --- | --- | --- |
| `TaskNotebook` | `oryxflow/tasks/notebook.py` | Task types with optional heavy dependencies already live in `oryxflow.tasks` submodules (`tasks/torch.py`, `tasks/h5.py`, `tasks/dt.py`), and that is where users look for "a task that writes X" (`TaskMarkdown` writes `.md` + `.html`; `TaskExcelPandas` writes `.xlsx`). It is not imported by `tasks/__init__.py`, so the core install never imports papermill. |
| `NotebookTarget` | `oryxflow/targets/notebook.py` | Same pairing as `tasks/torch.py` ↔ `targets/torch.py`. Saving, exporting and invalidating the `.ipynb` + `.html` pair is a target's job (compare `MarkdownTarget`, which writes `.md` + `.html` and deletes both in `invalidate()`). |
| formatting helpers + the notebook-side input loader | `oryxflow/report.py` (top-level module, `import oryxflow.report as rpt`) | This is what a report notebook imports, inside or outside a `TaskNotebook`. It needs only pandas at import time. Styler and matplotlib are imported on first use. |
| install extra | `notebook`: `papermill`, `nbconvert`, `nbformat`, `ipykernel`, `jinja2` | papermill and nbconvert pull in a large Jupyter tree, so this follows the existing extras pattern (`export`, `evals`, `gcs`). `jinja2` is included because pandas `Styler` rendering needs it. matplotlib is **not** a dependency: `rpt.setup()` styles it only when it is importable. |

Rejected locations:

- **A new `oryxflow/output/` package (`output/notebook.py`).** "Output" is already a heavily used
  word in the API (`Task.output()`, `outputLoad`, `outputPath`, `outputLoadMeta`). A package with
  that name would split output formats into two homes, `tasks/`+`targets/` for most and `output/`
  for one, without any rule for choosing between them. The planned Excel writer would then face
  the same choice.
- **Everything in `oryxflow.report`, including the task.** That would make `oryxflow.report`
  depend on papermill at import time, or force awkward lazy imports. It would also make the task
  type the only one not under `oryxflow.tasks`. The two halves have different users: the engine
  side (`TaskNotebook`) runs papermill, and the notebook side (`oryxflow.report`) runs inside the
  kernel and must not need papermill.
- **Only a target (`NotebookTarget.save(params)` executes).** Executing a notebook needs the
  task's parameters and inputs, which belong to `run()`, not `save()`. `save()` stays "write this
  object to storage", the same as every other target.

**2. papermill is an optional extra, and fails at import with the fix in the message.**
`oryxflow/tasks/notebook.py` does `try: import papermill, nbformat, nbconvert except ImportError:
raise ImportError("oryxflow.tasks.notebook needs the 'notebook' extra: pip install
oryxflow[notebook]")`. The wording copies `oryxflow/evals/__init__.py`. `oryxflow/report.py` never
imports these.

**3. How a task declares its template and parameters.**

```python
import oryxflow
from oryxflow.tasks.notebook import TaskNotebook

GROUPS = {'industrial': (tasks.GrowthReturnsIndustrial, tasks.CapRatesIndustrial),
          'residential': (tasks.GrowthReturnsResidential, tasks.CapRatesResidential)}

class ReportSectorSubtypes(TaskNotebook):
    """Rent/NOI growth, returns and cap rates by property subtype, one report per sector group."""
    notebook = 'reports/templates/sector-subtypes.ipynb'      # relative to cwd (the project root)
    report_name = 'viz-{sector_group}-subtypes-growth-returns'   # optional published copy name
    sector_group = oryxflow.ChoiceParameter(choices=list(GROUPS))
    min_properties = oryxflow.IntParameter(default=25)

    def requires(self):
        growth, caps = GROUPS[self.sector_group]
        return {'growth': self.clone(growth), 'caps': self.clone(caps)}
```

- `notebook` is a **class attribute, not a Parameter.** Which template a task runs is part of its
  code, not its data identity, so it is covered by code identity (decision 5) and kept out of
  `task_id`. If you need two templates, write two classes.
- **Every oryxflow Parameter becomes a papermill parameter with the same name.** This covers all
  params, significant or not, using native JSON values (`DateParameter` becomes an ISO string,
  `EnumParameter` its `.name`). The conversion is `json.loads(json.dumps(v, default=str))`, so
  a value always reaches the notebook as something papermill can write into a cell.
- **Extra computed parameters** go in an optional hook, `def notebook_parameters(self): return
  {...}`. It is merged over the param dict, and the hook wins on key clashes. Use it for derived
  values (a label, a list of series) that should not be in `task_id`.
- The engine also injects **three reserved names**: `oryxflow_inputs` (decision 4),
  `oryxflow_task_id`, and `oryxflow_task_family`. A Parameter or `notebook_parameters()` key that
  starts with `oryxflow_` raises `ValueError` at `run()` time. Reserving the prefix keeps the
  notebook namespace safe without adding to `Register.RESERVED_PARAM_NAMES`.
- papermill's own rules apply: the template needs a cell tagged `parameters` that holds the
  defaults. If the template has none, papermill warns and injects at the top, and `TaskNotebook`
  **raises** instead, because an untagged template is almost always a mistake (it would run the
  defaults *after* the injected values and silently ignore them).

**4. How the notebook loads upstream outputs: from injected paths, not by re-importing the flow.**
`run()` injects `oryxflow_inputs`, a JSON dict that mirrors `self.input()`:

```python
{'growth': {'target': 'oryxflow.targets.PqPandasTarget', 'path': 'data/GrowthReturnsIndustrial/...-data.parquet'},
 'caps':   {'target': 'oryxflow.targets.PqPandasTarget', 'path': '...'}}
# a multi-persists dep:  {'split': {'persists': {'train': {...}, 'test': {...}}}}
```

The notebook reads them with `rpt.inputLoad(oryxflow_inputs, task='growth')`, which has the same
`task=` / `keys=` semantics as `self.inputLoad`. It imports the target class by dotted name and
calls `.load()`. Why this way, and not `from flow import flow; flow.outputLoad(tasks.X)` inside the
notebook:

- **The declared `requires()` is the only route to the data**, so the engine guarantees the inputs
  are complete before the kernel starts, a change upstream reruns the report, and
  `flow.dependents(X)` lists the report. A notebook-side `flow.outputLoad(tasks.Y)` for an
  undeclared Y is exactly the undeclared read that makes a stale report possible.
- **The kernel does not have to rebuild the right `Workflow`.** Paths come from the task instance
  that is running, including `Workflow(path=...)` and `WorkflowMulti` per-flow directories. A
  kernel that re-imports `flow.py` would rebuild the default flow, which can be a different
  directory.
- **Authoring stays easy.** While writing the template in Jupyter, `oryxflow_inputs` is `None`
  (the parameters-cell default), and one guarded line resolves the same dict the engine would
  inject:

  ```python
  # parameters
  sector_group = 'industrial'
  min_properties = 25
  oryxflow_inputs = None
  ```
  ```python
  import oryxflow.report as rpt
  if oryxflow_inputs is None:   # running by hand in Jupyter: use the same inputs the task would pass
      import tasks
      oryxflow_inputs = rpt.inputs_of(tasks.ReportSectorSubtypes(sector_group=sector_group))
  rpt.setup()
  growth = rpt.inputLoad(oryxflow_inputs, task='growth')
  caps = rpt.inputLoad(oryxflow_inputs, task='caps')
  ```

  `rpt.inputs_of(task)` is the same function `TaskNotebook.run()` uses to build the injected
  dict, so the authoring path and the engine path cannot diverge.
- **Rejected for v1:** in-memory inputs. `TaskCache*` / `CacheTarget` outputs exist only in the
  engine process, which the papermill kernel is not part of. `inputs_of()` raises `TypeError`
  naming the dep and suggesting a persisted task type such as `TaskPqPandas`. Cloud storage
  (`settings.cloud_fs_enabled`) raises `NotImplementedError` in v1 (see Open questions).

**5. Caching and invalidation: the template's cells are part of code identity.** `TaskNotebook`
defines a *computed* `code_version` (the method form from
`docs/todo/20260822-engine-computed-code-version.md`, already implemented: `core._resolve_code_version`
calls it and hashes the return value):

```python
def code_version(self):
    return {'template': _template_digest(self._template_path()),
            'python': codehash.task_code_hash(self) if settings.code_version_auto else None}
```

- **The template digest covers what changes the result and nothing else:** the `cell_type`,
  `source` and `tags` of every cell, in order. Outputs, `execution_count` and kernel/widget
  metadata are excluded. Without that, opening the template in Jupyter, running it and saving
  would rerun every report, which is the same false-positive class `codehash` avoids by ignoring
  comments and formatting. The digest reads the file as JSON with no nbformat import, so it is
  cheap. It is memoized in a module dict keyed by `(path, mtime_ns, size)`, because `code_version`
  runs on every completeness check (decision 4 of the computed-code-version plan) and saved
  templates with embedded images can be MBs.
- **Why `task_code_hash` is folded in.** An explicit `code_version` (constant or method) turns
  auto AST invalidation into an advisory warning for that task (`core._code_fingerprint_compute`:
  `own` is no longer None). Without the `'python'` entry, editing `requires()`, `report_name` or
  `notebook_parameters()` would only warn and never rerun. Including the auto hash keeps "edit the
  task, just run" true for notebook tasks. `codehash._is_code_version_stmt` strips only a
  `code_version` defined in the user's class body, and this method lives in oryxflow (not
  project-local), so nothing is counted twice.
- **Escape hatches, unchanged semantics.** A subclass that declares `code_version = '3'` pins the
  task (template edits then only warn, which is what pinning means). A subclass that also depends
  on a project helper module the template imports (for example `viz/sector.py`) extends the token:
  `def code_version(self): return [super().code_version(), oryxflow.hash_files('viz/sector.py')]`.
  Tracking the template's own `import` statements automatically is deferred (Open questions).
- Parameters change `task_id` as for any task. An upstream rerun reaches the report through the
  normal recursive `complete()` / dependency fingerprints, with nothing notebook-specific.

**6. Output targets.** `TaskNotebook` keeps `TaskData`'s default single output (`persist =
['data']`, with no `persists` declared), so `self.output()` is one target through the existing
`== ['data']` shortcut in `TaskData.output()`/`save()`, the same as every single-output task type.
`target_ext = 'ipynb'`. `NotebookTarget` owns:

- `<task_id>-data.ipynb` is the executed notebook, code included, for debugging.
- `<task_id>-data.html` is the published report: nbconvert `HTMLExporter(template_name='classic')`
  with `exclude_input=True`, `exclude_input_prompt=True`, `exclude_output_prompt=True`. These are
  the same three flags the plugin's manual recipe requires, now defaults that can't be forgotten.
  Class attributes `html_template = 'classic'` and `html_include_code = False` override them. Images
  stay embedded as base64 (no `ExtractOutputPreprocessor`), so the HTML is one self-contained file
  that can travel on its own.
- `exists()` is true only when **both** files exist, and `invalidate()` removes both (mirrors
  `MarkdownTarget.invalidate`). `load()` returns the HTML `pathlib.Path`, so
  `flow.outputLoad(ReportSectorSubtypes)` hands you the file to open or send.
- **Atomic success.** papermill writes to `<task_id>-data.running.ipynb`, and the file is renamed
  only after execution and export succeed. On `PapermillExecutionError` the partial notebook is
  kept as `<task_id>-data.failed.ipynb`, the error re-raises (so `build()` records it and
  `oryxflow.run(abort=True)` raises `RuntimeError` chained to it), and no `.ipynb`/`.html` pair
  exists, so the next run retries. A half-executed notebook can never look complete.
- **Optional published copy.** If `report_name` is set, the HTML is also copied to
  `<publish_dir>/<report_name formatted with the params>.html`, where `publish_dir` is a class
  attribute defaulting to `'reports/render'`. The copy has a readable, subject-first file name,
  unlike the task-id-based name in `data/`, which can approach the Windows path limit
  (see CHANGELOG 26.10.8). The copy is a convenience and is **not** checked by `exists()`. The
  canonical output is the target in the data directory.
- **PDF is out of scope for v1.** nbconvert's `webpdf` needs Playwright/Chromium and `pdf` needs
  TeX, neither of which is reasonable for an extra. See Open questions.

**7. Execution details, fixed so the executor doesn't relitigate them.**

- `pm.execute_notebook(template, running_path, parameters=..., cwd=os.getcwd(),
  kernel_name=self.kernel_name, progress_bar=False, log_output=False, request_save_on_cell_execute=False)`.
- **`cwd` is the engine process's cwd (the project root).** It is not the template's folder, so a
  template can live in `reports/templates/` and still `import tasks` and resolve `data/`. This
  removes the reason the plugin requires report notebooks to sit at the project root.
- **`kernel_name = 'python3'` by default.** That is the native kernel of the interpreter jupyter
  runs under, which in practice is the environment running the flow. The template's own
  kernelspec metadata is ignored, because a kernelspec saved on another machine is a common
  source of "No such kernel" errors. Override per class.
- The `notebook` path resolves relative to cwd, the same rule as `oryxflow.hash_files` and the
  relative `data/` directory. A missing template raises `FileNotFoundError` naming the resolved
  path, both in `code_version` (the first completeness check) and in `run()`.
- Logging: `self.logger.info("notebook {} -> {}", template, html_path)` at start, DEBUG for the
  injected parameter names (names only, never values, which can be large lists).

**8. `oryxflow.report`: one formatting vocabulary for notebooks, prose and Excel.**

- **The `FORMATS` presets** are a dict of named `Format(py, excel)` pairs. `py` is a
  `str.format` spec (used by Styler and prose), and `excel` is an Excel `number_format` string
  (for the planned Excel writer):

  | name | py | excel | from |
  | --- | --- | --- | --- |
  | `pct` | `{:.1%}` | `0.0%` | downstream `pct()` default |
  | `pct0` / `pct2` | `{:.0%}` / `{:.2%}` | `0%` / `0.00%` | chart axes / cap rates |
  | `pct_signed` | `{:+.1%}` | `+0.0%;-0.0%;0.0%` | sibling template's change columns |
  | `int` | `{:,.0f}` | `#,##0` | counts, thousands separators |
  | `num1` / `num2` | `{:,.1f}` / `{:,.2f}` | `#,##0.0` / `#,##0.00` | values |
  | `bp_signed` | `{:+.0f} bp` | `+0" bp";-0" bp";0" bp"` | cap-rate changes (input already in bp) |
  | `multiple` | `{:.2f}x` | `0.00"x"` | cumulative-return index |
  | `date` | `{:%Y-%m-%d}` | `yyyy-mm-dd` | dates |

  `NA_REP = '-'`, matching the downstream `pct()`. A formats mapping is always `{column: preset
  name | format string | callable}`, the shape pandas `Styler.format(formatter=...)` takes. The
  sibling project's inverted `{fmt: [cols]}` shape is accepted only through a sugar function,
  `rpt.formats(pct=['a', 'b'], int=['n'])`, which returns the canonical shape, so there is exactly
  one shape to pass around.
- **Functions:**
  - `rpt.fmt(value, spec='num1')` formats one value as a string.
  - `rpt.pct(x, decimals=1, signed=False)` and `rpt.num(x, decimals=0)` are shortcuts for numbers
    in prose (`f"... {rpt.pct(r)} a year"`), the most used helper in the downstream notebooks.
  - `rpt.format_frame(df, formats)` returns a DataFrame of strings, with no jinja2. Use it for
    markdown/prose tables and anywhere a Styler can't go.
  - `rpt.table(df, formats=None, index='auto', rename=None, columns=None, links=None, caption=None)`
    returns a styled `Styler`. `index='auto'` **hides a default RangeIndex and keeps a meaningful
    one.** The downstream tables put sector names in a labelled index (`rename_axis(...)`) that
    must stay, while the sibling template hid a meaningless 0..n index, and `'auto'` serves both.
    `True`/`False` force it. `links={'symbol': fn}` renders `fn(value)` as an anchor (the sibling
    `df_style(link_fun=)` case). There is one small CSS preset: numeric columns right-aligned,
    bold header, no row stripes.
  - `rpt.setup(matplotlib=None)` sets pandas display defaults for the session
    (`styler.format.na_rep='-'`, `styler.format.thousands=','`, `styler.format.precision=2`,
    `display.max_columns=50`). If matplotlib is importable (or `matplotlib=True`), it also applies
    the report rcParams preset lifted from the downstream helpers cell: light surface, left-aligned
    bold titles, top/right spines off, light grid, frameless legend, dpi 110. It exposes
    `rpt.PALETTE` (five categorical colours plus ink/grid/surface tokens) and
    `rpt.percent_axis(ax, decimals=0)`.
  - `rpt.inputLoad(spec, task=None, keys=None)` and `rpt.inputs_of(task)` are the decision 4
    bridge.
- **Deliberately not included:** the downstream `line_chart()` with end-of-line labels,
  `on_grid()` and `annualized()`. They are good code but domain-specific (quarterly grids, LTM
  compounding). They belong in the project's `viz/<subject>.py` / `utils/<subject>.py`, imported by
  the template, so two templates for the same project share them without the library guessing at
  chart APIs.
- **Relationship to the planned Excel writer in `oryxflow.utils`.** That writer should accept the
  same `{column: preset}` mapping and read `rpt.FORMATS[name].excel`. `oryxflow.report` is the
  single owner of the presets because it is pure data plus pandas. The writer imports it, which
  is safe because `report.py` imports only pandas at module level, so no dependency cycle or
  extra is added. This plan adds the `excel` strings and a unit test that each one is a valid
  openpyxl `number_format` (openpyxl is already a core dependency). It does **not** build the
  writer.

**9. Prose is not parameterized away.** In the real case, the prose cells were only 37-67% alike.
A paragraph like "Warehouse dominates Industrial" is analysis, not boilerplate. One template
covers both sector groups through the structure, tables and charts, plus data-driven sentences
built from the frames (which those notebooks already did for most numbers). Sector-specific
commentary goes in a markdown cell guarded by `if sector_group == ...` and shown with
`display(Markdown(...))`. When two reports' narratives genuinely diverge, two templates that share
`oryxflow.report` plus a project helper module is still the right answer. The duplication this
plan removes is the helpers cell, the render commands and the copy-and-edit workflow, not the
analyst's writing.

## Implementation

Do the steps in order. Each names its exact file. Don't commit until Verification passes and the
user asks; when committing, this plan goes in the same commit as the code (repo convention).

### 1. `setup.py`: add the extra

In `extras_require`, add `'notebook': ['papermill', 'nbconvert', 'nbformat', 'ipykernel', 'jinja2'],`.
`packages=[...]` needs no change, since `oryxflow.tasks` / `oryxflow.targets` already ship their
submodules, and `oryxflow/report.py` is a plain module of the `oryxflow` package.

### 2. `oryxflow/report.py` (new): formatting + the input bridge

Module docstring: what it is ("helpers a report notebook imports: consistent number formats,
styled tables, chart defaults, and loading a `TaskNotebook`'s inputs"), and that it imports only
pandas at module level. Contents, in this order:

```python
import importlib
import json
import pathlib
from collections import namedtuple

import pandas as pd

Format = namedtuple('Format', ['py', 'excel'])
FORMATS = {...}            # the table in decision 8, exactly
NA_REP = '-'
PALETTE = {'c1': '#2a78d6', 'c2': '#eb6834', 'c3': '#1baf7a', 'c4': '#eda100', 'c5': '#4f5d75',
           'ink': '#0b0b0b', 'ink2': '#52514e', 'grid': '#e4e3df', 'surface': '#fcfcfb'}

def _spec(spec):           # preset name -> py string; format string / callable passed through
def fmt(value, spec='num1', na_rep=NA_REP):
def pct(x, decimals=1, signed=False, na_rep=NA_REP):
def num(x, decimals=0, na_rep=NA_REP):
def formats(**by_preset):  # formats(pct=['a'], int=['n']) -> {'a': 'pct', 'n': 'int'}; dup column -> ValueError
def format_frame(df, formats=None, na_rep=NA_REP):   # returns a copy; never mutates df
def table(df, formats=None, index='auto', rename=None, columns=None, links=None,
          caption=None, na_rep=NA_REP):
def setup(matplotlib=None):
def percent_axis(ax, decimals=0, axis='y'):
def inputs_of(task):
def inputLoad(spec, task=None, keys=None):
```

Notes for the bodies:

- `fmt`/`pct`/`num` return `na_rep` for `pd.isna(value)` (and not for strings). A `KeyError` for an
  unknown preset name lists the valid names.
- `table()`: `df = df[columns]` if `columns` is given, then `.rename(columns=rename)`. Formats are
  keyed by the **renamed** column names, as in the downstream tables, whose headers are
  presentation labels. Then `styler = df.style.format(formatter={c: _spec(s) ...},
  na_rep=na_rep)`. Hide the index when `index is False` or when `index == 'auto'` and
  `isinstance(df.index, pd.RangeIndex) and df.index.name is None`. For links, use
  `styler.format(formatter={col: lambda v, f=f: '<a href="{}" target="_blank">{}</a>'.format(f(v), v)},
  subset=[col])`. If `import jinja2` fails, raise `ImportError` saying `rpt.format_frame` works
  without it, or install `oryxflow[notebook]`.
- `setup()`: wrap each `pd.set_option` so an option missing in an old pandas is skipped, not
  fatal (pandas >= 1.4 has the `styler.format.*` keys). matplotlib: `None` means try-import and
  apply if available; `True` means import (and raise if missing); `False` means skip. The rcParams
  dict is the downstream block verbatim, with the colours taken from `PALETTE`.
- `inputs_of(task)`: walk `task.input()`, which is a target, a `{persist: target}` dict, a list, or a
  `{name: ...}` dict (see the CLAUDE.md "input() indexing gotcha"). Emit
  `{'target': '<module>.<qualname>', 'path': str(target.path)}` per target. Lists become
  `{'0': ..., '1': ...}` so the keys are always strings, which keeps the JSON stable and the notebook
  selection uniform. A multi-persists dep becomes `{'persists': {k: {...}}}`. Raise `TypeError` for
  any `CacheTarget` (message names the dep key and suggests `TaskPqPandas`). Raise
  `NotImplementedError` if `settings.cloud_fs_enabled` (import settings lazily inside the function,
  to avoid an import cycle).
- `inputLoad(spec, task=None, keys=None)`: if `spec is None`, raise `ValueError` with the authoring
  snippet from decision 4 in the message. With one dep and `task=None`, load it; with several deps
  and `task=None`, return `{name: data}` (mirrors `TaskData.inputLoad` with multiple deps). Integer
  `task=` is converted with `str()`. For a target entry, import the class with
  `importlib.import_module(mod)` + `getattr`, then return `cls(pathlib.Path(path)).load()`. For a
  persists entry, return `keys`-selected data (`keys` str → one value, list → list in that order,
  None → dict).

### 3. `oryxflow/targets/notebook.py` (new): `NotebookTarget`

```python
import pathlib
import shutil

from oryxflow.targets import _LocalPathTarget


class NotebookTarget(_LocalPathTarget):
    """Executed notebook (.ipynb, code included) plus a standalone HTML report (.html)."""

    @property
    def html_path(self):
        return self.path.with_suffix('.html')

    def exists(self):
        return self.path.exists() and self.html_path.exists()

    def load(self, cached=False, **kwargs):
        if not self.exists():
            raise RuntimeError('Target does not exist, make sure task is complete')
        return self.html_path

    def save(self, executed_path, html_template='classic', include_code=False):
        """Move a successfully executed notebook into place and export its HTML."""
        # nbconvert import here (module import must not require it for exists()/invalidate())
        ...  # HTMLExporter(template_name=html_template); exclude_input = not include_code;
             # exclude_input_prompt = True; exclude_output_prompt = True
             # write html to html_path (utf-8), then pathlib.Path(executed_path).replace(self.path)
        return self.path

    def invalidate(self):
        for p in (self.html_path, self.path):
            if p.exists():
                p.unlink()
        return not self.exists()
```

Export first and move afterwards. If the export fails, neither final file exists, so the target
stays incomplete.

### 4. `oryxflow/tasks/notebook.py` (new): `TaskNotebook`

```python
import hashlib, json, os, pathlib, shutil

try:
    import papermill as pm
    import nbformat  # noqa: F401  (needed by the target's export)
    import nbconvert  # noqa: F401
except ImportError as e:
    raise ImportError("oryxflow.tasks.notebook needs the 'notebook' extra: "
                      "pip install oryxflow[notebook]") from e

import oryxflow.report
import oryxflow.settings as settings
from oryxflow import codehash
from oryxflow.tasks import TaskData
from oryxflow.targets.notebook import NotebookTarget

_DIGESTS = {}   # (resolved path, mtime_ns, size) -> digest


def _template_digest(path): ...   # json.load; [(cell_type, ''.join(source), sorted(tags))]; md5[:16]


class TaskNotebook(TaskData):
    """Task whose output is an executed, parameterized notebook and its HTML report. ..."""
    target_class = NotebookTarget
    target_ext = 'ipynb'
    # persist stays TaskData default ['data'] -- one output, see decision 6

    notebook = None             # template path, relative to cwd -- required
    kernel_name = 'python3'
    html_template = 'classic'
    html_include_code = False
    report_name = None          # e.g. 'viz-{sector_group}-report'; None = no published copy
    publish_dir = 'reports/render'

    def notebook_parameters(self):
        return {}

    def _template_path(self): ...   # None -> TypeError naming the class; missing -> FileNotFoundError

    def code_version(self):
        return {'template': _template_digest(self._template_path()),
                'python': codehash.task_code_hash(self) if settings.code_version_auto else None}

    def _parameters(self): ...      # params (json-native) | notebook_parameters() | reserved oryxflow_* names

    def run(self): ...
```

`run()` does the following, in order:

1. Resolve the template and use nbformat to check that a cell tagged `parameters` exists; if not,
   raise `ValueError`.
2. Build the parameters (the `oryxflow_` prefix clash check is in `_parameters`).
3. `target = self.output()`. That is a single target because the default `persist == ['data']`
   hits the one-key shortcut in `TaskData.output()` (tasks/__init__.py:247). Then
   `running = target.path.with_name(target.path.stem + '.running.ipynb')`, and remove a stale
   `.failed.ipynb`.
4. In `try:`, call `pm.execute_notebook(...)` with the decision 7 arguments. In
   `except pm.PapermillExecutionError:`, run `running.replace(failed)` and re-raise.
5. `self.save(running, html_template=self.html_template, include_code=self.html_include_code)`.
   `TaskData.save` forwards `**kwargs` to the target and calls `core.traversal_memo_clear()`.
6. If `self.report_name`, format it with
   `self.report_name.format(**self.param_kwargs)`, then `mkdir` the publish dir and run
   `shutil.copyfile(target.html_path, ...)`.

Do **not** declare `persists` on `TaskNotebook`. A name like `['report']` would miss the
`== ['data']` shortcut, which would make `self.output()` a dict and change the `save()` call
shape. The task-id-based filename in `data/` doesn't need to read well, because the published
copy carries the readable name. A user subclass that declares `persists` gets a `ValueError` at
`run()` ("TaskNotebook has exactly one output").

### 5. `oryxflow/__init__.py`: no change

Do not import `report` or `tasks.notebook` from the package root. `import oryxflow.report as rpt`
and `from oryxflow.tasks.notebook import TaskNotebook` are explicit, as with `oryxflow.evals`. (If
the docstring-coverage check from `docs/CLAUDE.md` is extended to new modules, every public
function above needs a docstring. Write them anyway.)

### 6. Tests: `tests/test_notebook_report.py` (new) and `tests/test_report_format.py` (new)

`test_report_format.py` needs only pandas, openpyxl and jinja2. Skip the Styler tests with
`pytest.importorskip('jinja2')`. It checks:

- `fmt`/`pct`/`num` outputs for each preset, plus NaN → `'-'`
- `formats()` sugar and duplicate-column `ValueError`
- `format_frame` does not mutate its input
- `table(index='auto')` hides a RangeIndex (`'<th class="blank level0"'` / row headers absent in
  `to_html()`) and keeps a named index
- the `links` anchor is rendered
- every `FORMATS[...].excel` is accepted by openpyxl: assign it to a cell's `number_format` in an
  in-memory `Workbook()`, save to a `BytesIO`, reload it, and compare

`test_notebook_report.py` starts with `pytest.importorskip('papermill')` and
`pytest.importorskip('nbconvert')`. Build the template **programmatically** with
`nbformat.v4.new_notebook()` in `tmp_path`, so no binary fixtures are committed: a `parameters`
cell (`x = 1`, `oryxflow_inputs = None`), a code cell that loads the input with
`rpt.inputLoad(oryxflow_inputs)` and `print`s a marker `'SUM=' + str(df['a'].sum() * x)`, and a
markdown cell. Run with `monkeypatch.chdir(tmp_path)` and `oryxflow.set_dir(tmp_path / 'data')`.
Upstream is a `TaskPqPandas` returning `pd.DataFrame({'a': [1, 2]})`. Cases:

1. **runs and exports**: the `.ipynb` and `.html` both exist, the html contains `SUM=3`, the html does
   **not** contain the code-cell source text (`rpt.inputLoad`), and `flow.outputLoad()` returns the
   html Path.
2. **params injected**: with `x=2` the html contains `SUM=6`, and the two task_ids differ.
3. **cached**: a second `flow.run()` does not run the report (`result.did_run(Report)` is False).
4. **template edit reruns**: append a cell to the template source and the next run reruns it.
5. **output-only edit doesn't**: write outputs/execution_count into the template's code cells
   (simulates "ran it in Jupyter and saved") and the next run does not rerun.
6. **upstream change reruns**: `flow.reset(Upstream)`, run, and the report reran.
7. **failure is atomic**: a template cell `raise ValueError('boom')` makes `oryxflow.run` raise
   `RuntimeError` whose `__cause__` chain includes the papermill error. Neither the final
   `.ipynb` nor the `.html` exists, and `.failed.ipynb` exists.
8. **guards**: a template without a `parameters` tag gives `ValueError`, a `TaskCache` upstream gives
   `TypeError` naming the dep, and a Parameter named `oryxflow_x` gives `ValueError`.
9. **published copy**: with `report_name = 'viz-{x}-test'` and `publish_dir` under `tmp_path`, the
   file `viz-2-test.html` exists.
10. **lint stays quiet**: `flow.check_inputs()` reports no `unused` finding for the report task.
    The task has no `run()` in its own class body, so `inputcheck` omits it, as for aggregators.

### 7. Docs

- **`docs/docs/reports.md` (new): "Reports from notebooks".** Write it for data scientists, per the
  CLAUDE.md conventions: benefits and what to type, no internals. Cover: install the extra, write a
  template with a `parameters` cell, declare the task, run it, and find the HTML. The page should
  explain that the report reruns when its data, its parameters, its task code or its cells change,
  and not when you merely re-run the template in Jupyter. Include the authoring guard snippet and
  `rpt.table` / `rpt.pct` / `rpt.setup` examples. Do **not** add the page to `TESTED_PAGES`
  (examples need a Jupyter kernel). Mark its code blocks as illustrative.
- `mkdocs.yml`: add `docs/reports.md` to `nav` (next to "Task I/O formats") **and** to the
  `llmstxt` `sections` → `Guide` list. Both are required per `docs/CLAUDE.md`.
- `docs/docs/installation.md`: add a row for `pip install "oryxflow[notebook]"` ("Run report
  notebooks as tasks (papermill + nbconvert)") to the extras table.
- `docs/docs/targets.md`: one table row, "A parameterized report notebook → `TaskNotebook` → `.ipynb`
  + `.html`", linking to `reports.md`.
- `CHANGELOG.md`, under `## [Unreleased]` → `### Added`: `oryxflow.tasks.notebook.TaskNotebook`,
  `oryxflow.targets.notebook.NotebookTarget`, `oryxflow.report` (`table`, `format_frame`, `pct`,
  `FORMATS`, `inputLoad`, `inputs_of`, `setup`), and the `notebook` extra. Name symbols in backticks.
- `CLAUDE.md` (repo root): add `report.py` and the two `notebook.py` submodules to the Layout block,
  and add a **Public API** bullet: "**Report notebooks**: `TaskNotebook` executes a template with
  papermill, injects params + `oryxflow_inputs`, and saves `.ipynb` + code-free `.html`. The
  template's cells are part of code identity, and outputs are not. The notebook reads inputs with
  `oryxflow.report.inputLoad(oryxflow_inputs, task=...)`, never by importing the flow. See
  `docs/todo/20261018-output-notebook-reports.md`."
- **Plugin follow-up (do not edit from this workspace beyond a note):** the plugin's
  "Render / publish a notebook" section and `viz-template.ipynb` should learn the `TaskNotebook`
  path. Leave that to the plugin repo, and mention it in the final report to the user.

## Files modified

- `setup.py`: new `notebook` extra.
- `oryxflow/report.py` (new): `FORMATS`/`Format`, `fmt`, `pct`, `num`, `formats`, `format_frame`,
  `table`, `setup`, `PALETTE`, `percent_axis`, `inputs_of`, `inputLoad`.
- `oryxflow/targets/notebook.py` (new): `NotebookTarget` (ipynb + html pair, atomic save, invalidate
  both).
- `oryxflow/tasks/notebook.py` (new): `TaskNotebook` (template attr, parameter injection, computed
  `code_version`, papermill run, failed-notebook retention, published copy).
- `tests/test_report_format.py` (new): formatting, Styler, and Excel number-format validity.
- `tests/test_notebook_report.py` (new): the 10 end-to-end cases (skipped without the extra).
- `docs/docs/reports.md` (new): user guide page.
- `mkdocs.yml`: nav + llmstxt sections entry for `reports.md`.
- `docs/docs/installation.md`: extras table row.
- `docs/docs/targets.md`: task-type table row.
- `CHANGELOG.md`: Unreleased → Added.
- `CLAUDE.md`: Layout + Public API bullet.
- `docs/todo/20261018-output-notebook-reports.md`: this plan, plus divergence notes if any.

## Verification

1. **Core baseline is untouched (no extra installed).**
   `python -m pytest tests/test_main.py tests/test_workflow.py tests/test_workflowMulti.py tests/test_workflowMulti2.py -q`
   gives the same pass count as before the change (CLAUDE.md records **86 passing**; take the
   actual number from a run on the unmodified tree first). `python -c "import oryxflow,
   oryxflow.report"` works without papermill installed. `python -c "import oryxflow.tasks.notebook"`
   without the extra fails with the `pip install oryxflow[notebook]` message.
2. **Formatting tests** pass: `python -m pytest tests/test_report_format.py -q`.
3. **Notebook tests** need the extra. **Do not install packages without the user's go-ahead**, and
   never create a virtualenv inside a cloud-synced folder. Once the extra is available
   (`pip install -e ".[notebook]"` in the user's chosen environment):
   `python -m pytest tests/test_notebook_report.py -q` gives 10 passed. Without the extra, the
   module is reported as skipped, not failed.
4. **Full suite**: `python -m pytest -q` shows no new failures versus the pre-change run.
5. **End-to-end by hand (don't delegate; this is the acceptance test).** In a scratch directory
   outside the repo, make a two-task project: an upstream `TaskPqPandas`, and a `TaskNotebook` with a
   `ChoiceParameter(choices=['a', 'b'])`, whose template uses `rpt.setup()`, `rpt.table(...)` with
   `pct`/`int` presets, a matplotlib line chart, and a markdown cell that shows the choice. Then:
   - `run()` both choices, open both `reports/render/*.html` files, and check: no code, no
     `In [ ]:` prompts, the chart is embedded, percentages show as `12.3%`, counts show as `1,234`,
     and NaN shows as `-`.
   - Run again: nothing reruns (`result.summary()`).
   - Open the template in Jupyter, run all, and save: nothing reruns.
   - Edit one markdown cell: only the two report tasks rerun, and the reason names a code change.
   - Edit the upstream task's `run()`: upstream and both reports rerun.
6. **Docs build**: `python scripts/build_docs.py` passes, `mkdocs build` shows no link warnings for
   `reports.md`, and the built `site/llms.txt` lists the new page.
7. **Sanitization check before committing**: grep the new and changed files for absolute paths,
   client names, private project names, and data-vendor names. There should be none (CLAUDE.md
   Conventions table).

## Open questions

1. **Auto-tracking the template's own imports.** A template that does `from viz import sector`
   depends on that module, but only a manual `hash_files('viz/sector.py')` in `code_version`
   covers it. Should `_template_digest` parse the code cells' `import` statements and feed
   project-local modules through `codehash`'s module hashing? That path is built around classes,
   so it would need a "hash this source as a pseudo-module" entry point. Proposed for v2 once v1
   is in use.
2. **PDF output.** Is a `pdf` option worth an `oryxflow[notebook-pdf]` extra (nbconvert `webpdf` +
   Playwright/Chromium), or is "print the HTML to PDF" enough? The downstream reports have so far
   been consumed as HTML.
3. **Cloud storage.** v1 refuses `settings.cloud_fs_enabled`. Supporting it means the kernel must
   call `oryxflow.enable_cloud_storage(...)` with the same settings. Should that be injected as an
   `oryxflow_settings` parameter, and applied by `rpt.inputLoad`?
4. **The published copy.** Should `exists()` also require the `reports/render/<name>.html` copy, so
   a deleted copy is regenerated on the next run? Currently it is a convenience file and deliberately
   untracked. There is also a smaller alternative: re-copy it on cache hits.
5. **Values back from the notebook.** Should a report be able to return headline numbers to the
   flow (scrapbook-style `glue`, saved as task metadata via `saveMeta`)? Out of scope for v1.
6. **Ownership of `FORMATS`.** This plan puts the presets in `oryxflow.report` and has the
   planned Excel writer in `oryxflow.utils` import them. If that writer's plan prefers to own them
   (for example as `oryxflow.utils.FORMATS`, re-exported by `report`), settle it in whichever plan
   lands second, and keep a single definition.
7. **Plugin changes.** When `TaskNotebook` ships, should the plugin's scaffold `viz-template.ipynb`
   gain a `parameters` cell and the authoring guard, and should "render with nbconvert" become the
   fallback rather than the default? That is a decision for the plugin repo.

## Implementation notes (divergences from the plan as built)

- **Reconciliation with the Excel spec.** One preset table lives in `oryxflow/report.py` (`FORMATS` of `Format(py, excel)`); `utils.excel_write` reads `FORMATS[name].excel`. `resolve_formats(df, formats, defaults=True)` is in `report.py`; `table`/`format_frame` pass `defaults=False`, so glob keys such as `'*_pct'` and one `formats=` dict work for Excel and for notebook tables alike. A preset-looking name that does not exist raises `KeyError` on both sides, and a bad value falls back to `str(value)` instead of raising.
- **`report.fmt` replaces `pct()`/`num()`.** Use `fmt(x, 'pct')`. The `formats(**by_preset)` helper and the `bp_signed` preset were cut.
- **`inputs_of` stores absolute, resolved paths** (the spec said `str(target.path)`), so a notebook run from another working directory still finds its data.
- **`execute_notebook` gets `language='python'`**, because papermill needs a language for templates without kernelspec metadata. Consistent with decision 7 (`kernel_name='python3'`).
- **Failure handling.** A failed run renames the notebook to `.failed.ipynb` on any exception, not only `PapermillExecutionError`. The start of a run also removes a stale `.running.ipynb` (and an old `.failed.ipynb`).
- **`report.inputLoad` differs from `self.inputLoad` in two ways.** (a) With `keys=None` on a multi-output dependency it returns a dict, where `self.inputLoad` returns a list. (b) With several dependencies and no `task`, it returns `{name: data}`, and positional dependencies come back as `{'0': ..., '1': ...}` where `self.inputLoad` returns a list.
- **`inputLoad(task=..., keys=...)` on a single-file multi-output dependency** (`TaskExcelPandas`, one `ExcelPandasSheetsTarget`) passes `keys` through to the target's `load`, when that `load` takes a `keys` argument.
- **Hook values and `code_version` robustness.** `notebook_parameters()` values go through the same JSON round-trip as parameter values (`isoformat()` when available, then `.item()` for numpy scalars, else `str()`), so numpy scalars and timestamps no longer kill the run. A template missing from the working directory gives `code_version()` the token `'missing'` instead of raising, so `complete()`, `preview()` and `outputLoad()` keep working and the report shows as incomplete; `run()` still raises `FileNotFoundError` naming the resolved path. Resolution stays cwd-relative (decision 7).
- **Papermill noise.** `run()` adds a logging filter on the `papermill` logger while the notebook executes, to drop its "Passed unknown parameter" warning for the injected `oryxflow_*` names. Warnings about the user's own names are kept.
- **Task 6 test detail.** The hidden-index check asserts on `row_heading`, because the `<th id="T_` probe also matches column headers.
- **Deferred (cut from the plan):** the published copy (`report_name`, `publish_dir`), the `_template_digest` memo cache, and `report.setup()` / `PALETTE` / `percent_axis` / `links=`. The docs therefore show `rpt.table`, `rpt.fmt` and `rpt.format_frame`, not `rpt.setup`.
- **Plugin follow-up (not done here):** the plugin's "Render / publish a notebook" section and `viz-template.ipynb` should learn the `TaskNotebook` path.
