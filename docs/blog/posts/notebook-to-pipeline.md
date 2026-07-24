---
date: 2026-07-23
slug: notebook-to-reproducible-pipeline
categories:
  - Reproducibility
description: A step-by-step guide to converting a linear Python notebook into a reproducible, cached pipeline with oryxflow — incrementally, without a rewrite.
faq:
  - q: "How do I convert a Jupyter notebook into a reproducible pipeline?"
    a: "Migrate incrementally rather than rewriting. Wrap your slowest step — usually the data load — in an oryxflow task that ends with self.save(), run it once so the result caches, then feed the rest of your notebook from that cached output. Add one step at a time with @oryxflow.requires to wire dependencies. You keep a working pipeline at every stage, and each output is tied to the exact code and parameters that produced it."
  - q: "Do I have to rewrite my whole notebook at once to use oryxflow?"
    a: "No — that's the point of migrating incrementally. Start by converting the single step that hurts most, usually the slow load, and leave the rest of your cells untouched, now fed by flow.outputLoad(). You have a working pipeline after every step, and you only pull the next step into a task when it earns it. oryxflow is designed for this one-step-at-a-time conversion, not a big-bang rewrite."
  - q: "Why do I get different numbers when I rerun a notebook a week later?"
    a: "Usually it's hidden state, out-of-order cell execution, or stale intermediate results that never refreshed after an upstream change — none of which surface as errors, they just quietly make your output wrong. oryxflow removes all three by declaring each step as a task with explicit dependencies: the engine runs them in the right order, caches each output under an id derived from its code and parameters, and reruns a step only when its code or inputs actually change."
---

# From notebook to a reproducible, cached pipeline in Python

*Turn a working-but-fragile notebook into a pipeline you can trust — one step at a time, without a big rewrite.*

<!-- more -->

A notebook is the best place to start an analysis and the worst place to keep one.

While you are exploring, cells run out of order, variables linger in memory long after the cell that defined them was edited, and half your intermediate results are stale copies from an hour ago. It all still works — until you reopen the notebook a week later, hit "Run All," and get a different number. Now you are stuck asking the question every data scientist eventually asks: *which version of the code and which data actually produced this result?* If you cannot answer that, you cannot trust the result, and you cannot reproduce it for anyone else.

The usual culprits are hidden state (a variable that only exists because you ran a cell you have since deleted), out-of-order execution, and stale intermediates that never got refreshed after an upstream change. None of these show up as errors. They just quietly make your output wrong.

**oryxflow is a small Python library that lets you declare each step of your analysis as a task — with its parameters and its dependencies — so the library runs them in the right order, caches each output, and reruns a step only when its code or inputs actually change.** The payoff is that every result is tied to the exact code and parameters that produced it. That is reproducibility you get for free, not reproducibility you have to remember to maintain.

The good news: you do not rewrite your notebook to get there. You migrate it one step at a time, and you have a working pipeline after every single step.

## Start with the linear script

Here is a typical notebook, flattened into the script it really is: load data, build features, train a model.

```python
import pandas as pd
from sklearn.linear_model import LinearRegression

df = pd.read_parquet('raw.parquet')          # slow: pulls a big table

df['x_squared'] = df['x'] ** 2               # feature engineering
features = df[['x', 'x_squared']]

model = LinearRegression().fit(features, df['y'])
```

Every time you tweak the model, that first line pulls the whole table again. And nothing records that *this* model came from *that* data and *that* feature code.

## Convert the first expensive step into a task

Do not convert everything. Start with the one step that hurts most — usually the slow load. Wrap it in a task class, and replace the return with `self.save(...)`.

```python
import oryxflow
import pandas as pd

oryxflow.set_dir('data/')                     # where cached outputs live

class GetData(oryxflow.tasks.TaskPqPandas):   # TaskPqPandas = saves a DataFrame as parquet
    def run(self):
        df = pd.read_parquet('raw.parquet')
        self.save(df)
```

Run it:

```python
flow = oryxflow.Workflow(GetData)
flow.run()
df = flow.outputLoad()                         # the saved DataFrame, back in your hands
```

The first `flow.run()` does the slow load and caches the result. Every run after that is a cache hit — it loads from disk instead of re-fetching. Your remaining notebook cells keep working, now fed by `df = flow.outputLoad()`. You have already gained something and rewritten almost nothing.

## Wire the next step with @oryxflow.requires

Now pull feature engineering into its own task. The `@oryxflow.requires(GetData)` decorator declares the dependency; inside `run`, `self.inputLoad()` hands you `GetData`'s already-loaded output.

```python
@oryxflow.requires(GetData)
class BuildFeatures(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()                  # GetData's output, already loaded
        df['x_squared'] = df['x'] ** 2
        self.save(df[['x', 'x_squared', 'y']])
```

You never call `GetData` yourself. You ask for `BuildFeatures`, and oryxflow runs its dependencies first, in order — no more "did I run the cells in the right sequence?"

## Finish the DAG

Add the model as a final task. Models are not DataFrames, so save them with `TaskPickle`. This is also where parameters earn their keep: expose the knobs you actually sweep.

```python
from sklearn.linear_model import LinearRegression

@oryxflow.requires(BuildFeatures)
class TrainModel(oryxflow.tasks.TaskPickle):   # TaskPickle = pickle, for models & arbitrary objects
    power = oryxflow.IntParameter(default=2)
    fit_intercept = oryxflow.BoolParameter(default=True)

    def run(self):
        df = self.inputLoad()
        X, y = df[['x', 'x_squared']], df['y']
        model = LinearRegression(fit_intercept=self.fit_intercept).fit(X, y)
        self.save(model)
```

That is the whole pipeline: `GetData → BuildFeatures → TrainModel`. Preview it before running anything:

```python
flow = oryxflow.Workflow(TrainModel)
flow.preview()                                 # prints the task tree, runs nothing
flow.run()
model = flow.outputLoad()
```

`preview()` shows you the execution plan — which steps will run and which are already cached — without touching your data. When you are ready, `run()` executes only what is needed.

## What you actually gain

**Reproducibility by construction.** Each task's output is stored under an id derived from its code and its parameters. Change the feature formula, and oryxflow knows `BuildFeatures` is now different from the version that produced the cached file. "Which data made this result?" stops being a detective problem.

**No wasted recompute.** Rerun the pipeline and nothing happens if the outputs already exist — cache hits all the way down. Edit `BuildFeatures`, though, and oryxflow reruns it *and* `TrainModel` automatically, because the model depends on features that just changed. **Code invalidation is automatic** — there is no cache to manually clear and no `reset` to remember. This is the same mechanism the sibling post [Stop rerunning your whole pipeline](stop-rerunning-your-pipeline.md) digs into.

**Load any result by name.** Every intermediate is addressable. Want the features without rerunning the model? `oryxflow.Workflow(BuildFeatures).outputLoad()` hands back the cached frame. Comparing two settings? `oryxflow.Workflow(TrainModel, {'power': 3}).outputLoad()` loads that configuration's model directly. No more scrolling for the cell that defined the variable you need.

## When not to bother

Throwaway exploration should stay a notebook. If you are poking at a new dataset for twenty minutes and will not run any of it twice, the task scaffolding is pure overhead — reach for oryxflow when a computation is slow enough to want caching, or a result is important enough to need reproducing. It is built for the research loop, not for production orchestration; if you need scheduling, retries, and alerting across a fleet, use a real orchestrator. oryxflow makes *your own analysis* trustworthy and fast to iterate on.

A good rule of thumb: migrate a step the second time you find yourself waiting for it to recompute something that did not change.

## Takeaway

- Migrate incrementally. Wrap the most expensive step first, keep a working pipeline at every stage, and never do a big-bang rewrite.
- `@oryxflow.requires` wires dependencies; `self.inputLoad()` reads them; `self.save()` caches the output.
- Editing a task reruns it and everything downstream automatically — reproducibility you do not have to maintain by hand.

```bash
pip install oryxflow
```

If you use Claude Code, the oryxflow plugin's `/oryxflow:migrate` command walks a script through exactly this conversion, one step at a time.

## Frequently asked questions

### How do I convert a Jupyter notebook into a reproducible pipeline?

Migrate incrementally rather than rewriting. Wrap your slowest step — usually the data load — in an oryxflow task that ends with `self.save()`, run it once so the result caches, then feed the rest of your notebook from that cached output. Add one step at a time with `@oryxflow.requires` to wire dependencies. You keep a working pipeline at every stage, and each output is tied to the exact code and parameters that produced it.

### Do I have to rewrite my whole notebook at once to use oryxflow?

No — that's the point of migrating incrementally. Start by converting the single step that hurts most, usually the slow load, and leave the rest of your cells untouched, now fed by `flow.outputLoad()`. You have a working pipeline after every step, and you only pull the next step into a task when it earns it. oryxflow is designed for this one-step-at-a-time conversion, not a big-bang rewrite.

### Why do I get different numbers when I rerun a notebook a week later?

Usually it's hidden state, out-of-order cell execution, or stale intermediate results that never refreshed after an upstream change — none of which surface as errors, they just quietly make your output wrong. oryxflow removes all three by declaring each step as a task with explicit dependencies: the engine runs them in the right order, caches each output under an id derived from its code and parameters, and reruns a step only when its code or inputs actually change.

Next steps: [Why oryxflow](../../docs/why-oryxflow.md) · [Transition guide](../../docs/transition.md) · [Quickstart](../../docs/quickstart.md) · [Claude Code plugin](../../docs/claude-plugin/index.md) · [GitHub](https://github.com/oryxintel/oryxflow)
