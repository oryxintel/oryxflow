---
date: 2026-07-23
slug: when-not-to-use-oryxflow
categories:
  - Guides
description: An honest guide to when a caching pipeline library like oryxflow is the wrong tool — throwaway exploration, production orchestration, dashboards, and what caching can't check.
---

# When not to use oryxflow

*Being clear about where a tool doesn't fit is part of being trustworthy — so here's where oryxflow is the wrong choice.*

<!-- more -->

oryxflow is a local, zero-infrastructure library for the research loop: it caches task outputs, tracks lineage, and skips work whose inputs and code haven't changed, so your pipeline stays reproducible without a server, a scheduler, or a database. That's genuinely useful — but only for a specific shape of problem. Every honest tool has a boundary, and pretending oryxflow fits everywhere would waste your time and cost you trust. So here is where it doesn't fit, and what to reach for instead.

## Don't reach for oryxflow when… it's throwaway exploration that runs once

If your whole analysis is "load a CSV, group by a column, plot one thing," a task DAG is pure ceremony. You'd write more scaffolding than analysis, and the payoff — skipping re-computation — never arrives because you only run it once.

The value of a caching DAG rises with three things: **depth** (how many dependent steps), **cost** (how expensive each step is), and **breadth** (how many parameter combinations you sweep). For a five-line notebook cell, all three are near zero, so the return is near zero too.

**Use instead:** a plain notebook or script for the first pass. Reach for oryxflow when the notebook starts to hurt — when you're re-running a slow step for the tenth time, or hand-managing intermediate files.

That threshold is softer than it used to be, though, if you build with the [oryxflow Claude Code plugin](../../docs/claude-plugin/index.md). The plugin keeps even exploratory work tidy — it organizes EDA and scratch code in a conventional place from the start, so early analysis isn't a mess you later regret. And when a throwaway script *does* grow teeth — you find yourself re-running it, depending on its output, or sweeping it over parameters — you don't rewrite by hand: `/oryxflow:migrate` restructures the script into a proper cached pipeline, one step at a time. So "start in a notebook, formalize when it earns it" stops being a painful cliff and becomes a one-command migration you run exactly when the work crosses the threshold above.

## Don't reach for oryxflow when… you need production orchestration

Scheduled runs at 6am, retries across a cluster, backfills over a date range, alerting when a job fails, SLAs your team is on the hook for — that's production operations, and oryxflow doesn't do it. There's no scheduler, no distributed retry, no alerting, and no operational UI.

**Use instead:** [Airflow](https://airflow.apache.org/), [Prefect](https://www.prefect.io/), or [Dagster](https://dagster.io/). These are excellent at what they do, and they do a *different* job than oryxflow — they orchestrate operations, oryxflow accelerates the research loop that happens before anything is scheduled. Many teams develop logic in oryxflow and later wrap the finished pipeline in one of these for production. They're complementary, not competitors.

## Don't reach for oryxflow when… you need distributed or very-large-scale execution

If a single step needs a Kubernetes cluster, or your data doesn't fit on one machine and you need engine-level parallelism, the OSS core isn't built for that. It's local-first and runs in-process.

**Use instead:** [Flyte](https://flyte.org/) or [Metaflow](https://metaflow.org/) on Linux or WSL. (oryxflow's paid Pro tier adds SQL, cloud storage, Dask, and PySpark backends — but the open-source core is deliberately local-first, and that's the right lens for evaluating fit here.)

## Don't reach for oryxflow when… you want an experiment dashboard

If what you need is a searchable web UI showing every run's metrics, params, and charts side by side — sortable, filterable, shareable with your team — oryxflow doesn't provide it. It gives you a queryable lineage log, not a hosted dashboard.

**Use instead:** [MLflow](https://mlflow.org/) or [Weights & Biases](https://wandb.ai/). And note these *compose* with oryxflow: log your metrics to MLflow from inside a task's `run()`, and let oryxflow handle the caching and reproducibility around it. You don't pick one — you use both, each for its strength. (More on that split in [MLflow or pipeline caching](mlflow-or-pipeline-caching.md).)

## Don't reach for oryxflow when… you need Git-tied data versioning

If your goal is versioning large data artifacts alongside your code, pinned to Git commits and pushed to remote storage, that's a different discipline.

**Use instead:** [DVC](https://dvc.org/). It also composes with oryxflow — DVC for artifact versioning, oryxflow for the compute graph on top.

## The honest caveat: oryxflow does not check that your result is *correct*

This is the one worth reading twice. oryxflow guarantees that an output was produced by the exact code and inputs it recorded — it makes your pipeline *reproducible*. It does **not** guarantee the result is *right*.

It will happily cache, with full lineage:

- a join that silently went many-to-many when it should have been many-to-one,
- a test set that leaked into training,
- a ratio computed against the wrong denominator,
- a timestamp shifted by a timezone you forgot to normalize,
- a backtest that peeks at data from the future.

Every one of those is reproducible, lineage-tracked, and wrong. oryxflow manages pipeline *mechanics*; it has no opinion about statistical *judgment*. Those bugs are caught by habit — sanity checks, held-out validation, reading your own numbers skeptically — not by any caching machinery. If a post ever tells you a workflow tool makes your analysis correct, close the tab. For where this boundary lives, see [what caching does not protect against](../../docs/managing-workflows.md).

## Where it *is* the right tool

With the boundaries drawn honestly, the fit is clear. oryxflow earns its keep when:

- your pipeline is a **deep chain** of dependent steps,
- some of those steps are **expensive** (minutes to hours),
- you sweep a **matrix of parameters** and want to re-run only what changed,
- you need to **reproduce or hand off** research months later, and
- pipelines are **authored by AI agents** that benefit from an explicit, inspectable task graph.

A typical task is small and declarative — declare dependencies, load inputs, save outputs, and the engine skips anything already computed:

```python
@oryxflow.requires(CleanData)
class Features(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()
        self.save(add_features(df))

flow = oryxflow.Workflow(task=Features)
flow.run()
```

That's the sweet spot: enough depth and cost that caching pays for itself, run often enough that reproducibility matters.

## Takeaway

Use a plain script for quick exploration. Use Airflow, Prefect, or Dagster for production ops. Use Flyte or Metaflow for distributed scale. Use MLflow or W&B for dashboards, DVC for data versioning — and compose them with oryxflow where it helps. And never expect any of them, oryxflow included, to check your statistics for you.

Being honest about fit is the whole point: reach for oryxflow when you have a deep, expensive, reproducible research pipeline, and reach for something else when you don't.

```bash
pip install oryxflow
```

**Read next:** [Why oryxflow](../../docs/why-oryxflow.md) · [Managing complex workflows](../../docs/managing-workflows.md) · [oryxflow vs the field](oryxflow-vs-the-field.md) · [MLflow or pipeline caching](mlflow-or-pipeline-caching.md) · [Claude plugin](../../docs/claude-plugin/index.md) · [GitHub](https://github.com/oryxintel/oryxflow)
