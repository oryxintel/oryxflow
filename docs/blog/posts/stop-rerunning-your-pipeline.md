---
date: 2026-07-11
slug: cache-intermediate-dataframes-python
categories:
  - Caching
description: How data scientists waste hours recomputing steps that never changed — and a lightweight way to fix it.
faq:
  - q: "How do I stop rerunning my whole pipeline when I change one step?"
    a: "When you edit one step, you want only that step and its downstream to recompute, not the whole script. Model each step as a task with declared dependencies so an engine can run them in dependency order and skip anything already computed. oryxflow does this: it caches each task's output and reruns only the steps a code, data, or parameter change actually affects, so a one-line edit stops triggering a fifteen-minute recompute."
  - q: "How do I make Python reruns only recompute what changed?"
    a: "Turn your script into tasks that declare what they depend on and what they produce, then let an engine track each task's identity from its code and parameters. When something changes, only the affected tasks are stale. oryxflow, a small local-first Python library, caches every task output and reruns only what changed on the next run, so unchanged upstream steps load from disk instead of recomputing."
---

# Stop rerunning your whole pipeline: caching intermediary DataFrames in Python

*How data scientists waste hours recomputing steps that never changed — and a lightweight way to fix it.*

<!-- more -->


## The problem every data science script eventually has

Almost every analysis starts as a linear script:

```python
df = load_data()              # 40 seconds
df = clean(df)                # 2 minutes
features = build_features(df) # 5 minutes
model = train(features)       # 8 minutes
evaluate(model, features)
```

It works. Then you tweak `evaluate()` — a one-line change — and to see the result you
rerun the file and wait **fifteen minutes** while `load_data`, `clean`, and
`build_features` recompute output that is byte-for-byte identical to last time.

So you start hand-rolling caches:

```python
if os.path.exists('features.pkl'):
    features = pd.read_pickle('features.pkl')
else:
    features = build_features(df)
    features.to_pickle('features.pkl')
```

Now multiply that by every step, every parameter, and every teammate who doesn't know
which `.pkl` is stale. You're manually tracking filenames, manually invalidating caches,
and quietly training models on yesterday's data. This is the single most common way
machine learning code rots.

## The fix: make each step a task, let the engine cache

The clean solution is to stop thinking in *lines of a script* and start thinking in
*tasks with dependencies* — a DAG. Each task declares what it needs, what it produces,
and where its output is stored. The engine then:

- runs steps in dependency order,
- **skips any step whose output already exists**,
- and reruns **only** the steps affected by a code, data, or parameter change.

[`oryxflow`](https://github.com/oryxintel/oryxflow) is a small, dependency-free Python
library that does exactly this. Here's the pipeline above, rewritten:

```python
import oryxflow
import pandas as pd

class GetData(oryxflow.tasks.TaskPqPandas):   # output persisted as Parquet
    def run(self):
        df = load_data()
        self.save(df)                          # no filename to manage

@oryxflow.requires(GetData)                    # declares the dependency
class BuildFeatures(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()                  # loads GetData's output
        self.save(build_features(df))

@oryxflow.requires(BuildFeatures)
class TrainModel(oryxflow.tasks.TaskPickle):   # output persisted as pickle
    def run(self):
        features = self.inputLoad()
        model = train(features)
        self.save(model)
        self.saveMeta({'score': model.score(...)})

oryxflow.run(TrainModel())
```

Run it once and all three tasks execute. Run it again and you get:

```
Scheduled 3 tasks
* 0 ran successfully
* 3 complete          <-- nothing recomputed, output loaded from disk
* 0 failed
```

Change `train()` and only `TrainModel` reruns — `GetData` and `BuildFeatures` are served
from cache. **The fifteen-minute edit-rerun loop becomes eight minutes, then eight
seconds.** You never wrote a single `if os.path.exists(...)`.

## The part that actually saves you: parameter-aware invalidation

The real payoff shows up when you compare models. Add a parameter and oryxflow tracks a
separate cached output per parameter value automatically:

```python
@oryxflow.requires(GetData)
class TrainModel(oryxflow.tasks.TaskPickle):
    model = oryxflow.Parameter(default='ols')  # a knob you'll sweep

    def run(self):
        features = self.inputLoad()
        clf = LinearRegression() if self.model == 'ols' else GradientBoostingRegressor()
        clf.fit(features.drop('y', axis=1), features['y'])
        self.save(clf)
        self.saveMeta({'score': clf.score(features.drop('y', axis=1), features['y'])})

flow = oryxflow.WorkflowMulti(TrainModel, {
    'ols': {'model': 'ols'},
    'gbm': {'model': 'gbm'},
})
flow.run()
print(flow.outputLoadMeta())
# {'ols': {'score': 0.74}, 'gbm': {'score': 0.97}}
```

Training the second model **does not** rerun `GetData` or `BuildFeatures` — they're shared
and already cached. oryxflow figures out the minimal set of work for each configuration.
That's the difference between "sweep five hyperparameters over coffee" and "sweep five
hyperparameters over lunch."

## Where this fits (and where it doesn't)

oryxflow is a **research-iteration** tool. Reach for it when your day is EDA → feature
engineering → train → evaluate and you're tired of babysitting intermediate files. It
works with any ML library — sklearn, PyTorch, XGBoost — because it only cares about task
inputs and outputs, not what happens inside `run()`.

It is **not** a production orchestrator. If you need cron-style scheduling, retries across
a cluster, and SLAs, use Airflow, Prefect, or Dagster. And it's **complementary** to
experiment trackers: keep logging metrics to MLflow or Weights & Biases inside your
tasks — oryxflow handles the caching and rerun logic those tools don't.

## Try it

```bash
pip install oryxflow
```

- Docs: https://docs.oryxflow.dev
- Source & examples: https://github.com/oryxintel/oryxflow

The next time you change one line and reach for the run button, you shouldn't have to
recompute everything upstream of it. Let the DAG remember what's already done.

## Frequently asked questions

### How do I stop rerunning my whole pipeline when I change one step?

When you edit one step, you want only that step and its downstream to recompute, not the whole script. Model each step as a task with declared dependencies so an engine can run them in dependency order and skip anything already computed. oryxflow does this: it caches each task's output and reruns only the steps a code, data, or parameter change actually affects, so a one-line edit stops triggering a fifteen-minute recompute.

### How do I make Python reruns only recompute what changed?

Turn your script into tasks that declare what they depend on and what they produce, then let an engine track each task's identity from its code and parameters. When something changes, only the affected tasks are stale. oryxflow, a small local-first Python library, caches every task output and reruns only what changed on the next run, so unchanged upstream steps load from disk instead of recomputing.
