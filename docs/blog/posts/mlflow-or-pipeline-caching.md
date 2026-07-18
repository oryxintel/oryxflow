---
date: 2026-07-11
slug: mlflow-vs-pipeline-caching
categories:
  - MLOps
description: They sound like the same problem. They're not — and mixing them up is why so many ML projects still can't reproduce last week's result.
---

# Do you need MLflow, or do you need reproducible pipeline caching?

*They sound like the same problem. They're not — and mixing them up is why so many ML
projects still can't reproduce last week's result.*

<!-- more -->


If you've searched for "manage machine learning experiments" you've been pointed at
MLflow, Weights & Biases, and DVC. They're excellent tools. But a lot of people install
one, log some metrics, and are surprised to find their pipeline is *still* a mess of stale
pickle files they can't reliably reproduce. That's because these tools solve a **different
half** of the problem than the one biting you.

## Two different problems that both get called "experiment management"

**Problem A — tracking:** *"Which run got 0.91 AUC, and what were its hyperparameters?"*
This is a logging and comparison problem. It's what **MLflow**, **W&B**, and
**Neptune** are built for: you call `log_metric(...)`, `log_param(...)`, and get a
searchable dashboard of every run.

**Problem B — computation:** *"To reproduce that 0.91 run, which steps do I actually need
to rerun, and which are already computed?"* This is a dependency and caching problem. It's
about **not** recomputing a 10-minute feature step when only the model changed, and about
**guaranteeing** the model you're evaluating was trained on the current data.

Trackers answer A. They do **not** answer B. MLflow will faithfully log that you got 0.91
— it has no idea whether the features feeding that model are stale, and it won't skip
recomputing them for you. That gap is where reproducibility quietly dies.

## What a pipeline-caching engine does that a tracker doesn't

A workflow engine like [`oryxflow`](https://github.com/oryxintel/oryxflow) models your
work as a DAG of tasks and owns the computation side:

```python
import oryxflow

class GetData(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(load_data())

@oryxflow.requires(GetData)
class BuildFeatures(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(build_features(self.inputLoad()))

@oryxflow.requires(BuildFeatures)
class TrainModel(oryxflow.tasks.TaskPickle):
    model = oryxflow.Parameter(default='gbm')

    def run(self):
        features = self.inputLoad()
        clf = fit(self.model, features)
        self.save(clf)
        self.saveMeta({'score': clf.score(...)})   # <-- log to MLflow here too

oryxflow.run(TrainModel())
```

From this it gives you three things a tracker structurally cannot:

1. **Skip-what's-done.** Rerun the script and completed tasks load from cache instead of
   recomputing. Change one task and only its downstream reruns.
2. **Correct-by-construction invalidation.** Change a parameter, the data, or a task's
   code, and exactly the affected outputs are marked stale and rebuilt. You can't
   accidentally evaluate a new model on old features.
3. **Load-any-result-by-name.** `TrainModel().output().load()` gives you the model;
   `BuildFeatures().output().load()` gives you the features — no hunting for `.pkl` paths.

## The honest answer: use both

This isn't "oryxflow vs MLflow." The two compose cleanly:

```python
def run(self):
    features = self.inputLoad()
    clf = fit(self.model, features)
    self.save(clf)                         # oryxflow: caches + invalidates
    mlflow.log_param('model', self.model)  # MLflow: dashboard + comparison
    mlflow.log_metric('score', clf.score(...))
```

- **oryxflow** owns the *pipeline*: dependency order, caching, minimal reruns,
  reproducibility.
- **MLflow / W&B** own the *record*: the searchable history of what each run scored.

Put the tracker calls **inside** your oryxflow tasks and you get both a reproducible
computation graph and a clean experiment log — without either tool pretending to be the
other.

## What about DVC?

DVC is the tool people most often conflate with this space, because it *does* do
pipeline caching. The honest difference is what identity is built from. **DVC hashes
files and YAML-declared stages**: you describe your pipeline in `dvc.yaml` — each stage's
command, dependencies, and outputs — and DVC recomputes a stage when a declared file
hash changes. **oryxflow's identity is native Python task identity — parameters plus an
automatic code fingerprint, zero config files**: the DAG *is* your `requires()` methods, a
parameter change is automatically a new cached identity (no stage file to edit), and a
code change reruns the task and everything downstream on its own — an AST-normalized hash
of the task's module and its project-local imports, so comment and formatting edits never
recompute (pin a task with `code_version` when you'd rather manage it by deliberate
bumps). If your workflow is command-line stages over
large versioned data files, DVC's file-hash model fits. If your workflow is Python tasks
you iterate on inside a session — parameter sweeps, per-entity fan-outs — keeping
identity in the code you're already editing beats maintaining a parallel YAML description
of it.

## So which do you actually need?

- You can already reproduce runs but can't *compare* them → you want a **tracker**
  (MLflow/W&B).
- You can log runs but rerunning your pipeline is slow, fragile, and you're never sure
  what's stale → you want a **caching workflow engine** (oryxflow, or its heavier cousins
  Luigi, Metaflow, Kedro).
- Most real projects want both.

If it's the second problem you feel every day — the fifteen-minute edit-rerun loop, the
`features_v3_final.pkl` graveyard, the "wait, was this trained on the new data?" — start
here:

```bash
pip install oryxflow
```

Docs: https://oryxflow.readthedocs.io · Source: https://github.com/oryxintel/oryxflow

oryxflow is a lightweight, dependency-free alternative to Luigi, Metaflow, and Kedro,
focused on research iteration rather than production orchestration — and it plays nicely
with whatever tracker you already use.
