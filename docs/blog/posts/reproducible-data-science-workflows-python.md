---
date: 2026-07-23
slug: reproducible-data-science-workflows-in-python
categories:
  - Reproducibility
description: What it takes to build reproducible data science workflows in Python — deterministic task identity, automatic invalidation, and lineage — with a small, local-first library.
---

# Reproducible data science workflows in Python

*Reproducibility and lineage are the product. Caching is just how you get them for free.*

<!-- more -->

A workflow is reproducible when you can say exactly which code and which inputs produced a given result — and regenerate it on demand. That's the whole definition, and it's a surprisingly high bar. Most analysis pipelines fail it not because anyone was careless, but because the tools we reach for — notebooks and loose scripts — have no memory of what produced what.

This post is about closing that gap in plain Python: what actually makes a data-science workflow reproducible, why workflows quietly lose reproducibility in the first place, and how a small library called [oryxflow](https://github.com/oryxintel/oryxflow) gets you there without a server, a database, or a YAML file. If you want the canonical positioning — where oryxflow sits relative to notebooks and orchestrators — read [why oryxflow](../../docs/why-oryxflow.md). This is the keyword-first explainer.

## Why workflows lose reproducibility

Nobody sets out to build a pipeline they can't reproduce. It happens by accretion:

- **Hidden notebook state.** A variable exists only because you ran a cell you've since edited or deleted. The notebook still runs top-to-bottom in your head, but not on disk.
- **Out-of-order execution.** Cells run 1, 2, 4, 3, 2-again. The result on screen reflects a path through the code that no fresh "Run All" will reproduce.
- **Stale intermediates.** You saved `features.parquet` yesterday, changed the feature code today, and trained on the old file without noticing. Nothing errored. The number is just quietly wrong.
- **No durable link between artifact and code.** You have `model.pkl` and you have your script, but nothing records that *this* model came from *that* commit of the feature code and *those* parameters. Six weeks later, you can't answer the question that matters: *which version made this?*

None of these are exotic. They're the default failure mode of exploratory work, and they all share a root cause: the saved artifacts and the code that made them live in separate worlds, with no enforced connection between them.

## What makes a workflow reproducible

Reproducibility isn't one feature. It's three properties working together.

1. **Deterministic task identity from code + params.** Every step has an identity computed from its code and its parameters. Same code, same inputs → same identity → the same output, every time. Change either and you get a new identity — so a result is never silently attributed to code that didn't produce it.
2. **Automatic invalidation.** You should not be able to evaluate new code against a stale output. When a step's logic changes, that step and everything downstream of it must be recomputed — automatically, without you remembering to delete a cache file. Reproducibility that depends on your discipline isn't reproducibility.
3. **A lineage record.** A durable, append-only log of what ran, when, and why. Not because it's tidy, but because "regenerate this result" requires knowing what produced it in the first place.

oryxflow is built around exactly these three. You declare each step of your analysis as a `Task` with its parameters and its dependencies; the library runs them in dependency order, caches each output, and reruns a step only when its code or inputs actually change. The identity is deterministic, the invalidation is automatic, and every run appends to a lineage file. You don't maintain reproducibility — you get it as a side effect of writing tasks.

### Deterministic identity, and automatic code-change invalidation

The part that's easy to underrate: oryxflow watches your **code**, not just your parameters. It builds an AST-normalized fingerprint of each task's logic plus the project files it transitively imports. Edit the body of `BuildFeatures` — or a helper function it calls — and oryxflow knows that task's output is stale and reruns it and everything downstream. Reformat the code, add a comment, rename a local variable? The normalized fingerprint is unchanged, so nothing reruns. And an expensive upstream step whose code you didn't touch stays cached.

That's the property that makes the research loop both fast and trustworthy: you can never test new logic against an output the old logic produced, and you never pay to recompute a step that didn't change.

## Building a small reproducible DAG

Here's the canonical shape — `GetData` → `BuildFeatures` → `TrainModel` — with nothing but the verified API. Each task's base class picks its serialization by type: `TaskPqPandas` saves a DataFrame as parquet, `TaskPickle` saves an arbitrary Python object. No paths, no `to_parquet` calls, no config.

```python
import oryxflow

oryxflow.set_dir('data/')                          # where cached outputs live


class GetData(oryxflow.tasks.TaskPqPandas):        # saves a DataFrame as parquet
    source = oryxflow.Parameter(default='raw.parquet')

    def run(self):
        df = load_table(self.source)               # your loader
        self.save(df)


@oryxflow.requires(GetData)                        # declares the dep AND copies params
class BuildFeatures(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()                      # the upstream DataFrame
        df['x_squared'] = df['x'] ** 2
        self.save(df[['x', 'x_squared', 'y']])


@oryxflow.requires(BuildFeatures)
class TrainModel(oryxflow.tasks.TaskPickle):       # saves any Python object
    alpha = oryxflow.FloatParameter(default=1.0)

    def run(self):
        df = self.inputLoad()
        model = fit_model(df[['x', 'x_squared']], df['y'], alpha=self.alpha)
        self.save(model)
        self.saveMeta({'n_rows': len(df), 'alpha': self.alpha})
```

Run it through a `Workflow`, which resolves the DAG and runs only what's needed:

```python
flow = oryxflow.Workflow(TrainModel, {'alpha': 0.5})
flow.preview()                                     # show the plan without running
flow.run()                                         # runs GetData -> BuildFeatures -> TrainModel

model = flow.outputLoad()                           # the trained model
meta = flow.outputLoadMeta()                        # {'n_rows': ..., 'alpha': 0.5}
```

The first run computes all three steps. Edit `TrainModel.run` and rerun: `GetData` and `BuildFeatures` stay cached, only `TrainModel` recomputes. Change `alpha` and you get a distinct identity — the old and new models coexist, each tied to its parameters. Nothing here writes a path or checks whether a file already exists; that bookkeeping is the library's job. For the fuller pattern — parameter sweeps, resetting, sharing flows — see [managing workflows](../../docs/managing-workflows.md).

## When to use it — and when not to

Reach for a reproducible DAG when a result needs to be defensible: something you'll hand to a colleague, revisit in a month, or make a decision on. The moment a workflow has more than one expensive step and you're iterating on the later ones, deterministic identity and automatic caching pay for themselves.

Don't reach for it during throwaway exploratory data analysis. If you're eyeballing distributions in a scratch notebook and nothing downstream depends on the output, task boilerplate is pure overhead. Structure the workflow once the shape stabilizes and the results start to matter. There's a whole post on the boundary: [when not to use oryxflow](when-not-to-use-oryxflow.md).

## Where oryxflow sits

Reproducibility isn't binary, and oryxflow isn't the only layer that touches it. It occupies the missing middle between a notebook and a production orchestrator.

| | Notebook / loose script | oryxflow | Orchestrator (Airflow / Prefect / Dagster) |
| --- | :---: | :---: | :---: |
| Reproducible task identity | ❌ manual, by convention | ✅ deterministic, from code + params | ⚠️ run versioning, not code-aware |
| Caching | ❌ you manage files by hand | ✅ automatic, code-change aware | ⚠️ opt-in / configured |
| Lineage record | ❌ none | ✅ append-only `.oryxflow/events.jsonl` | ✅ run history in a DB / UI |
| Infrastructure | ✅ none | ✅ none — local files only | ❌ server, DB, scheduler |
| Job it's built for | Exploration | The trustworthy research loop | Scheduled production pipelines |

Orchestrators are a **complementary** layer, not a competitor: they schedule and distribute pipelines in production, which is a real and different problem. Experiment trackers like MLflow and W&B are complementary too — they *record* runs; oryxflow *structures and caches* them. oryxflow's differentiators are the combination that neither layer offers: type-driven zero-config I/O, automatic code-change invalidation, deterministic task identity, and a local-first design with native Python identity — no YAML, no server, no account, no telemetry. There's a full comparison in [oryxflow vs. the field](oryxflow-vs-the-field.md).

## The honest caveat: reproducible ≠ correct

Be clear about what reproducibility buys you. oryxflow guarantees that an output was produced by the exact code and inputs it recorded — no more. It does **not** guarantee that the output is *right*. A pipeline with a bug in its feature logic is reproduced just as faithfully as a correct one; you'll get the same wrong number every time, tied cleanly to the flawed code that made it.

That's not a weakness — it's the honest scope. Reproducibility is what makes a wrong result *debuggable*: because you know exactly which code and inputs produced it, you can find the bug, fix it, and let automatic invalidation rerun precisely what the fix touched. Correctness is still your job. Reproducibility is what makes doing that job tractable.

## Takeaway

A workflow is reproducible when you can name the exact code and inputs behind any result and regenerate it. That takes three things — deterministic task identity, automatic invalidation so you can't test new code on stale outputs, and a durable lineage record. oryxflow gives you all three in plain Python, locally, with the caching that makes them cheap thrown in for free. It won't make your pipeline correct. It will make it something you can trust, hand off, and reproduce.

```bash
pip install oryxflow
```

**Read next:** [From notebook to a reproducible pipeline](notebook-to-pipeline.md) · [Stop rerunning your whole pipeline](stop-rerunning-your-pipeline.md) · [When not to use oryxflow](when-not-to-use-oryxflow.md) · [oryxflow vs. the field](oryxflow-vs-the-field.md) · [Why oryxflow](../../docs/why-oryxflow.md) · [Managing workflows](../../docs/managing-workflows.md) · [Build with the Claude Code plugin](../../docs/claude-plugin/index.md)
