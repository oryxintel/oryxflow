# Sharing Workflows and Outputs

## Introduction

Handing off analysis is usually painful: you zip up a folder of data files and a separate script, and the person on the other end has to figure out which file came from which step and how to regenerate them. Because a oryxflow task bundles the code, its parameters, and its output together, you can share the **whole reproducible pipeline** — the recipient runs the tasks that still need running and loads any result by name, no manual file-shuffling.

Common cases where you want to do this:

- data engineers share cleaned, ready-to-use data with data scientists — without re-sending it every time it changes
- vendors sharing data with clients — as a pipeline the client can re-run, not a one-off dump
- teachers sharing data with students — everyone starts from the same reproducible outputs

oryxflow gives you three approaches, from simplest to most advanced — this page covers them in order:

1.  **Share the data folder** — version `data/` with Git LFS (optionally split by `env=` so you hand off only what you mean to). The recipient clones and gets the exact outputs.
2.  **Export and import tasks** — mark tasks `export = True` and every run writes `tasks_export.py`, a code-free, read-only copy of them; another project loads it with `FlowImport` and requires those tasks like its own.
3.  **Bridge separate flows** — `attach_flow` lets one flow read another flow's outputs at run time, across projects or environments.

## Sharing the data itself: Git LFS

Because oryxflow writes every task output under `data/`, the simplest way to share results is to version that folder alongside your code with [Git LFS](https://git-lfs.com). Then you version and share your data as easily as your code: a teammate clones the repo and gets the exact datasets each run produced, so nobody re-runs the expensive tasks just to obtain outputs someone already computed. This is the recommended approach for most teams.

The [Claude Code plugin](claude-plugin/index.md) sets this up for you in one step with `/oryxflow:init-gitlfs` (it puts `data/` under Git LFS and wires the `.gitattributes`). To do it by hand, install Git LFS and track the data directory:

    git lfs install
    git lfs track "data/**"
    git add .gitattributes data/
    git commit -m "Track data outputs with Git LFS"

Tracking `data/**` also picks up the small `.oryxflow-code-status.json` record oryxflow keeps *inside* the data directory (the code fingerprints behind [Automatic code invalidation](managing-workflows.md#automatic-code-invalidation)), so it travels with the outputs it describes — move or restore a data directory whole and the freshness information comes along. The separate `.oryxflow/` event log (run history) is high-frequency exhaust, not a shared output: keep it gitignored and export history deliberately if a teammate needs it.

The next section refines this — splitting `data/` by `env=` so you share only part of it. The later Export/Import and Attach sections cover the cases Git LFS doesn't: reading another project's outputs from your own flow, or one flow's outputs from inside another flow.

## Separating environments with `env=`

Often you don't want to share *everything* under `data/` — production outputs, a colleague's scratch experiments, and your own dev runs may all live there, and only some of it is worth handing off. The `env=` argument to `Workflow` keeps them in separate subfolders so you can be selective about what gets shared (Git-LFS-track or commit just the one you mean to).

Passing `env='prod'` writes all task output under `data/env=prod/` instead of `data/`:

```python
flow = oryxflow.Workflow(TaskTrain, env='prod')
flow.run()          # output now under data/env=prod/
# a separate dev environment, isolated from prod:
flow_dev = oryxflow.Workflow(TaskTrain, env='dev')
flow_dev.run()      # output under data/env=dev/
```

Because each environment is its own directory, you can share just the one you mean to — commit `data/env=prod/` and leave `data/env=dev/` out — without the environments overwriting each other or leaking. When you later read these outputs (an imported task, below), pass the same `env=` to point at the environment you want:

```python
flow_prod = oryxflow.Workflow(TaskTrain, env='prod')
df = flow_prod.outputLoad()     # reads data/env=prod/
```

Just make sure the environment you point at is the one whose outputs actually exist — reading `env='prod'` when the shared data was saved without an `env` (plain `data/`) will find the tasks incomplete and rerun them.

## Exporting tasks for other projects (`export = True`)

Mark each task another project may read with `export = True`. That is the whole producer side:

```python
class SectorReturns(oryxflow.tasks.TaskPqPandas):
    """Quarterly total returns by sector. Columns: sector, ret_pct (decimal, 0.05 = 5%)."""
    export = True
    sector = oryxflow.Parameter(default='office')

    def run(self):
        self.save(...)   # your logic -- NOT exported
```

Every `flow.run()` that includes an exported task rewrites `tasks_export.py` at the project root (next to `data/`). For each exported task it holds a read-only copy: the docstring, the output format, `persists`, the parameters with their defaults, `external = True` (never run, only read) and the data path, written relative to the file so it works wherever the folders live. `built` lists the parameter values whose output exists:

```python
class SectorReturns(oryxflow.tasks.TaskPqPandas):
    """Quarterly total returns by sector. Columns: sector, ret_pct (decimal, 0.05 = 5%)."""
    external = True
    persists = ['data']
    path = str(_HERE / 'data')
    sector = oryxflow.parameter.Parameter(default='office')
    built = [
        {'sector': 'office'},
        {'sector': 'retail'},
    ]
```

There is no `run()` in the file, so you can hand your outputs to someone without your code: share `tasks_export.py` with the `data/` folder (Git LFS above makes that easy).

Commit `tasks_export.py`. It is your project's interface: when you rename an exported task, change its parameters or its `persists`, the diff shows it, and so does every project reading it. The file is only rewritten when its content changes, and `flow.run(export=False)` skips it for a throwaway run. To write it without running, call `oryxflow.FlowExport(save=True).generate()`.

Export tasks whose output another project can read without your code: parquet, csv or json. A pickled object may need your classes to load.

## Reading another project's outputs (`FlowImport`)

Two separate projects, each with its own flow and its own `data/`: `project2` needs a result `project1` exports. Load `project1`'s export and require its tasks like your own:

```python
exported = oryxflow.FlowImport('../project1')     # reads ../project1/tasks_export.py

@oryxflow.requires(exported.SectorReturns)
class SectorReport(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()                       # project1's output for this sector
        self.save(df)

oryxflow.Workflow(SectorReport, {'sector': 'retail'}).run()
```

What that gets you:

- **Parameters pass through the flow.** `SectorReport` takes on `sector` from `SectorReturns`, so you choose the sector the way you choose any parameter, including one flow per sector with `WorkflowMulti`. `exported.SectorReturns.built` lists what `project1` has built.
- **Reruns follow upstream.** When `project1` recomputes `SectorReturns`, `SectorReport` reruns on its next run; when nothing changed there, nothing reruns here.
- **No side effects on the other project.** Imported tasks never run from here. If an output isn't there, the run stops with `external missing output (looked for <path>); built: ...` -- pick a built value, or have `project1` build the one you need.
- **No path work.** The export knows where `project1` keeps its files, and two projects whose files share a name (`tasks.py`, `cfg.py`) don't mix: the export imports nothing from `project1`.

**Don't read another project's outputs by file path or run id.** Output file names come from the task's name and parameters, and the format has changed between releases, so a hard-coded path breaks silently, skips the file-format handling and never notices when the upstream project recomputes. A run id names one build in the event log, not an output -- the cache keeps the latest output per task, so there is no "that run's output" to fetch. A file path is fine for a consumer *outside* oryxflow (Excel, a BI tool); hand it `flow.outputPath(...)`, not a typed-in path.

## Reading another flow's outputs (`attach_flow`)

In more complex projects, users need to import data from many sources. Flows can be attached together in order to access the data generated in one flow inside the other.

Attach a flow to a workflow with `attach_flow(flow, name)`; every task in that workflow then sees it under `self.flows[name]`, so a task's `run()` can load another flow's output without wiring an explicit `requires()` dependency.

### `requires` vs `attach_flow`: the defining difference

The two mechanisms look similar — both let one task read another's output — but they solve different problems, and the difference comes down to two things: **whose data root** the read uses, and **whether oryxflow tracks it**.

A normal `@oryxflow.requires` edge assumes both tasks live in **one flow with one data root**. When you build a `Workflow`, it pushes its `path` (and `env`) down onto *every* upstream task instance — that is how the whole DAG agrees on where `data/` is. oryxflow then *tracks* that edge: if the upstream output is missing, it builds it.

`attach_flow` is the opposite on both counts:

- the attached flow is a **self-contained handle that keeps its own** `path`/`env`, so `self.flows['x'].outputLoad()` reads from *that* flow's data location — which can be a different project's `data/` or a different `env=`;
- it is **not tracked as a dependency** — oryxflow will not build the source flow for you.

So the mental split is: `requires` = "this is part of my pipeline, same data root, build it if needed"; `attach_flow` = "reach into a *separate* flow that has its own lifecycle and data root, and read what it already produced."

### Why not just load it yourself?

The obvious alternative is to load the other flow's output in your driver script and pass the DataFrame into your task. **Don't** — it throws away the one thing oryxflow is for. In oryxflow an output's location is a deterministic function of *(task, parameters)*: you never type a path, you ask for a result and the engine computes where it lives. Hand-loading reintroduces exactly the path bookkeeping the library removes, and it gets worse the moment the source flow is parameterized:

- a source with **no parameters** — you already have to know or hardcode one path;
- a **parameterized** source — now there is a *path per parameter combination*, and to pass the right DataFrame you would have to reconstruct the source's `task_id` → path mapping yourself for every parameter set, and keep it in sync as parameters change. That is reimplementing oryxflow's parameter management by hand, in glue code.

`attach_flow` avoids all of this. Because you attach the flow **object**, not its data, the "ask by parameters, not paths" property survives across the flow boundary — the attached flow resolves its own output paths from its own parameters:

```python
self.flows['scraped'].outputLoad(Task2)                 # single flow: its parameters
self.flows['scraped'].outputLoad(Task2, flow='2024')    # WorkflowMulti: pick a parameter set by name
```

You never touch a path, parameterized or not, cross-project or not.

```python
class Task1(oryxflow.tasks.TaskCachePandas):
    def run(self):
        self.save(pd.DataFrame({'a': [1, 2]}))

class Task3(oryxflow.tasks.TaskCachePandas):
    def run(self):
        # read the attached flow's default-task output
        temp_flow_df = self.flows['flow'].outputLoad()
        self.save(temp_flow_df)

# Define both flows and run the source flow first so its output exists
flow = oryxflow.Workflow(Task1)
flow2 = oryxflow.Workflow(Task3)
flow.run()

# Attach the first flow to the second under the name 'flow', THEN run
# (attachment is propagated to the tasks when flow2.run() executes)
flow2.attach_flow(flow, 'flow')
flow2.run()

df = flow2.outputLoad()   # Task3's output, sourced from the attached flow
```

Step by step, here is what actually happens:

1.  `flow.run()` executes `Task1` and saves its output (here into the in-memory cache, because `Task1` is a `TaskCachePandas`). At this point `flow` is a live `Workflow` object that knows how to load that output — `flow.outputLoad()` would return the DataFrame.
2.  `flow2.attach_flow(flow, 'flow')` records the whole `flow` *object* — not its data — under the key `'flow'` on `flow2`. Nothing runs yet; you are just registering that `flow2` may need to reach into `flow` later.
3.  `flow2.run()` propagates that registration onto the task instances just before executing them: every task in `flow2` gets `self.flows = {'flow': flow}`. This is why the attach must come *before* the run — a task created and run without it would have `self.flows` empty.
4.  Inside `Task3.run()`, `self.flows['flow']` is the attached `flow` object, so `self.flows['flow'].outputLoad()` calls `outputLoad()` on it and returns `Task1`'s DataFrame. `Task3` then saves it as its own output.

The key idea is that the link is between *flows*, resolved lazily at load time — `Task3` never declares `Task1` in its `requires()`. That keeps the two flows independent (separate projects, separate `data/` directories, separate run/reset scopes) while still letting one read the other's results. Crucially, unlike a `requires` edge, the attached flow **keeps its own** `path`/`env`, so `self.flows['flow'].outputLoad()` reads from *that* flow's data root — which may be a different project's `data/` or a different `env=`. For reading another *project's* outputs, prefer `FlowImport` and a plain `@requires` (see [Reading another project's outputs](#reading-another-projects-outputs-flowimport)): it is tracked, and reruns follow upstream. `attach_flow` fits when you want a whole flow configured once — a fixed `env=` or parameter slice — and read on demand:

```python
# another project's flow, configured once: its prod environment
flow_prod = oryxflow.Workflow(exported.Task2, path='../another-project/data', env='prod')

# your flow whose task consumes the scraped data
flow_mine = oryxflow.Workflow(MyTask)
flow_mine.attach_flow(flow_prod, 'scraped')
flow_mine.run()          # inside MyTask.run(): self.flows['scraped'].outputLoad(Task2)
```

Two trade-offs to know:

- **Not tracked as a dependency.** oryxflow will not build the source flow for you: if the output doesn't exist yet, `self.flows['flow'].outputLoad()` raises rather than running it, so you must run the source flow first (as `flow.run()` does above).
- **Attached at a fixed parameter slice.** `attach_flow` hands you the source flow configured at attach time — clean when the consumer wants a *fixed* slice ("the prod-2024 scrape"). If instead you want *each instance* of a parameterized consumer to automatically pull the **matching** parameter set from the source (consumer `country=US` → source `country=US`), that per-instance coupling is what a real `@oryxflow.requires` edge does best — parameters propagate upstream automatically and it is tracked and auto-built.

Decision rule:

- **Same project, and the upstream should follow the consumer's parameters** → `@oryxflow.requires` (parameters propagate, tracked, auto-built). Don't reach for `attach_flow` to wire tasks that belong to *one* pipeline.
- **Another project's outputs** → `FlowImport` its exported tasks, then `@oryxflow.requires` (tracked; reruns follow upstream).
- **A fixed slice of an independently-managed flow (a set `env=` or parameter set), read on demand** → `attach_flow`, which keeps parameter → path resolution across the boundary — the thing manual DataFrame passing destroys.
