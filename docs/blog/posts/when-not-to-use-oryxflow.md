---
date: 2026-07-23
slug: when-not-to-use-oryxflow
categories:
  - Guides
description: An honest guide to when oryxflow is the wrong tool — production orchestration, distributed scale, dashboards, and what a caching pipeline library can't check.
faq:
  - q: "When should I not use oryxflow?"
    a: "Skip oryxflow for production orchestration (scheduling, retries, alerting — use Airflow, Prefect, or Dagster), for distributed or larger-than-memory execution (Flyte or Metaflow), for a hosted experiment dashboard (MLflow or W&B), and for Git-tied data versioning (DVC). oryxflow is a local, zero-infrastructure library for making research results trustworthy and reproducible; it reruns exactly what a change affects and reuses the rest, but it doesn't schedule, scale out, or display."
  - q: "Is oryxflow overkill for a quick one-off analysis?"
    a: "No — a five-line cell you run once doesn't need task classes, and you don't have to decide upfront either way. If you build with the oryxflow Claude Code plugin, exploration gets a home in the project from the start: a read-only probe script that states the question it answers and still runs next session, instead of a snippet you lose. When a probe turns out to be load-bearing — you keep re-running it, or something downstream depends on its result — /oryxflow:migrate lifts it into cached, parameterized tasks and never deletes the original. So you can start small in oryxflow and scale to any complexity, with no rewrite in between."
  - q: "Does oryxflow check that my analysis is correct?"
    a: "No — and no workflow tool does. oryxflow guarantees an output was produced by the exact code and inputs it recorded, which makes your pipeline reproducible, not correct. It will happily cache, with full lineage, a leaked test set, a bad join, or a backtest that peeks at the future. Those bugs are caught by sanity checks, held-out validation, and reading your own numbers skeptically — not by any caching machinery."
---

# When not to use oryxflow

*Being clear about where a tool doesn't fit is part of being trustworthy — so here's where oryxflow is the wrong choice.*

<!-- more -->

oryxflow is a local, zero-infrastructure library for the research loop: it ties every result to the code and inputs that produced it, tracks lineage, and reruns exactly what a change affects — so a number can't quietly sit on stale data and you can regenerate any result months later, without a server, a scheduler, or a database. (It reuses the steps that didn't change, which is why none of that costs you rerun time.) That's genuinely useful — but only for a specific shape of problem. Every honest tool has a boundary, and pretending oryxflow fits everywhere would waste your time and cost you trust. So here is where it doesn't fit, and what to reach for instead.

## A quick one-off doesn't need a pipeline — but you can still start here

If your whole analysis is "load a CSV, group by a column, plot one thing," you don't need task classes around it. Five lines you'll run once get no payoff from caching, and wrapping them in a DAG is ceremony.

The value of a caching DAG rises with three things: **depth** (how many dependent steps), **cost** (how expensive each step is), and **breadth** (how many parameter combinations you sweep). For a five-line notebook cell, all three are near zero, so the return is near zero too.

**Use instead:** a plain script for the first pass — but keep it *inside the project*, not in a scratch folder you'll abandon. If you build with the [oryxflow Claude Code plugin](../../docs/claude-plugin/index.md), that's already the convention: exploration lives as a read-only probe at `eda/<subject>/<name>.py`, one line of docstring stating the question it answers, printing the answer legibly, run with `python -m eda.<subject>.<name>`. A probe writes no pipeline artifact — disposable scratch goes to a gitignored scratch area — and anything material it turns up gets written into the project's data doc, so a question you've answered once isn't re-asked next session.

Then, when a script turns out to be load-bearing — you keep re-running it, something depends on its output, or you start sweeping it over parameters — you don't rewrite it by hand. `/oryxflow:migrate` reads the script as the spec, shows you a step-to-task map, writes only on your approval, and never deletes the source. So there's no cliff between "exploring" and "having a pipeline": **you can start with a small EDA and let it scale to any complexity, with no rewrite in between.** More on both ends: [migrate a notebook to a pipeline](../../docs/migrate-notebook-to-pipeline.md) and [Claude Code for data science](../../docs/claude-code-for-data-science.md).

## Don't reach for oryxflow when… you need production orchestration

Scheduled runs at 6am, retries across a cluster, backfills over a date range, alerting when a job fails, SLAs your team is on the hook for — that's production operations, and oryxflow doesn't do it. There's no scheduler, no distributed retry, no alerting, and no operational UI.

**Use instead:** [Airflow](https://airflow.apache.org/), [Prefect](https://www.prefect.io/), or [Dagster](https://dagster.io/). These are excellent at what they do, and they do a *different* job than oryxflow — they orchestrate operations; oryxflow makes the research loop that happens before anything is scheduled trustworthy and reproducible. Many teams develop logic in oryxflow and later wrap the finished pipeline in one of these for production. They're complementary, not competitors.

## Don't reach for oryxflow when… you need distributed or very-large-scale execution

If a single step needs a Kubernetes cluster, or your data doesn't fit on one machine and you need engine-level parallelism, the OSS core isn't built for that. It's local-first and runs in-process.

**Use instead:** [Flyte](https://flyte.org/) or [Metaflow](https://metaflow.org/) on Linux or WSL. (oryxflow's paid Pro tier adds SQL, cloud storage, Dask, and PySpark backends — but the open-source core is deliberately local-first, and that's the right lens for evaluating fit here.)

## Don't reach for oryxflow when… you want an experiment dashboard

If what you need is a searchable web UI showing every run's metrics, params, and charts side by side — sortable, filterable, shareable with your team — oryxflow doesn't provide it. It gives you a queryable lineage log, not a hosted dashboard.

**Use instead:** [MLflow](https://mlflow.org/) or [Weights & Biases](https://wandb.ai/). And note these *compose* with oryxflow: log your metrics to MLflow from inside a task's `run()`, and let oryxflow handle the caching and reproducibility around it. You don't pick one — you use both, each for its strength. (More on that split in [MLflow, or a reproducible pipeline](mlflow-or-pipeline-caching.md).)

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

- a result has to be **defensible** — you'll hand it to someone, or make a decision on it,
- you need to **reproduce or hand off** that research months later,
- your pipeline is a **deep chain** of dependent steps, where staleness has somewhere to hide,
- some of those steps are **expensive** (minutes to hours), so rerunning everything to be sure isn't an option,
- you sweep a **matrix of parameters** and want every configuration compared against the same upstream, and
- pipelines are **authored by AI agents** that benefit from an explicit, inspectable task graph.

A typical task is small and declarative — declare dependencies, load inputs, save outputs, and the engine reruns what a change affects and reuses the rest:

```python
@oryxflow.requires(CleanData)
class Features(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()
        self.save(add_features(df))

flow = oryxflow.Workflow(task=Features)
flow.run()
```

That's the sweet spot: results that have to hold up, in a pipeline deep and expensive enough that you'd never verify them by rerunning the whole thing from scratch.

## Takeaway

Start a quick exploration as a plain script — inside an oryxflow project, where `/oryxflow:migrate` can promote it the day it earns a pipeline. Use Airflow, Prefect, or Dagster for production ops. Use Flyte or Metaflow for distributed scale. Use MLflow or W&B for dashboards, DVC for data versioning — and compose them with oryxflow where it helps. And never expect any of them, oryxflow included, to check your statistics for you.

Being honest about fit is the whole point: reach for oryxflow when you have a research pipeline — or the first small script that might become one — and reach for something else when the job is scheduling, scaling out, or display.

```bash
pip install oryxflow
```

## Frequently asked questions

### When should I not use oryxflow?

Skip oryxflow for production orchestration (scheduling, retries, alerting — use Airflow, Prefect, or Dagster), for distributed or larger-than-memory execution (Flyte or Metaflow), for a hosted experiment dashboard (MLflow or W&B), and for Git-tied data versioning (DVC). oryxflow is a local, zero-infrastructure library for making research results trustworthy and reproducible; it reruns exactly what a change affects and reuses the rest, but it doesn't schedule, scale out, or display.

### Is oryxflow overkill for a quick one-off analysis?

No — a five-line cell you run once doesn't need task classes, and you don't have to decide upfront either way. If you build with the oryxflow Claude Code plugin, exploration gets a home in the project from the start: a read-only probe script that states the question it answers and still runs next session, instead of a snippet you lose. When a probe turns out to be load-bearing — you keep re-running it, or something downstream depends on its result — /oryxflow:migrate lifts it into cached, parameterized tasks and never deletes the original. So you can start small in oryxflow and scale to any complexity, with no rewrite in between.

### Does oryxflow check that my analysis is correct?

No — and no workflow tool does. oryxflow guarantees an output was produced by the exact code and inputs it recorded, which makes your pipeline reproducible, not correct. It will happily cache, with full lineage, a leaked test set, a bad join, or a backtest that peeks at the future. Those bugs are caught by sanity checks, held-out validation, and reading your own numbers skeptically — not by any caching machinery.

**Read next:** [Why oryxflow](../../docs/why-oryxflow.md) · [Managing complex workflows](../../docs/managing-workflows.md) · [oryxflow vs the field](oryxflow-vs-the-field.md) · [MLflow, or a reproducible pipeline](mlflow-or-pipeline-caching.md) · [Claude plugin](../../docs/claude-plugin/index.md) · [GitHub](https://github.com/oryxintel/oryxflow)
