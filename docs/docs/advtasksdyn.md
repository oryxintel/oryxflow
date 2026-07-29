# Advanced: Dynamic Workflow Generation

You don't have to write out every task by hand. When the shape of the work comes from a list — one task per region, per file, per model — you write the list once and let oryxflow generate the tasks from it. Everything on this page builds on that one idea.

## One task per item in a list

Instead of naming each dependency, point a task at a list and oryxflow declares one dependency per item in it — a workflow that grows and shrinks with your data instead of with your typing.

```python
# cfg.py -- the list is your own domain data, kept in a config module
REGIONS = ['north', 'south', 'east']

class RegionLoad(oryxflow.tasks.TaskPqPandas):
    region = oryxflow.Parameter()

    def run(self):
        self.save(fetch_raw(self.region))

@oryxflow.requires_each(RegionLoad, region=cfg.REGIONS)
class RegionCombine(oryxflow.tasks.TaskPqPandas):

    def run(self):
        self.save(self.inputLoadConcat())      # stacks the regions, tags each row with its region
```

`@oryxflow.requires_each` declares one dependency *per value* — one `RegionLoad` per region — and `self.inputLoadConcat()` stacks their outputs into one DataFrame, adding a `region` column so you can still group by it. Add a fourth region to `cfg.REGIONS` and only that one runs; the other three are already cached.

!!! warning "Declare the fan-out — don't loop inside `run()`"

    You can get the same numbers by looping over the regions inside `run()` and starting a flow for each one. The reason not to is what happens the next time you change something.

    Tasks started inside a `run()` are not dependencies, so nothing can find them. `flow.reset_upstream(RegionCombine, only=RegionLoad)` reports no error and invalidates nothing. You change how a region is loaded, reset "just that step", re-run — and get the old numbers back, with a green run and no warning. Declared as a fan-out, every reset reaches every branch, `preview()` counts them before you start, and the run summary tells you how many actually ran. Same reasoning, worked through, in [Avoid building a flow inside a task](advparam.md#avoid-building-a-flow-inside-a-task).

The list doesn't have to be config — anything that produces one works. A common starting point for a workflow is a folder of input files you didn't write down anywhere:

```python
import glob

class LoadFile(oryxflow.tasks.TaskPqPandas):
    file = oryxflow.Parameter()

    def run(self):
        self.save(pd.read_csv(self.file))

@oryxflow.requires_each(LoadFile, file=glob.glob('data-raw/*.csv'))
class AllFiles(oryxflow.tasks.TaskPqPandas):

    def run(self):
        self.save(self.inputLoadConcat())      # every file, in one frame, tagged by filename
```

Each file becomes its own task, so each one's parsed output is cached separately: drop a new CSV into the folder, run again, and only that file is read. The folder is listed when your tasks are defined, so new files are picked up the next time you run your script.

!!! note

    Call the parameter `file` (or `filename`) rather than `path`. `path` means something else to a workflow — it's how you point a flow at a different data directory — so a parameter of that name won't behave the way you expect.

That is the whole mechanism. The rest of this page is what you can build with it: aggregating level by level, comparing model variants, and grouping tasks that don't combine at all.

## Hierarchical iterate-and-aggregate

!!! note

    This section is the mechanics reference. For *why* and *when* you'd reach for this pattern — caching expensive granular work and resetting it selectively as you iterate — start with [Managing Complex Workflows](managing-workflows.md).

The section above generates one task per item. Stack that idea and you get a hierarchy: a common pattern is to iterate over some dimension (e.g. per-state tasks), then aggregate the results one level up (e.g. a per-country task that combines all of its states). Do this with a **native DAG aggregator**: the aggregating task's `requires()` returns a dict of the per-item task instances, and `run()` stacks them with `self.inputLoadConcat()`. Each dependency's significant params are added as columns automatically, so your groupby keys (state, country) survive the concat.

```python
STATES = {'US': ['CT', 'NY'], 'UK': ['London', 'Belfast']}

class DataLoadState(oryxflow.tasks.TaskPqPandas):
    country = oryxflow.Parameter()
    state = oryxflow.Parameter()

    def run(self):
        self.save(fetch_raw(self.country, self.state))

@oryxflow.requires(DataLoadState)              # copies country+state params, wires requires()
class ProcessState(oryxflow.tasks.TaskPqPandas):

    def run(self):
        df = self.inputLoad()                  # raw data for this state
        df['value_norm'] = df['value'] / df['value'].sum()   # per-state feature engineering
        self.save(df)

class Country(oryxflow.tasks.TaskPqPandas):
    country = oryxflow.Parameter()

    def requires(self):                        # the list depends on this task's own country
        return self.requires_grid(ProcessState, state=STATES[self.country])

    def run(self):
        self.save(self.inputLoadConcat())      # stacks states, keeps state/country cols
```

`requires_grid` is the form to use when the list is computed from the task's own parameters (here `STATES[self.country]`); `@oryxflow.requires_each` is the shorthand for a fixed list. Either way `country` is carried down to every `ProcessState` without being listed.

Because the whole hierarchy is now **one DAG in one** `run()` call (rather than a nested flow-within-a-flow built inside `run()`), you get three wins for free:

- `oryxflow.Workflow(Country, {'country': 'US'}).preview()` shows every per-state task in the tree.
- The run summary lists the per-state tasks (they land in the same `RunResult`).
- Central reset cascades along `requires()` edges — `reset_upstream`/`reset_downstream` reach every `DataLoadState`/`ProcessState` instance, no hand-tracking inside the task.

To reset just one family everywhere it appears in the DAG (every state/country), pass `only=`:

```python
flow = oryxflow.WorkflowMulti(Country, params={'country': list(STATES)})
flow.run()
flow.reset_upstream(Country, only=DataLoadState)   # only DataLoadState instances everywhere
flow.run()                                         # ProcessState/Country auto-recompute
# flow.reset_upstream(Country)                     # or reset the whole upstream (no `only=`)
```

The `only=` filter enumerates every `DataLoadState` (`US/CT`, `US/NY`, `UK/London`, `UK/Belfast`) via the DAG — no hand-listing. Since `check_dependencies` makes `complete()` recursive, invalidating just `DataLoadState` forces `ProcessState`/`Country` to recompute on the next run.

### The enumeration is your own data, not a oryxflow object

`STATES` above is **plain domain data** describing the hierarchy's shape — keep it in a `cfg.py`. Your `requires()` methods just index into it to decide how many children to depend on; that fan-out is the *only* thing that builds the DAG. Name it for what it holds (`STATES`, `STATES_BY_COUNTRY`) — avoid `grid`, which invites confusion with the unrelated `WorkflowMulti` params (covered below and in [Constructing the params grid](workflow.md#constructing-the-params-grid)).

### Nesting further (multi-level)

The pattern composes to any depth: each aggregating level is another task whose `requires()` returns a dict of the level below and whose `run()` calls `self.inputLoadConcat()`. For a sector → country → state hierarchy, add a `Sector` task that aggregates countries on top of the `Country` task that aggregates states:

```python
# cfg.py — plain domain config (nested enumeration), NOT a oryxflow object
UNIVERSE = {
    'Retail': {'US': ['CT', 'NY'], 'UK': ['London']},
    'Office': {'US': ['CA']},
}

class DataLoadState(oryxflow.tasks.TaskPqPandas):
    sector = oryxflow.Parameter()
    country = oryxflow.Parameter()
    state = oryxflow.Parameter()

    def run(self):
        self.save(fetch_raw(self.sector, self.country, self.state))

@oryxflow.requires(DataLoadState)
class ProcessState(oryxflow.tasks.TaskPqPandas):

    def run(self):
        df = self.inputLoad()                  # raw data for this state
        df['value_norm'] = df['value'] / df['value'].sum()   # per-state feature engineering
        self.save(df)

class Country(oryxflow.tasks.TaskPqPandas):          # aggregate states within a country
    sector = oryxflow.Parameter()
    country = oryxflow.Parameter()

    def requires(self):                              # sector+country carried down for you
        return self.requires_grid(ProcessState, state=cfg.UNIVERSE[self.sector][self.country])

    def run(self):
        self.save(self.inputLoadConcat())

class Sector(oryxflow.tasks.TaskPqPandas):           # aggregate countries within a sector
    sector = oryxflow.Parameter()

    def requires(self):
        return self.requires_grid(Country, country=list(cfg.UNIVERSE[self.sector]))

    def run(self):
        self.save(self.inputLoadConcat())
```

Each level's `inputLoadConcat()` tags frames with that level's dependency params, so the `Sector` step re-writes the `sector`/`country` columns the `Country` frames already carry — an idempotent overwrite with the same values, so there is no double-counting. If a lower level's tag column ever needs different handling, use `tagkeys=` (tag only these params), `tag=False` (tag nothing), or `concat_fn=` (full control) on `inputLoadConcat`.

### Fan-out vs. independent runs (do you need WorkflowMulti?)

There is really only **one** mechanism here — fan-out via `requires()` over your enumeration. The outer `sector` dimension is just more fan-out, so you have a choice for how to drive the top:

**One DAG (no WorkflowMulti).** Add one more aggregator on top and fan out over sectors too. The whole three-level tree is a single `build()` — one run, one combined output, one reset scope:

```python
@oryxflow.requires_each(Sector, sector=list(cfg.UNIVERSE))
class AllSectors(oryxflow.tasks.TaskPqPandas):

    def run(self):
        self.save(self.inputLoadConcat())

flow = oryxflow.Workflow(AllSectors)
flow.run()
dfall = flow.outputLoad()                                # sector/country/state columns present
flow.reset_upstream()                                    # resets every leaf across the tree
```

**Independent runs (WorkflowMulti).** Keep each sector as a *separate flow* — its own run summary, its own `outputLoad`, its own reset scope — when you want to manage sectors independently. Here the top-level `params` is a list of runs (see [Constructing the params grid](workflow.md#constructing-the-params-grid)), **not** part of DAG construction:

```python
flow = oryxflow.WorkflowMulti(Sector, params={'sector': list(cfg.UNIVERSE)})
flow.run()
dfall = flow.outputLoadConcat(Sector)                   # combine the per-sector flows
flow.reset_upstream(Sector, only=DataLoadState)         # reset one family, all sectors
```

Same result frame either way. Reach for fan-out (`AllSectors`) when you want one combined run; reach for `WorkflowMulti` when sectors are separately-managed experiments.

A complete, runnable version of this sector → country → state example — including the dev loop where you add a feature to the country-level task, iterate on one `(sector, country)` first, then roll it out to every flow *without re-fetching the expensive per-state source* — is in `docs/example-flow-multi.py`.

!!! tip

    These advanced dynamic-loop flows are exactly what the [Claude Code plugin](claude-plugin/index.md) is built to manage. Describe the hierarchy in plain language and it writes the fan-out `requires()` and the `inputLoadConcat()` aggregators; when you iterate, it scopes the reset for you — resetting just the family you changed (`reset_upstream(..., only=...)`) so the expensive leaf tasks are preserved. The hand-tracking this section warns about is what the plugin removes.

## Comparing model variants, then carrying on downstream

The same fan-out handles a very common modelling shape: run one expensive pipeline once per model
variant, combine the results into a single frame, and keep building on top of it. The shared data
prep happens **once**; only the parts that actually differ per model are repeated.

```python
MODELS = ['ridge', 'forest']          # your own domain config, e.g. in cfg.py

class DataLoad(oryxflow.tasks.TaskPqPandas):
    date_asof = oryxflow.Parameter(default='2026-06-30')

    def run(self):
        self.save(fetch_training_data(self.date_asof))

@oryxflow.requires(DataLoad)
class ModelTrain(oryxflow.tasks.TaskPqPandas):
    model = oryxflow.ChoiceParameter(default='ridge', choices=MODELS)

    def run(self):
        df = self.inputLoad()
        self.save(train_and_predict(df, self.model))     # columns: actual, predicted

@oryxflow.requires_each(ModelTrain, model=MODELS)   # one ModelTrain per model
class ModelCombine(oryxflow.tasks.TaskPqPandas):

    def run(self):
        self.save(self.inputLoadConcat())                # tags each row: model, date_asof

@oryxflow.requires(ModelCombine)
class ModelReport(oryxflow.tasks.TaskPqPandas):

    def run(self):
        df = self.inputLoad().assign(error=lambda d: (d['predicted'] - d['actual']).abs())
        self.save(df.groupby('model')['error'].mean().reset_index())

flow = oryxflow.Workflow(ModelReport, params={'date_asof': '2026-03-31'})
flow.run()
flow.outputLoad()          # one row per model, ranked by error
```

Three details make this work, and they're worth naming because they're the difference between this
and a script:

**`DataLoad` has no `model` parameter, so it is one task, not two.** Both `ModelTrain` branches ask
for the same data, get the same task, and the load runs once no matter how many models you compare.
Add a fifth model and the data is still loaded once.

**`ModelCombine` has no `model` parameter either.** `@oryxflow.requires_each` copies `ModelTrain`'s
parameters onto it *except* the one being fanned out, which is what turns the fan-out back into a
single node: it is the one place the branches meet. Everything downstream of it — `ModelReport`
here, and anything after that — goes back to the ordinary `@oryxflow.requires` decorator and never
has to know that two models were involved.

**`date_asof` is set once on the flow and reaches every task**, including the ones on the far side
of the fan-out. You only ever name the task you're fanning out over; which of *its* parameters came
from further upstream is not your problem. Preview shows the value arriving everywhere:

```text
+--[ModelReport-{'date_asof': '2026-03-31'} (PENDING)]
   +--[ModelCombine-{'date_asof': '2026-03-31'} (PENDING)]
      |--[ModelTrain-{'date_asof': '2026-03-31', 'model': 'ridge'} (PENDING)]
      |  +--[DataLoad-{'date_asof': '2026-03-31'} (PENDING)]
      +--[ModelTrain-{'date_asof': '2026-03-31', 'model': 'forest'} (PENDING)]
         +--[DataLoad-{'date_asof': '2026-03-31'} (PENDING)]
```

From there the loop is the one you'd want. Add a model by editing the `MODELS` line and re-running
your script (it is ordinary config, read when the tasks are defined):

```python
MODELS = ['ridge', 'forest', 'boosted']             # only the new branch runs

flow.reset_upstream(ModelReport, only=ModelTrain)   # retrain every model, keep the loaded data
flow.run()
```

Adding a model trains only that model — the ones you already ran are cached, and so is the data
load. Changing how you train retrains all of them without re-fetching the source. If a single
branch fails, the error names the parameters it was running with
(`ModelTrain(date_asof=2026-03-31, model=forest): ValueError: ...`) and `flow.preview()` shows each
branch, so you can see which ones are still pending.

### Fanning out over more than one thing

Name more parameters to get every combination of them — this is six branches:

```python
@oryxflow.requires_each(ModelTrain, model=MODELS, horizon=[1, 5, 20])
class ModelCombine(oryxflow.tasks.TaskPqPandas):

    def run(self):
        self.save(self.inputLoadConcat())
```

Each keyword takes the **list** of values to run for. Every branch is tagged with its own values in
the combined frame, so `df.groupby(['model', 'horizon'])` works straight away.

### Combining a fan-out with something shared

A combining task usually needs more than the branches. It needs the table the branches were built
from, or a benchmark to score them against, or the labels to render them with — one input, shared by
all of them, that is deliberately *not* fanned out. Stack the two decorators:

```python
@oryxflow.requires({'input': ReportInput})                     # the shared half
@oryxflow.requires_each(RegionNarrative, region=cfg.REGIONS)   # the fan-out half
class Report(oryxflow.tasks.TaskMarkdown):

    def run(self):
        deps = self.inputLoad(flatten=False)
        drivers = deps['input']                                # the shared input
        for region, narrative in deps['RegionNarrative'].items():
            ...                                                # every branch, grouped
        self.save(text)
```

Order doesn't matter, and you can stack as many as you like. The parameter rule still holds across
all of them: `Report` gets every parameter its dependencies have **except** `region`.

`inputLoad(flatten=False)` is what keeps `run()` readable — the branches arrive under one key
(the dependency's own name), so you never have to pop the inputs you recognise and assume the rest
are branches. `self.inputLoad(task='RegionNarrative')` gets just the branches, and
`self.inputLoadConcat(task='RegionNarrative')` stacks just those into one frame.

Why keep the drivers table out of the narratives at all? Because each narrative is an expensive LLM
call. Fold the table into them and changing how it's formatted re-bills every region; keep it
separate and the narratives stay cached.

If two fan-outs would produce the same keys — one per region of narratives *and* one per region of
charts — name one of them, and its keys carry the name:

```python
@oryxflow.requires_each({'chart': RegionChart}, region=cfg.REGIONS)
@oryxflow.requires_each(RegionNarrative, region=cfg.REGIONS)
class Report(oryxflow.tasks.TaskMarkdown):

    def run(self):
        deps = self.inputLoad(flatten=False)
        deps['RegionNarrative']['north']       # unnamed group: keyed by the name of the task
        deps['chart']['north']                 # named group
```

Without a name, colliding keys are an error rather than one dependency quietly replacing the other.

### When the list depends on the task's own parameters

Notice that `ModelCombine` above has no `requires()` method at all. `@oryxflow.requires_each` does
both of the jobs `@oryxflow.requires` does — it copies the dependency's parameters onto your task
(minus the ones being fanned out) *and* defines its `requires()` — so a combining task is usually
just a `run()` that calls `self.inputLoadConcat()`.

That works because the decorator reads its lists when your tasks are **defined**, which is what you
want for config like `MODELS`. When the values instead depend on the task's *own* parameters — the
regions in one country, the models approved for one date — pass a function and it is asked once
there is an instance to ask:

```python
@oryxflow.requires_each(RegionTrain, region=lambda self: cfg.REGIONS[self.country])
class CountryCombine(oryxflow.tasks.TaskPqPandas):
    country = oryxflow.Parameter()

    def run(self):
        self.save(self.inputLoadConcat())
```

The function sees the task's **parameters** — not its inputs, which don't exist until its
dependencies are known.

`requires_grid` is the same thing written out longhand, for when you want the dict in your own
`requires()`:

```python
class CountryCombine(oryxflow.tasks.TaskPqPandas):
    country = oryxflow.Parameter()

    def requires(self):
        return self.requires_grid(RegionTrain, region=cfg.REGIONS[self.country])

    def run(self):
        self.save(self.inputLoadConcat())
```

Either way every branch keeps the parameters the calling task already has, so `country` reaches each
`RegionTrain` without being listed. Writing the dict by hand instead is where it goes wrong — you
have to forward each shared parameter yourself, and remember to again every time you add one:

```python
def requires(self):
    return {r: RegionTrain(region=r, country=self.country) for r in cfg.REGIONS[self.country]}
```

Dict keys are the value itself for one parameter (`'ridge'`), or the combination for several
(`'horizon_5_model_ridge'`); those keys are what `self.inputLoad(task=...)` selects on when you want
one specific branch.

## Collector Task

To run several tasks together without combining their outputs, just pass them as a list — you don't need a task for that at all.

```python
flow = oryxflow.Workflow()
flow.run([TrainModel1, TrainModel2])
```

When something downstream needs to depend on the whole group, give the group a name with <span class="title-ref">TaskAggregator</span>. List its members with <span class="title-ref">@oryxflow.requires</span> and leave the body empty — the group saves nothing itself, and it's done when all of its members are done.

```python
@oryxflow.requires(TrainModel1,TrainModel2) # inherit all params from input tasks
class TrainAllModels(oryxflow.tasks.TaskAggregator):
    pass

flow = oryxflow.Workflow(TrainAllModels)
flow.preview()          # shows the group and every member below it
flow.run()
models = flow.outputLoad()   # one entry per member
```

A group task works like any other task: `preview()` shows what's still pending inside it, per-flow settings reach its members, and `flow.reset_upstream()` resets them.

To run the *same* task for many parameter combinations, use <span class="title-ref">WorkflowMulti</span> — you get one independently managed run per combination.

```python
params = dict()
params_all = oryxflow.utils.params_generator_single({'param':['a','b']},params)

flow = oryxflow.WorkflowMulti(tasks_search.SearchModelTrain, params=params_all)
flow.run()
```

## When the list itself depends on the data

Sometimes you don't know which items are *usable* until you've looked at the data — which regions have enough history to model, which files parsed cleanly. The workflow is built before any of that is known.

The answer is to **fan out over the full list and let the empty branches say so**. Keep the enumeration as plain domain data, declare a branch for every item, and have each branch check for its own data and save a placeholder when there isn't any:

```python
class RegionModel(oryxflow.tasks.TaskPqPandas):
    region = oryxflow.Parameter()

    def run(self):
        df = self.inputLoad()
        if len(df) < cfg.MIN_ROWS:
            self.save(pd.DataFrame())          # nothing to model here -- cheap, and cached
            return
        self.save(fit(df))

@oryxflow.requires_each(RegionModel, region=cfg.REGIONS)
class AllRegions(oryxflow.tasks.TaskPqPandas):

    def run(self):
        self.save(self.inputLoadConcat())      # empty frames contribute nothing
```

An empty branch costs almost nothing to run and is cached like any other, so the next run skips it. What you get in exchange is a workflow that stays visible: `preview()` lists every region including the ones that produced nothing, and a reset reaches all of them. A region that starts qualifying later is a re-run away, not a code change.

Two real limits are worth knowing:

- A function passed as a grid value (above) sees the task's **parameters**, not its inputs. It runs while the workflow is being assembled, before anything has been loaded.
- A `run()` can `yield` tasks and they will execute — but they're created mid-run, so `preview()` can't show them and a targeted reset can't find them. Reach for it only when nothing above fits.

To load an unknown number of input files, see "Load External Files" in [tasks](tasks.md).
