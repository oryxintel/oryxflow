---
title: Experiment tracking with oryxflow
description: An experiment tracker (MLflow, W&B) and oryxflow do different halves of the same project — how to use both, with tracker logging inside oryxflow tasks so every logged run is a reproducible one.
faq:
  - q: "Can I use oryxflow and MLflow together?"
    a: "Yes, and that's the recommended setup — not a compromise. Put your tracker's logging calls inside an oryxflow task's run(), so every row in your tracker corresponds to a reproducible computation you can rebuild rather than a run you can't, while MLflow (or Weights & Biases, Neptune, Comet) keeps the searchable record of what each run scored. oryxflow works out what a change has invalidated and reuses the rest, so the record stays honest without costing you rerun time."
  - q: "Is oryxflow an MLflow alternative?"
    a: "Not a replacement — a complement. MLflow records and charts the runs you already did; oryxflow is the machinery that produces them, guaranteeing your model scored on features built by current code, deciding which steps must execute, and reusing the ones that don't. Use both. The one case where oryxflow alone is enough is when what you actually wanted from a tracker was reproducible reruns rather than a dashboard."
  - q: "Do I still need an experiment tracker if I use oryxflow?"
    a: "If you want a hosted dashboard, side-by-side run comparison, or a leaderboard your team can browse, yes — oryxflow does not provide any of those and is not trying to. oryxflow gives you reproducible, minimally-recomputed pipelines underneath whichever tracker you pick. If you iterate locally, read your results from a DataFrame, and never wanted the UI, oryxflow on its own covers what you need."
  - q: "How do I log metrics from an oryxflow task?"
    a: "Call your tracker inside the task's run(), next to self.save(). Nothing about oryxflow has to change: compute the metric, log it with mlflow.log_metric(...) or wandb.log(...), and save the artifact with self.save(...). self.to_str_params() hands you the task's parameters as a dict to log in one call, and self.task_id identifies the exact cached output the logged run came from."
  - q: "What's the difference between pipeline caching and experiment tracking?"
    a: "Experiment tracking answers which run got which metric with which parameters, and gives you a UI to sort and chart it. Pipeline caching — what oryxflow does — answers whether a result is still valid: which steps a change has invalidated and must rerun, and which are already current. So a model is never scored on stale inputs, and an unchanged feature build isn't recomputed. A tracker records what happened; oryxflow makes the computation behind it reproducible, and cheap to repeat."
---

# Experiment tracking with oryxflow

**An experiment tracker and oryxflow do different halves of the same project. Using both is the
normal setup, not a compromise.**

This is the comparison people most often read as either/or, so to be explicit about the split:

- A **tracker** (MLflow, Weights & Biases, Neptune, Comet) is a *record of results*. It collects
  metrics, parameters, and artifacts from runs you already did, and gives you a UI to sort and
  chart them.
- **oryxflow** is the *machinery that produces those runs*. It guarantees the features your model
  just scored on were built by current code, keeps every result traceable to the code and
  parameters behind it — and, as a consequence of how it does that, reuses the steps that didn't
  change instead of recomputing them.

A tracker can't tell you a logged run was trained on a stale intermediate. oryxflow can't draw
you a leaderboard. Neither is trying to.

## Which question does it answer?

| The question you're asking | The tool that answers it |
| --- | --- |
| Which run scored 0.91, and with which parameters? | **Tracker** |
| Show me every run's metrics side by side, sorted | **Tracker** |
| Which chart do I paste into the review deck? | **Tracker** |
| Was this model trained on features built by the current code? | **oryxflow** |
| Which steps must rerun to reproduce that 0.91 — and are its inputs stale? | **oryxflow** |
| Where is the output for `window=60` — did I already compute it? | **oryxflow** |
| Can I skip the ten-minute data pull and just retrain? | **oryxflow** |

Read the table one way and it's a feature comparison. Read it the other way and it's a
division of labor: the left column is your *record*, the right column is your *computation*.

## What each tool cannot do for you

Being honest in both directions is the whole point of this page.

**A tracker records what you ran — it doesn't decide what to run.** It will faithfully log that
a run scored 0.91 without knowing whether the features feeding it were regenerated after your
last edit. It won't skip the expensive step that didn't change, and it won't rerun the steps a
code change actually affected. Those are properties of the computation, not of the log.

**oryxflow has no dashboard.** No hosted UI, no run-comparison view, no team-wide leaderboard, no
model registry, no shareable report link. If those are what you're missing, you want a tracker —
see the [MLflow alternatives roundup](../blog/posts/mlflow-alternatives.md) for which one.

**And one boundary that applies to oryxflow specifically:** it makes your analysis
*reproducible*, not *correct*. It guarantees a result came from the code and inputs it records,
and that stale steps get rerun. It does not check that your join grain was right or your
denominator sensible. That last mile is still your review.

## The integration pattern: log from inside the task

The whole integration is one idea — **the tracker call lives inside a task's `run()`**, so every
logged run is also a cached, reproducible one:

<!--phmdoctest-skip-->
```python
import mlflow
import oryxflow

@oryxflow.requires(BuildFeatures)
class Train(oryxflow.tasks.TaskPickle):
    model = oryxflow.ChoiceParameter(default='rf', choices=['rf', 'gbm'])

    def run(self):
        features = self.inputLoad()                    # upstream output, already cached
        clf, auc = fit_and_score(features, kind=self.model)

        mlflow.log_params(self.to_str_params())        # this task's parameters, as a dict
        mlflow.log_metric('auc', auc)
        mlflow.set_tag('oryxflow_task_id', self.task_id)

        self.save(clf)                                 # oryxflow: caches + invalidates
        self.saveMeta({'auc': auc})                    # the score, kept next to the model
```

Three small things are doing real work here:

- **`self.to_str_params()`** hands you every parameter of the task as a `{name: value}` dict, so
  the tracker's parameter list can't drift from what the task was actually run with — you don't
  maintain a second copy of the parameter names.
- **`self.task_id`** identifies the exact cached output this logged run produced. Tag the run with
  it and a row in the tracker points back at a file you can reopen, months later, without
  guessing.
- **`self.saveMeta(...)`** keeps the score beside the model in oryxflow's own cache, so a script
  can read scores back with `flow.outputLoadMeta()` without going through the tracker's API.

Swap `mlflow` for `wandb`, `neptune`, or `comet_ml` — the shape doesn't change. oryxflow has no
tracker integration to configure, because none is needed: it's your code, in your `run()`.

## Sweeping parameters: one comparable row per configuration

This is where the pairing pays off most visibly. Sweep a model parameter and every configuration is
scored against features built by the same current code, so the rows filling up your tracker are
genuinely comparable — and only the affected tasks rerun, the shared feature build happening
**once**.

Drive the sweep with `WorkflowMulti`, one flow per configuration:

<!--phmdoctest-skip-->
```python
flow = oryxflow.WorkflowMulti(Train, params={
    'rf':  {'model': 'rf'},
    'gbm': {'model': 'gbm'},
})

result = flow.run()               # BuildFeatures runs once; Train runs once per model
print(result.ran)                 # exactly what recomputed
print(result.reasons)             # and why each of them did

scores = flow.outputLoadMeta()    # {'rf': {'auc': ...}, 'gbm': {'auc': ...}}
best = flow.outputLoad(flow='gbm')                 # one flow's model, loaded by name
```

Add a third configuration tomorrow and only that one runs; the two you already trained are
complete and skipped, and the expensive feature build is not touched at all. Your tracker gains
exactly one new row. Nothing was recomputed to produce it, and nothing was recomputed *wrongly*
either — if you had edited the feature code in between, every configuration would have retrained
on the new features automatically.

When every flow's output is a schema-compatible DataFrame, `flow.outputLoadConcat(Train)`
row-stacks them into one frame tagged by each flow's parameters — a local leaderboard for the
cases where you don't want to open the UI at all. The full treatment of sweeps, fan-out
aggregation, and scoping a rerun to one family of tasks is in
[Managing complex workflows](managing-workflows.md).

## When one tool alone is enough

Most projects want both. Two honest exceptions:

- **A tracker alone is fine** if you run one script end to end, the run is cheap or happens on
  someone else's infrastructure, and what you want is a leaderboard. There's nothing to cache and
  no dependency graph to get wrong, so oryxflow would be ceremony.
- **oryxflow alone is fine** if you're iterating locally, you read results out of a DataFrame or
  a notebook rather than a UI, and the pain you keep hitting is stale intermediates and expensive
  reruns. That's a computation problem, and a dashboard doesn't fix it. `pip install oryxflow`,
  no server or account, done.

The failure mode to avoid is picking one tool to solve the *other* one's problem: installing a
tracker because your pipeline isn't reproducible, or expecting a caching layer to give you charts.

## Where to read the head-to-heads

This page states the division of labor. The arguments behind it live elsewhere, so you don't get
them twice:

- **[Do you need MLflow, or pipeline caching?](../blog/posts/mlflow-or-pipeline-caching.md)** —
  the two problems that both get called "experiment management," and how to tell which one is
  biting you.
- **[MLflow alternatives](../blog/posts/mlflow-alternatives.md)** — an honest roundup of the
  trackers (W&B, Neptune, Comet, ClearML, Aim, DVCLive, SageMaker Experiments) and which job each
  one is best at.
- **[Why oryxflow](why-oryxflow.md)** — the full comparison table across trackers,
  orchestrators, and DVC.

## Takeaway

- **Different halves, same project.** A tracker owns the record of results; oryxflow owns the
  computation that produced them. Expect to run both.
- **The integration is one line of placement**: put the tracker call inside a task's `run()`, and
  every logged run is also cached and reproducible.
- **Sweeps are where it shows.** Vary a parameter and every configuration is scored on features
  built by current code, so the tracker's rows are comparable — and only the affected tasks rerun.
- **Neither tool covers the other.** oryxflow won't chart your runs; a tracker won't tell you a
  run was trained on something stale. And oryxflow makes analysis reproducible, not correct.

```bash
pip install oryxflow
```

- **[Quickstart](quickstart.md)** — a running, reproducible pipeline in minutes.
- **[Managing complex workflows](managing-workflows.md)** — parameter sweeps, aggregation, and
  scoping a rerun to just what changed.
- **[Running workflows](run.md)** — `Workflow`, `WorkflowMulti`, and what
  reruns when.
- **[Transition from scripts](transition.md)** — turn an existing training script into tasks.
- **[Build with Claude Code](claude-plugin/index.md)** — let an AI agent scaffold the pipeline
  around your tracker calls.

## Frequently asked questions

**Can I use oryxflow and MLflow together?**
Yes, and that's the recommended setup — not a compromise. Put your tracker's logging calls inside
an oryxflow task's `run()`, so every row in your tracker corresponds to a reproducible computation
you can rebuild rather than a run you can't, while MLflow (or Weights & Biases, Neptune, Comet)
keeps the searchable record of what each run scored. oryxflow works out what a change has
invalidated and reuses the rest, so the record stays honest without costing you rerun time.

**Is oryxflow an MLflow alternative?**
Not a replacement — a complement. MLflow records and charts the runs you already did; oryxflow is
the machinery that produces them, guaranteeing your model scored on features built by current code,
deciding which steps must execute, and reusing the ones that don't. Use both. The one case where
oryxflow alone is enough is when what you actually wanted from a tracker was reproducible reruns
rather than a dashboard.

**Do I still need an experiment tracker if I use oryxflow?**
If you want a hosted dashboard, side-by-side run comparison, or a leaderboard your team can
browse, yes — oryxflow does not provide any of those and is not trying to. oryxflow gives you
reproducible, minimally-recomputed pipelines underneath whichever tracker you pick. If you iterate
locally, read your results from a DataFrame, and never wanted the UI, oryxflow on its own covers
what you need.

**How do I log metrics from an oryxflow task?**
Call your tracker inside the task's `run()`, next to `self.save()`. Nothing about oryxflow has to
change: compute the metric, log it with `mlflow.log_metric(...)` or `wandb.log(...)`, and save the
artifact with `self.save(...)`. `self.to_str_params()` hands you the task's parameters as a dict to
log in one call, and `self.task_id` identifies the exact cached output the logged run came from.

**What's the difference between pipeline caching and experiment tracking?**
Experiment tracking answers which run got which metric with which parameters, and gives you a UI
to sort and chart it. Pipeline caching — what oryxflow does — answers whether a result is still
valid: which steps a change has invalidated and must rerun, and which are already current. So a
model is never scored on stale inputs, and an unchanged feature build isn't recomputed. A tracker
records what happened; oryxflow makes the computation behind it reproducible, and cheap to repeat.
