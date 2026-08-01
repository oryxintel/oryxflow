---
date: 2026-07-11
slug: stop-rerunning-your-pipeline
categories:
  - Caching
description: Why you rerun everything — you can't tell what's stale — and how declaring your steps as tasks reruns exactly what a change affects, so results stay trustworthy and reruns stay cheap.
faq:
  - q: "Can I trust a pipeline that skips steps it has already run?"
    a: "Only if the decision to skip is made from the identity of the work rather than from a filename you chose. oryxflow derives each task's identity from its code, its inputs, and its parameters, so a step is reused only when all three are unchanged, and it reruns automatically the moment any of them move. A result therefore can't quietly sit on data or logic you have since changed — you get the answer a from-scratch run would give, without paying for the from-scratch run."
  - q: "How do I stop rerunning my whole pipeline when I change one step?"
    a: "When you edit one step, you want only that step and its downstream to recompute, not the whole script. Model each step as a task with declared dependencies so an engine can run them in dependency order and reuse anything whose code, inputs, and parameters are unchanged. oryxflow does this: it reruns exactly the steps a code, data, or parameter change actually affects — so nothing downstream is left sitting on a stale input, and a one-line edit stops triggering a fifteen-minute recompute."
  - q: "How do I make Python reruns only recompute what changed?"
    a: "Turn your script into tasks that declare what they depend on and what they produce, then let an engine track each task's identity from its code and parameters. When something changes, only the affected tasks are stale, and they rerun without you remembering to clear anything. oryxflow, a small local-first Python library, does exactly this: what changed is rebuilt, and unchanged upstream steps load from disk instead of recomputing."
---

# Stop rerunning your whole pipeline when one step changes

*You rerun everything because you can't tell what's stale. Fix that, and the rerun gets cheap on its own.*

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

And notice what the fifteen-minute rerun actually is: **the price you pay for not knowing
what's stale.** You don't rerun `load_data` and `clean` because you think they changed —
you rerun them because you can't prove they didn't, and a number built on a stale input is
worse than no number at all. Fix the *knowing*, and the waiting takes care of itself.

## The fix: make each step a task, and let the engine decide what's stale

The clean solution is to stop thinking in *lines of a script* and start thinking in
*tasks with dependencies* — a DAG. Each task declares what it needs, what it produces,
and where its output is stored. The engine then:

- reruns **every** step affected by a code, data, or parameter change — so a result can't
  quietly sit on inputs or logic you've since edited,
- runs steps in dependency order, so nothing is computed from something that hasn't been
  rebuilt yet,
- and **reuses any step whose code, inputs, and parameters are unchanged** — which is why
  correctness here doesn't cost you time.

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

Change `train()` and only `TrainModel` reruns — `GetData` and `BuildFeatures` are unchanged,
so their existing outputs are still the right answer. **That's the part that matters: the
rerun is exact, so you can't evaluate new code against an output the old code produced.**
The speed is what falls out of it — the fifteen-minute edit-rerun loop becomes eight
minutes, then eight seconds — and you never wrote a single `if os.path.exists(...)`.

## Parameter-aware invalidation: every configuration keeps its own answer

The real payoff shows up when you compare models. Add a parameter and oryxflow tracks a
separate output per parameter value automatically, so two configurations can never
overwrite each other's results:

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

Both models are scored against the *same* features — not because you remembered to keep
them in sync, but because there's only one `BuildFeatures` output for both to read. That's
what makes the comparison defensible. Training the second model **does not** rerun
`GetData` or `BuildFeatures`; oryxflow figures out the minimal set of work for each
configuration, which is also the difference between "sweep five hyperparameters over
coffee" and "sweep five hyperparameters over lunch."

## Where this fits (and where it doesn't)

oryxflow is a **research-iteration** tool. Reach for it when your day is EDA → feature
engineering → train → evaluate and you need the numbers at the end of it to be defensible
— something you'll hand to a colleague or revisit in a month. It works with any ML library
— sklearn, PyTorch, XGBoost — because it only cares about task inputs and outputs, not
what happens inside `run()`.

One honest boundary: this makes your result *reproducible*, not *correct*. A bug in your
feature logic is reproduced just as faithfully as good logic — what you get is a number
you can always trace back to the exact code and inputs that made it, which is what makes
the bug findable in the first place.

It is **not** a production orchestrator. If you need cron-style scheduling, retries across
a cluster, and SLAs, use Airflow, Prefect, or Dagster — they're a complementary layer, not
a competitor. Same for experiment trackers: keep logging metrics to MLflow or Weights &
Biases inside your tasks — oryxflow handles the rerun logic and the code-to-result link
those tools don't.

## Try it

```bash
pip install oryxflow
```

- Docs: https://docs.oryxflow.dev
- Source & examples: https://github.com/oryxintel/oryxflow

The next time you change one line and reach for the run button, you shouldn't have to
recompute everything upstream of it just to be sure of the answer. Let the DAG track what
changed — the trustworthy path turns out to be the fast one.

## Frequently asked questions

### Can I trust a pipeline that skips steps it has already run?

Only if the decision to skip is made from the identity of the work rather than from a filename you chose. oryxflow derives each task's identity from its code, its inputs, and its parameters, so a step is reused only when all three are unchanged, and it reruns automatically the moment any of them move. A result therefore can't quietly sit on data or logic you have since changed — you get the answer a from-scratch run would give, without paying for the from-scratch run.

### How do I stop rerunning my whole pipeline when I change one step?

When you edit one step, you want only that step and its downstream to recompute, not the whole script. Model each step as a task with declared dependencies so an engine can run them in dependency order and reuse anything whose code, inputs, and parameters are unchanged. oryxflow does this: it reruns exactly the steps a code, data, or parameter change actually affects — so nothing downstream is left sitting on a stale input, and a one-line edit stops triggering a fifteen-minute recompute.

### How do I make Python reruns only recompute what changed?

Turn your script into tasks that declare what they depend on and what they produce, then let an engine track each task's identity from its code and parameters. When something changes, only the affected tasks are stale, and they rerun without you remembering to clear anything. oryxflow, a small local-first Python library, does exactly this: what changed is rebuilt, and unchanged upstream steps load from disk instead of recomputing.
