# Quickstart

oryxflow turns a data-science script into a pipeline of **tasks**. You declare each step as a task — what it *depends on* and what it *produces* — and the engine runs them in the right order, skips anything already computed, and lets you load any result by name. No manual file paths, no re-running the slow steps to test a fast one.

This page gets you from nothing to a running pipeline. For installation, follow the [GitHub instructions](https://github.com/oryxintel/oryxflow#installation) (`pip install oryxflow`).

## The idea in one minute

A **task** is one step of your analysis, written as a class:

- it inherits from a task type that decides the output format (parquet, pickle, in-memory, …), so you never write save/load code;
- `run()` does the work and calls `self.save(...)` to store the result;
- `@oryxflow.requires(...)` declares which other task(s) it depends on;
- inside `run()`, `self.inputLoad()` hands you the dependency's output, already loaded.

You then wrap the final task in a `Workflow` and call `run()`. The engine walks the dependencies, runs only what's missing, and caches every output.

## Your first pipeline

Two tasks: one produces data, the next transforms it.

<!--phmdoctest-share-names-->
```python
import oryxflow
import pandas as pd

oryxflow.set_dir('data/')                       # where task outputs are cached

class GetData(oryxflow.tasks.TaskPqPandas):     # output saved as parquet
    def run(self):
        df = pd.DataFrame({'x': range(10)})
        self.save(df)                           # cache this task's output

@oryxflow.requires(GetData)                     # declare the dependency on GetData
class ProcessData(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()                   # GetData's output, already loaded
        df['x2'] = df['x'] ** 2
        self.save(df)
```

Run it by asking for the *final* task — upstream dependencies run automatically:

<!--phmdoctest-share-names-->
```python
flow = oryxflow.Workflow(ProcessData)

flow.preview()          # show what will run, without running it
flow.run()              # runs GetData, then ProcessData

df = flow.outputLoad()  # load ProcessData's result by referencing the flow
print(df.head())
```

Run `flow.run()` **again** and nothing happens — both outputs already exist, so the engine skips them. That is the core payoff: re-running a pipeline only pays for what actually changed.

## Change a parameter, rerun only what depends on it

Parameters are how you try different settings without renaming files by hand. Give a task a parameter and oryxflow caches a **separate output per value**, so you can switch between them instantly, and changing one reruns exactly the tasks that depend on it.

<!--phmdoctest-share-names-->
```python
@oryxflow.requires(GetData)
class ProcessData(oryxflow.tasks.TaskPqPandas):
    power = oryxflow.IntParameter(default=2)    # a knob to experiment with
    def run(self):
        df = self.inputLoad()
        df['x_pow'] = df['x'] ** self.power
        self.save(df)

# run with a non-default parameter
flow = oryxflow.Workflow(ProcessData, {'power': 3})
flow.run()              # GetData is already complete and is skipped; only ProcessData runs
df = flow.outputLoad()
```

`GetData` doesn't re-run — it has no dependence on `power`, so its cached output is reused. Only `ProcessData` recomputes. (Edit a task's *code* and it reruns automatically too; see [Automatic code invalidation](managing-workflows.md#automatic-code-invalidation).)

## A realistic ML workflow

The same three ideas — depend, produce, load by name — scale to a real pipeline: get data, preprocess it, train a model, and compare two models. This needs `scikit-learn` installed.

<!--phmdoctest-share-names-->
```python
import oryxflow
import pandas as pd
import sklearn.datasets, sklearn.preprocessing
import sklearn.linear_model, sklearn.ensemble

oryxflow.set_dir('data/')

class GetDiabetes(oryxflow.tasks.TaskPqPandas):
    def run(self):
        ds = sklearn.datasets.load_diabetes()
        df = pd.DataFrame(ds.data, columns=ds.feature_names)
        df['y'] = ds.target
        self.save(df)

@oryxflow.requires(GetDiabetes)                 # inherits GetDiabetes's params, wires the dependency
class ModelData(oryxflow.tasks.TaskPqPandas):
    do_preprocess = oryxflow.BoolParameter(default=True)   # preprocessing on/off
    def run(self):
        df = self.inputLoad()
        if self.do_preprocess:
            df.iloc[:, :-1] = sklearn.preprocessing.scale(df.iloc[:, :-1])
        self.save(df)

@oryxflow.requires(ModelData)                   # parameters flow upstream automatically
class ModelTrain(oryxflow.tasks.TaskPickle):    # a model object → saved as pickle
    model = oryxflow.Parameter(default='ols')   # which model to train
    def run(self):
        df = self.inputLoad()
        X, y = df.drop(columns='y'), df['y']
        if self.model == 'ols':
            m = sklearn.linear_model.LinearRegression()
        elif self.model == 'gbm':
            m = sklearn.ensemble.GradientBoostingRegressor()
        else:
            raise ValueError('invalid model selection')
        m.fit(X, y)
        self.save(m)
        self.saveMeta({'score': m.score(X, y)})   # save a small metadata sidecar
```

Compare two models by running both as separate *flows*. `WorkflowMulti` maps a name to each flow's parameters:

<!--phmdoctest-share-names-->
```python
flow = oryxflow.WorkflowMulti(ModelTrain, {
    'ols': {'do_preprocess': True,  'model': 'ols'},
    'gbm': {'do_preprocess': False, 'model': 'gbm'},
})
flow.run()      # GetDiabetes runs once and is shared — the 'gbm' flow reuses it, it doesn't refetch

print(flow.outputLoadMeta())          # scores from the metadata sidecars
# {'ols': {'score': 0.52}, 'gbm': {'score': 0.80}}

models = flow.outputLoad(ModelTrain)  # {'ols': <fitted model>, 'gbm': <fitted model>}
```

Notice that `GetDiabetes` runs only once even though two models are trained — the engine sees its output is already complete and shares it across both flows. That is the whole point: expensive, shared steps are computed once and reused.

!!! tip

    The fastest way to a fully-structured project is the Claude Code plugin: it sets up the project layout and wires tasks for you. See [Build with Claude Code](claude-plugin/index.md).

## Next steps

- [Transition to oryxflow](transition.md) — turn an existing script into tasks.
- [Writing and Managing Tasks](tasks.md) — dependencies, inputs, outputs, and save formats.
- [Advanced: Parameters](advparam.md) — parameter inheritance and how it drives reruns.
- [Running Workflows](run.md) — previewing, running, and resetting tasks.
- [Managing Complex Workflows](managing-workflows.md#managing-complex-workflows) — caching expensive granular work and resetting it selectively as you iterate.

An interactive version of the ML example is on [mybinder](http://tiny.cc/d6tflow-start-interactive), and a real-life project template is at <https://github.com/d6t/d6tflow-template>.
