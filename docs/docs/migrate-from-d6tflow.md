---
title: Migrate from d6tflow to oryxflow
description: oryxflow is the maintained successor to d6tflow. Migrating is a whole-word d6tflow → oryxflow package rename, not an API port — the task API kept its shape, and your existing data/ cache stays valid because task identities come from your class names and parameters, not the package name.
faq:
  - q: "Do I have to migrate all at once?"
    a: "The rename is one atomic pass, so yes — do it in a single commit. It's mechanical and reversible (it's in version control), and there's no half-renamed state worth keeping."
  - q: "Will oryxflow keep receiving updates?"
    a: "Yes — oryxflow is the actively maintained line. See the changelog for what's shipping."
  - q: "Is the on-disk cache format the same?"
    a: "Yes. Outputs are the same formats (parquet, pickle, CSV, JSON, …) in the same per-task layout, keyed by the same task identities — which is why your existing data/ directory keeps working."
---

# Migrating from d6tflow to oryxflow

**oryxflow is the maintained successor to d6tflow.** The library was renamed
`d6tflow` → `oryxflow` and its engine made self-contained; the public API kept its shape.
So moving a project over is a **package rename, not a rewrite** — and your cached results
come with you.

If you only remember one thing: change `import d6tflow` to `import oryxflow` (and the same
prefix everywhere else), reinstall, and you're done. The task classes, `@requires`,
parameters, and `Workflow` you already wrote keep working unchanged.

## Is oryxflow the same as d6tflow?

Yes — it's the same project, renamed and modernized. Everything you know carries over:
`tasks.TaskPqPandas`, `@requires` / `@inherits`, `BoolParameter` and the other parameter
types, `Workflow`, `settings`, `set_dir`, `enable_logging`. The only change to your code is
the top-level name: `d6tflow.` becomes `oryxflow.`

The one thing under the hood that changed for the better: oryxflow is **self-contained**. The
old engine dependency is gone — no external workflow engine to install, no server, no account.
You get a lighter install and the same task model you already use.

## Will my cached data still be valid after migrating?

**Yes — your `data/` cache stays valid.** oryxflow identifies each cached result from your
**task's class name and its parameters**, not from the package it was imported through.
Renaming `d6tflow` → `oryxflow` doesn't rename your task classes, so every task keeps the same
identity and the engine finds its existing output. You migrate the code and keep the computed
results — no forced recompute of expensive steps.

## What changed between d6tflow and oryxflow?

For the vast majority of projects, only the name. The differences worth knowing:

- **Package name** — `d6tflow` → `oryxflow`, everywhere it appears (imports, decorators, base
  classes, parameter types, `settings`, `set_dir`, `Workflow`).
- **Self-contained engine** — the old external workflow-engine dependency was removed; the task
  model, parameters, and executor now live entirely in oryxflow.
- **A focused parameter set** — oryxflow ships the parameter types data science actually uses:
  `Parameter`, `IntParameter`, `FloatParameter`, `BoolParameter`, `DateParameter`,
  `DictParameter`, `ListParameter`, `ChoiceParameter`, `EnumParameter`. If your project leaned
  on an obscure engine-specific parameter type, that's the one place to check (see
  [What doesn't map one-to-one](#what-doesnt-map-one-to-one)).

## How do I migrate a d6tflow project to oryxflow?

It's a whole-word find-and-replace plus a reinstall. Four steps:

**1. Install oryxflow.**

```text
pip install oryxflow
```

**2. Rename the token everywhere.** A whole-word `d6tflow` → `oryxflow` swap across your Python
files, dependency files, notebooks, and any rendered reports. The word boundary matters — it
turns `d6tflow-template` into `oryxflow-template` while leaving an unrelated identifier alone.

```bash
# from the project root — Python, deps, notebooks, reports
perl -i -pe 's/\bd6tflow\b/oryxflow/g' \
  $(grep -rlw d6tflow --include='*.py' --include='*.txt' \
       --include='*.toml' --include='*.ipynb' --include='*.html' .)
```

On Windows PowerShell:

```powershell
Get-ChildItem -Recurse -Include *.py,*.txt,*.toml,*.ipynb,*.html |
  ForEach-Object {
    (Get-Content $_ -Raw) -replace '\bd6tflow\b','oryxflow' | Set-Content $_
  }
```

**3. Rename the data doc, if you have one.** `docs/d6tflow-data.md` → `docs/oryxflow-data.md`
(use `git mv` if it's tracked).

**4. Smoke-test.** Run your pipeline (`python run.py`, or however you run it). Because task
identities are preserved, completed steps load straight from the existing cache instead of
recomputing.

Before and after are identical apart from the prefix:

```python
# before — d6tflow
import d6tflow

class GetData(d6tflow.tasks.TaskPqPandas):
    def run(self):
        self.save(get_frame())

@d6tflow.requires(GetData)
class ProcessData(d6tflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()
        self.save(transform(df))

flow = d6tflow.Workflow(ProcessData)
flow.run()
```

```python
# after — oryxflow (only the prefix changed)
import oryxflow

class GetData(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(get_frame())

@oryxflow.requires(GetData)
class ProcessData(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()
        self.save(transform(df))

flow = oryxflow.Workflow(ProcessData)
flow.run()
```

## What doesn't map one-to-one?

A clean, modern d6tflow project renames without surprises. Flag these for a quick manual look
rather than a blind swap:

- **Module-level `d6tflow.run(...)`** from a very old idiom — oryxflow drives runs through the
  [`Workflow`](workflow.md) object (`oryxflow.Workflow(task).run()`).
- **Engine-specific parameter types** that aren't in oryxflow's
  [focused parameter set](advparam.md) — pick the closest oryxflow type.
- **Removed helpers** from the old API — a survivor after the rename is a signal to check the
  [API reference](reference.md), not to loosen the find-and-replace.

If the token survives the pass in a spot the word-boundary rule skipped, resolve it by hand.

## Let Claude Code do the migration for you

If you use [Claude Code](claude-code-for-data-science.md), install the oryxflow plugin and just
ask it to migrate — it runs the detect → plan → apply → smoke-test flow above for you, shows the
rename plan before touching anything, and offers to commit the result as one clean change.

```text
/plugin marketplace add https://github.com/oryxintel/oryxflow-claude-plugin.git
/plugin install oryxflow@oryxflow
```

Then, in your d6tflow project: *"migrate from d6tflow to oryxflow using the plugin's d6tflow
migration instructions."* (This is a guided rename, distinct from `/oryxflow:migrate`, which
restructures a loose script or notebook into a pipeline.)

## Frequently asked questions

**Do I have to migrate all at once?**
The rename is one atomic pass, so yes — do it in a single commit. It's mechanical and reversible
(it's in version control), and there's no half-renamed state worth keeping.

**Will oryxflow keep receiving updates?**
Yes — oryxflow is the actively maintained line. See the [changelog](changelog.md) for what's
shipping.

**Is the on-disk cache format the same?**
Yes. Outputs are the same formats (parquet, pickle, CSV, JSON, …) in the same per-task layout,
keyed by the same task identities — which is why your existing `data/` directory keeps working.

## Takeaway

- oryxflow **is** d6tflow, renamed and made self-contained — migrating is a whole-word
  `d6tflow` → `oryxflow` rename, not an API port.
- Your **cached results stay valid**, because task identity comes from your class names and
  parameters, not the package name.
- Reinstall with `pip install oryxflow`, run the one-line rename, smoke-test — or let the
  [Claude Code plugin](claude-code-for-data-science.md) do it for you.

Next: [Why oryxflow](why-oryxflow.md) for the full positioning, or the
[Quickstart](quickstart.md) to see the engine in action.
