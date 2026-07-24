---
date: 2026-07-23
slug: parameter-sweeps-without-rerunning
categories:
  - Caching
description: Run parameter sweeps in Python that reuse cached upstream steps, so you can compare models and hyperparameters without recomputing the shared data every time.
faq:
  - q: "How do I run a parameter sweep without rerunning the upstream steps every time?"
    a: "Put the varying choice on a task parameter and let the engine key a separate cached output per value, while the shared upstream steps compute once and are reused across the whole grid. oryxflow does exactly this: changing a parameter reruns only the tasks that depend on it, so a sweep costs you the model fits and not the repeated data loading and feature building."
  - q: "How do I share cached features across a grid of model configs?"
    a: "Compute the features once as an upstream task, then have each model configuration depend on it so every config reads the same cached output instead of rebuilding it. oryxflow shares that upstream across every flow in a WorkflowMulti or fan-out grid, so all configs train on byte-for-byte identical inputs and only the differing model steps run — a trustworthy comparison with no filename bookkeeping."
---

# Parameter sweeps in Python without rerunning upstream steps

*Sweep a big grid cheaply, and trust the comparison because every config trained on the same cached inputs.*

<!-- more -->

## The problem: the grid multiplies the wrong work

Comparing models or hyperparameters means running a Cartesian product of configurations: three feature sets times four regularization strengths times two model families is twenty-four runs. That part is unavoidable — you asked for twenty-four answers.

What is avoidable is the work each run *repeats*. Naively, every configuration reloads the same raw data, recomputes the same features, and re-splits the same train/test folds before it gets to the one step that actually differs. If the data load takes two minutes and the feature step takes five, you have paid seven minutes twenty-four times to change a single number.

The usual workaround is worse than the problem. You start hand-managing output filenames — `model_ols_alpha01.pkl`, `model_gbm_alpha10.pkl` — and now you are one typo away from a collision that silently overwrites results, or from comparing two models that were quietly trained on different snapshots of the data. The bookkeeping becomes the experiment.

## What oryxflow is

oryxflow is a small Python library for building data-science workflows as `Task` classes that declare their dependencies and cache their outputs, so a step reruns only when its inputs or parameters actually change.

That caching model is exactly what a parameter sweep needs. Below are the two mechanisms it gives you, then the fan-out pattern that ties them into a grid.

## Mechanism 1: a parameter caches a separate output automatically

Add a parameter to a task and oryxflow keys the cache on its value. Each parameter value gets its own cached output — no filenames to invent, no collisions to worry about.

```python
import oryxflow
import pandas as pd

oryxflow.set_dir('data/')

class GetData(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = pd.read_csv('raw.csv')
        self.save(df)

@oryxflow.requires(GetData)
class TrainModel(oryxflow.tasks.TaskPickle):
    model = oryxflow.Parameter(default='ols')

    def run(self):
        df = self.inputLoad()
        estimator = fit(df, kind=self.model)
        score = evaluate(estimator, df)
        self.save(estimator)
        self.saveMeta({'score': score})
```

Run one configuration:

```python
flow = oryxflow.Workflow(TrainModel, {'model': 'gbm'})
flow.run()
```

`GetData` runs once and lands in the cache. Run `TrainModel` again with `model='ols'` and `GetData` is *not* recomputed — oryxflow sees its output already exists and reuses it. Changing a parameter reruns only the tasks that depend on that parameter. The `saveMeta` call stores a small metric sidecar next to the model so you can pull scores back without unpickling every estimator.

## Mechanism 2: WorkflowMulti compares named configurations

When you want to line up a handful of named configurations side by side, `WorkflowMulti` runs each as its own flow while sharing the upstream:

```python
flow = oryxflow.WorkflowMulti(TrainModel, {
    'ols': {'model': 'ols'},
    'gbm': {'model': 'gbm'},
})
flow.run()

print(flow.outputLoadMeta())        # {'ols': {'score': ...}, 'gbm': {'score': ...}}
models = flow.outputLoad(TrainModel) # {'ols': <model>, 'gbm': <model>}
```

The shared upstream — `GetData` — computes **once** and is reused across every flow. `outputLoadMeta()` collects the metric sidecars into one dict keyed by flow name, so a one-liner answers "which config won?" `outputLoad()` hands back the fitted models the same way.

## Mechanism 3: fan-out and aggregate for a full grid

For a sweep over many values, have one task fan out into one child task per value using a dict-`requires()`, then aggregate the results by stacking them:

```python
ALPHAS = [0.01, 0.1, 1.0, 10.0]

@oryxflow.requires(LoadData)
class TrainModel(oryxflow.tasks.TaskCachePandas):
    alpha = oryxflow.FloatParameter()

    def run(self):
        df = self.inputLoad()
        score = fit_and_score(df, alpha=self.alpha)
        self.save(pd.DataFrame({'alpha': [self.alpha], 'score': [score]}))

class Tune(oryxflow.tasks.TaskCachePandas):
    def requires(self):
        return {a: TrainModel(alpha=a) for a in ALPHAS}

    def run(self):
        df = self.inputLoadConcat()   # one row per alpha; 'alpha' column tags each
        self.save(df.sort_values('score', ascending=False))
```

`Tune` depends on one `TrainModel` per alpha. Each child caches its own one-row result. `inputLoadConcat()` row-stacks all of them into a single DataFrame, tagging each row with the parameters that produced it — so `Tune` ends up as a tidy leaderboard sorted by score. `LoadData` still runs once and feeds every child.

Run it and read off the winner:

```python
flow = oryxflow.Workflow(Tune)
flow.run()
leaderboard = flow.outputLoad()   # every alpha's score, best first
```

## Extend the grid, nothing wasted

Add two more values to the sweep:

```python
ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0]
```

Rerun `Tune`. The four alphas you already trained are still cached, so only the two new `TrainModel` tasks run. `LoadData` is untouched. The expensive shared step never re-executes just because the grid grew.

If you deliberately want to rerun the whole model family — say you changed the training code but not the data — you can invalidate just that band:

```python
flow.reset_upstream(Tune, only=TrainModel)
```

That reruns every `TrainModel` on the next `run()` while keeping `LoadData` cached. You re-fit the models, not the data.

## Why the comparison is trustworthy

Because the shared upstream is computed once and every configuration reads that same cached output, all twenty-four runs — or two, or two hundred — trained on byte-for-byte identical inputs. There is no chance that `ols` saw a slightly different split than `gbm`, because there is only one split to see. The comparison is apples-to-apples by construction, not by discipline.

And it is reproducible: the cache key is derived from parameters and upstream outputs, so rerunning the sweep on the same data reuses the same results rather than silently drifting. Delete `data/` and rebuild, and you get the same grid back.

One honest caveat: oryxflow is a research-loop tool for iterating quickly on your own machine, not a production orchestrator with scheduling, retries, or distributed workers. It is built to make the *next experiment* cheap, and the sequential engine runs the DAG in-process. For a hardened production pipeline, reach for a full orchestration system.

## Takeaway

A parameter sweep should cost you the model fits and nothing else. Put the varying choice on a parameter, let oryxflow cache one output per value, and share the expensive upstream across the whole grid. You get a cheap sweep, a trustworthy comparison, and no filename bookkeeping.

```bash
pip install oryxflow
```

## Frequently asked questions

### How do I run a parameter sweep without rerunning the upstream steps every time?

Put the varying choice on a task parameter and let the engine key a separate cached output per value, while the shared upstream steps compute once and are reused across the whole grid. oryxflow does exactly this: changing a parameter reruns only the tasks that depend on it, so a sweep costs you the model fits and not the repeated data loading and feature building.

### How do I share cached features across a grid of model configs?

Compute the features once as an upstream task, then have each model configuration depend on it so every config reads the same cached output instead of rebuilding it. oryxflow shares that upstream across every flow in a WorkflowMulti or fan-out grid, so all configs train on byte-for-byte identical inputs and only the differing model steps run — a trustworthy comparison with no filename bookkeeping.

Then keep going:

- [Managing complex workflows](../../docs/managing-workflows.md)
- [Why oryxflow](../../docs/why-oryxflow.md)
- [Quickstart](../../docs/quickstart.md)
- Sibling post: [Stop rerunning your pipeline](stop-rerunning-your-pipeline.md)
- [oryxflow on GitHub](https://github.com/oryxintel/oryxflow)
