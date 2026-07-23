# Advanced: Dynamic Tasks

Sometimes you might not know exactly what other tasks to depend on until runtime. There are several cases of dynamic dependencies.

## Fixed Dynamic

If you have a fixed set parameters, you can make <span class="title-ref">requires()</span> "dynamic".

```python
# cfg_params.py -- the enumeration is your own domain data, kept in a config module
PARAMS = ['a', 'b', 'c']

class TaskInput(oryxflow.tasks.TaskPqPandas):
    param = oryxflow.Parameter()
    ...

class TaskYieldFixed(oryxflow.tasks.TaskPqPandas):

    def requires(self):
        return {s: TaskInput(param=s) for s in cfg_params.PARAMS}

    def run(self):
        df = self.inputLoad()
        df = pd.concat(df)
        self.save(df)
```

You could also use this to load an unknown number of files as a starting point for the workflow.

```python
def requires(self):
    return {s: TaskInput(param=s) for s in glob.glob('*.csv')}
```

## Hierarchical iterate-and-aggregate

!!! note

    This section is the mechanics reference. For *why* and *when* you'd reach for this pattern — caching expensive granular work and resetting it selectively as you iterate — start with [Managing Complex Workflows](managing-workflows.md).

A common pattern is to iterate over some dimension (e.g. per-state tasks), then aggregate the results one level up (e.g. a per-country task that combines all of its states). Do this with a **native DAG aggregator**: the aggregating task's `requires()` returns a dict of the per-item task instances, and `run()` stacks them with `self.inputLoadConcat()`. Each dependency's significant params are added as columns automatically, so your groupby keys (state, country) survive the concat.

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

    def requires(self):
        return {s: ProcessState(country=self.country, state=s) for s in STATES[self.country]}

    def run(self):
        self.save(self.inputLoadConcat())      # stacks states, keeps state/country cols
```

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

    def requires(self):
        return {s: ProcessState(sector=self.sector, country=self.country, state=s)
                for s in cfg.UNIVERSE[self.sector][self.country]}

    def run(self):
        self.save(self.inputLoadConcat())

class Sector(oryxflow.tasks.TaskPqPandas):           # aggregate countries within a sector
    sector = oryxflow.Parameter()

    def requires(self):
        return {c: Country(sector=self.sector, country=c) for c in cfg.UNIVERSE[self.sector]}

    def run(self):
        self.save(self.inputLoadConcat())
```

Each level's `inputLoadConcat()` tags frames with that level's dependency params, so the `Sector` step re-writes the `sector`/`country` columns the `Country` frames already carry — an idempotent overwrite with the same values, so there is no double-counting. If a lower level's tag column ever needs different handling, use `tagkeys=` (tag only these params), `tag=False` (tag nothing), or `concat_fn=` (full control) on `inputLoadConcat`.

### Fan-out vs. independent runs (do you need WorkflowMulti?)

There is really only **one** mechanism here — fan-out via `requires()` over your enumeration. The outer `sector` dimension is just more fan-out, so you have a choice for how to drive the top:

**One DAG (no WorkflowMulti).** Add one more aggregator on top and fan out over sectors too. The whole three-level tree is a single `build()` — one run, one combined output, one reset scope:

```python
class AllSectors(oryxflow.tasks.TaskPqPandas):

    def requires(self):
        return {sec: Sector(sector=sec) for sec in cfg.UNIVERSE}

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

## Collector Task

If you want to spawn multiple tasks without processing any of the outputs, you can use <span class="title-ref">TaskAggregator</span>. This task should do nothing but yield other tasks.

```python
@oryxflow.requires(TrainModel1,TrainModel2) # inherit all params from input tasks
class TrainAllModels(oryxflow.tasks.TaskAggregator):

    def run(self):
        yield self.clone(TrainModel1)
        yield self.clone(TrainModel2)
```

Alternatively, you can achieve the same using the <span class="title-ref">WorkflowMulti</span> object with additional flexibility.

```python
params = dict()
params_all = oryxflow.utils.params_generator_single({'param':['a','b']},params)

flow = oryxflow.WorkflowMulti(tasks_search.SearchModelTrain, params=params_all)
flow.run()
```

If you want to run the workflow with multiple parameters at the same time, you can use <span class="title-ref">TaskAggregator</span> to yield multiple tasks.

```python
class TaskAggregator(oryxflow.tasks.TaskAggregator):

    def run(self):
        yield TaskTrain(do_preprocess=False)
        yield TaskTrain(do_preprocess=True)
```

## Fully Dynamic

This doesn't work yet, and it's actually quite rare that you need it. Parameters normally fall in a fixed range which can be solved with the approaches above. Another typical reason you would want to do this is to load an unknown number of input files which you can do manually, see "Load External Files" in [tasks](tasks.md).

```python
class TaskA(oryxflow.tasks.TaskCache):
    param = oryxflow.IntParameter()
    def run(self):
        self.save(self.param)

class TaskB(oryxflow.tasks.TaskCache):
    param = oryxflow.IntParameter()

    def requires(self):
        return TaskA()

    def run(self):
        value = 1
        df_train = self.input(param=value).load()
```
