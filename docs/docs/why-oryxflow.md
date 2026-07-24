---
title: Why oryxflow
description: oryxflow makes AI-driven data analysis faster, cheaper, and more trustworthy — reproducible, lineage-tracked Python pipelines that rerun only what changed, for humans and AI coding agents alike.
faq:
  - q: "Is oryxflow an MCP server?"
    a: "No. oryxflow ships a Claude Code plugin — a skill plus slash commands — backed by an open-source Python library. The reproducibility work happens locally in that library, not over MCP."
  - q: "Does oryxflow replace MLflow or Airflow?"
    a: "No — it composes beside them. oryxflow caches and reruns your local research pipeline; keep using an experiment tracker for dashboards and an orchestrator for scheduled production jobs."
  - q: "How does oryxflow know when to rerun a task?"
    a: "From the task's parameters and its code. Change a parameter, a data input, or the code and oryxflow reruns exactly the affected outputs — cosmetic edits like comments or formatting don't trigger a rerun."
  - q: "Where is my data stored?"
    a: "Locally. oryxflow is local-first and zero-infrastructure — no server, no database, no account, no telemetry. Your code, your cache, your repo."
---

# Why oryxflow

**oryxflow makes data-science work faster, cheaper, and more trustworthy** — it turns an
analysis script into a reproducible pipeline that records how every result was made and reruns
only what actually changed. It's a pip-installable Python library with no server, no database,
and no account: your code, your cache, your repo.

If you only remember one thing: oryxflow is the layer that makes an iterative analysis
**trustworthy** — for you, your teammates, and the AI coding agent writing half the code.

## The problem: iterative analysis quietly stops being trustworthy

Almost every project starts as a script that works. Then it accumulates the failures that
erode trust in the result long before anyone questions the math:

- **Stale intermediates.** You change a feature, forget to regenerate a cached file, and train
  on yesterday's data. Nothing errors. The number is just wrong.
- **Lost lineage.** Six months (or six hours) later, no one can say which code and which inputs
  produced `model_final_v3.pkl`.
- **Wasted recomputation.** A one-line change downstream re-runs the 10-minute data pull, so you
  either wait or start hand-rolling `if os.path.exists(...)` caches that themselves go stale.
- **AI-generated code you can't fully trust.** Coding agents write plausible pandas and
  scikit-learn fast — but across a long session they lose track of what's already computed and
  whether it's still valid, and silently build on stale state.

None of these are math errors. They're **trust** errors — in the mechanics of the pipeline. And
they get worse, not better, as an AI agent writes more of the code.

## What oryxflow gives you

- **Reproducibility by default.** Every output is tied to the exact task, parameters, and code
  version that produced it. "Can I reproduce last week's result?" becomes yes, mechanically.
- **Lineage you can query.** oryxflow records what ran, when, with which parameters and code,
  and *why* it recomputed. "Is this stale? Was it built with current code?" are queries, not
  guesses.
- **Reruns exactly what changed.** Change a parameter, a data input, or a task's code and exactly
  the affected outputs rebuild — you can't accidentally evaluate a new model on old features.
- **Speed and cost savings.** Completed steps load from cache instead of recomputing, so the
  edit–run loop drops from minutes to seconds. An AI agent stops paying — in time and tokens — to
  redo expensive work it already did.
- **AI-agent reliability.** The same cache and lineage log become an agent's memory across
  sessions. The companion [Claude Code plugin](claude-plugin/index.md) ships these disciplines
  as an auto-activating skill, so the agent uses the cache correctly instead of trusting stale
  state.

Caching is the *engine*. Trust — reproducible, lineage-tracked reruns that update exactly what
changed — is the *product*.

## When to use oryxflow

Reach for it when the work has a **shape worth keeping** — it will be rerun, depended on, or
swept over parameters:

- Feature-engineering pipelines with expensive intermediate steps.
- Model training and evaluation you iterate on repeatedly.
- Parameter sweeps and experiment matrices (model × features × window).
- Research code that must be reproduced, compared, and handed off.
- Any of the above written with an AI coding agent, where mechanical trust matters most.

## When *not* to use oryxflow

Being honest about fit is part of being trustworthy:

- **Throwaway exploration.** For a five-line "load a CSV, group by, plot one thing" that runs
  once, plain pandas in a notebook is clearer — a task DAG is just ceremony.
- **Production orchestration.** If you need cron-style scheduling, retries across a cluster, and
  SLAs, use [Airflow, Prefect, or Dagster](../blog/index.md). oryxflow is built for the research
  loop, not production ops.
- **Experiment dashboards.** If what you want is a searchable UI of every run's metrics, that's
  an experiment tracker's job (MLflow, Weights & Biases) — and oryxflow composes cleanly beside
  one.

## oryxflow vs MLflow, Airflow, and DVC — which do I need?

oryxflow doesn't replace trackers or orchestrators; it fills the gap between an ad-hoc script
and a heavyweight platform. What's distinctive is the **combination** of local-first
simplicity, automatic *code-aware* invalidation, and always-on lineage.

| | Local, zero-infra | Automatic caching & reruns | Reruns on a **code** change | Queryable lineage | Experiment dashboard | Production scheduling |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **oryxflow** | ✅ | ✅ | ✅ automatic | ✅ | — (use a tracker) | — (use an orchestrator) |
| Notebooks + pickle files | ✅ | ❌ hand-rolled | ❌ | ❌ | ❌ | ❌ |
| MLflow / W&B | partial | ❌ (tracks, doesn't rerun) | ❌ | logs runs | ✅ | ❌ |
| Airflow / Prefect / Dagster | ❌ server/infra | opt-in / configured | ❌ | run history | partial | ✅ |
| DVC | ✅ | ✅ (file-hash stages) | on declared file deps | via Git | ❌ | ❌ |

A few honest specifics:

- **vs notebooks + pickle files** — oryxflow gives you the caching, dependency order, and
  reproducibility you were hand-rolling, without the stale-`.pkl` graveyard.
- **vs MLflow / W&B** — complementary, not competing. Trackers answer "which run scored 0.91?";
  oryxflow answers "which steps do I actually need to rerun to reproduce it, and are they
  stale?" Keep logging to your tracker *inside* oryxflow tasks. See
  [MLflow or pipeline caching?](../blog/posts/mlflow-or-pipeline-caching.md)
- **vs Airflow / Prefect / Dagster** — a different job. Those run scheduled production pipelines
  on real infrastructure; oryxflow is a `pip install` for the local research loop. See
  [oryxflow vs Airflow](../blog/posts/oryxflow-vs-airflow.md).
- **vs DVC** — both cache pipelines. DVC hashes files and YAML-declared stages; oryxflow keeps
  identity in native Python — a parameter change is a new cached identity automatically, and a
  code edit reruns the affected tasks on its own, no config files to maintain.

## What it looks like

```python
import oryxflow
import pandas as pd

oryxflow.set_dir('data/')

class GetData(oryxflow.tasks.TaskPqPandas):        # output saved as parquet — no file paths
    def run(self):
        self.save(pd.DataFrame({'x': range(10)}))

@oryxflow.requires(GetData)                         # declare the dependency
class ProcessData(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()                       # GetData's output, already loaded
        df['x2'] = df['x'] ** 2
        self.save(df)

flow = oryxflow.Workflow(ProcessData)
flow.run()                                          # runs GetData, then ProcessData
df = flow.outputLoad()                              # load the result by name
```

Run `flow.run()` again and nothing recomputes — both outputs already exist. Edit
`ProcessData`'s code and only it (and anything downstream) reruns, automatically. The record of
what ran and why is written to a lineage log you can query later.

## Frequently asked questions

**Is oryxflow an MCP server?**
No. oryxflow ships a Claude Code plugin — a skill plus slash commands — backed by an open-source
Python library. The reproducibility work happens locally in that library, not over MCP.

**Does oryxflow replace MLflow or Airflow?**
No — it composes beside them. oryxflow caches and reruns your local research pipeline; keep using
an experiment tracker for dashboards and an orchestrator for scheduled production jobs.

**How does oryxflow know when to rerun a task?**
From the task's parameters and its code. Change a parameter, a data input, or the code and
oryxflow reruns exactly the affected outputs — cosmetic edits like comments or formatting don't
trigger a rerun.

**Where is my data stored?**
Locally. oryxflow is local-first and zero-infrastructure — no server, no database, no account, no
telemetry. Your code, your cache, your repo.

## Takeaway

- oryxflow makes iterative data analysis **reproducible and lineage-tracked**, with the right
  steps rebuilding automatically — for humans and AI agents.
- It's **local-first and zero-infrastructure**: `pip install oryxflow`, no server or account.
- **Caching is how it works; trust is what you get.** Faster and cheaper reruns come for free.
- It **composes** with the tools you already use — trackers for dashboards, orchestrators for
  production.

Ready to build?

```bash
pip install oryxflow
```

- **[Quickstart](quickstart.md)** — nothing to a running, self-caching pipeline in minutes.
- **[Transition from scripts](transition.md)** — convert an existing analysis.
- **[Build with Claude Code](claude-plugin/index.md)** — let an AI agent scaffold and wire it,
  the trustworthy way.
