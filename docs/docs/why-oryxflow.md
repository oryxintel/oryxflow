---
title: Why oryxflow
description: oryxflow makes AI-driven data analysis faster, cheaper, and more trustworthy — reproducible, lineage-tracked Python pipelines that rerun only what changed, for humans and AI coding agents alike.
faq:
  - q: "Is there a lightweight alternative to Airflow for a data science project?"
    a: "Yes — oryxflow is a local-first Python library built for the research loop rather than production ops: pip install oryxflow, no server, scheduler, database, or account. Airflow's job is running scheduled pipelines on real infrastructure with retries and alerting; oryxflow's job is making one analyst's pipeline fast and trustworthy to iterate on, caching each step and rerunning exactly what a code, data, or parameter change affects. If you're reaching for Airflow only to get dependency order and caching on your laptop, oryxflow is the smaller tool that does that part."
  - q: "Is oryxflow an MLflow alternative?"
    a: "Not a replacement — a complement, and they answer different questions. MLflow tracks and charts experiment runs, so it tells you which run scored 0.91; oryxflow decides what actually has to rerun to reproduce that run and whether its inputs are stale. Keep logging metrics to MLflow or Weights & Biases from inside your oryxflow tasks. If what you wanted from a tracker was really caching and reproducible reruns rather than a dashboard, oryxflow covers that on its own with no server or account."
  - q: "How do I make my data science workflow reproducible?"
    a: "Declare each step of your analysis as an oryxflow task that saves its output, and the pipeline becomes reproducible by construction: every result is addressed by the code and parameters that produced it, each run records what ran and why, and re-running regenerates any result from the recorded inputs. Reproducibility stops depending on remembering which cell you ran in what order."
  - q: "How does oryxflow know when to rerun a task?"
    a: "From the task's parameters and its code. Change a parameter, a data input, or the code and oryxflow reruns exactly the affected outputs — cosmetic edits like comments or formatting don't trigger a rerun."
  - q: "Do I have to manage file names and paths for cached results?"
    a: "No — that bookkeeping is the thing oryxflow takes over. A task saves its output with self.save(df) and you read it back with flow.outputLoad(); oryxflow derives the storage location from the task and its parameters, so there is no features_v3_final.pkl to name, no path constants to thread through your code, and no note about which run used which settings. Change a parameter and you get a separate cached result automatically instead of overwriting the old one, which is why you can sweep an experiment matrix without curating a folder of files by hand."
  - q: "Can I use oryxflow for exploratory data analysis?"
    a: "Yes, and that's the recommended way in: start with plain exploratory scripts, then let the work grow into a pipeline. The Claude Code plugin gives exploration a home — read-only probes under eda/, with findings written up as you go — and when a probe turns out to be load-bearing, /oryxflow:migrate lifts what you already wrote into cached tasks. You get a first look at a dataset without ceremony and a reproducible pipeline once the analysis earns one, with no rewrite in between."
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
- **A folder full of files you have to keep straight.** `features_v3.pkl`,
  `features_v3_final.pkl`, `features_v3_final_FIXED.pkl` — plus the mental note about which
  settings each one used, which nobody wrote down.
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

- **No storage or parameter boilerplate.** You never name a file, build a path, or track which
  settings produced which output. `self.save(df)` puts it away, `flow.outputLoad()` gets it back,
  and oryxflow works out where it lives from the task and its parameters.
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

## No file paths, no parameter bookkeeping

The most immediate change is how much clerical work disappears. Storage and parameter tracking are
the same job, and oryxflow does both for you.

Before, every intermediate is a filename you invent and a decision you have to remember:

<!--phmdoctest-skip-->
```python
# was the 60-day window the one that produced this? and is it still current?
features = build_features(df, window=60)
features.to_pickle('data/features_w60_v3_final.pkl')
...
features = pd.read_pickle('data/features_w60_v3_final.pkl')   # ...probably the right file
```

After, the parameter *is* the identity of the result, and the file is oryxflow's problem:

<!--phmdoctest-skip-->
```python
class Features(oryxflow.tasks.TaskPqPandas):
    window = oryxflow.IntParameter(default=60)

    def run(self):
        self.save(build_features(self.inputLoad(), window=self.window))
```

That one change buys you three things:

- **No result is ever built on stale data.** Ask for `window=60` and you get the output built with
  `window=60` and the current code — not whichever file was written last. There is no way to point
  at the wrong pickle, because you never point at a pickle.
- **No file mess.** No `_v3_final_FIXED` suffixes, no path constants threaded through modules, no
  cleanup of an intermediates folder nobody understands. Change a parameter and you get a *new*
  cached result rather than an overwritten one, so a ten-way sweep needs zero filing.
- **Nothing to keep in your head — or the agent's.** "Which settings made this?" is recorded, not
  remembered. That matters most when an AI coding agent is writing the code, since it's exactly the
  state an agent silently loses over a long session.

## When to use oryxflow — and how to start small

Reach for it when the work has a **shape worth keeping** — it will be rerun, depended on, or
swept over parameters:

- Feature-engineering pipelines with expensive intermediate steps.
- Model training and evaluation you iterate on repeatedly.
- Parameter sweeps and experiment matrices (model × features × window).
- Research code that must be reproduced, compared, and handed off.
- Any of the above written with an AI coding agent, where mechanical trust matters most.

**But you don't have to know that on day one, and you don't have to start there.** Nobody begins a
project by declaring a DAG — you begin by looking at a dataset. So start with plain exploratory
scripts: the [Claude Code plugin](claude-plugin/index.md) gives that stage a home (read-only probes
under `eda/`, each documenting the question it answers and writing findings into the project's data
doc), and when a probe turns out to be load-bearing, `/oryxflow:migrate` lifts what you already
wrote into cached tasks. Simple scripts at the start, any complexity later, with no rewrite and no
cliff in between — which matters because analyses only ever get more complicated.

## What oryxflow is *not*

Being honest about fit is part of being trustworthy. Two jobs are somebody else's, and in both
cases oryxflow is designed to sit *beside* the other tool, not argue with it:

- **Production orchestration.** If you need cron-style scheduling, retries across a cluster, and
  SLAs, use [Airflow, Prefect, or Dagster](../blog/posts/oryxflow-vs-airflow.md). oryxflow is built
  for the research loop, not production ops.
- **Experiment dashboards.** If you want a searchable UI charting every run's metrics, that's an
  experiment tracker's job (MLflow, Weights & Biases). **Experiment tracking and oryxflow are
  complementary, and you should expect to use both** — see below.

## How oryxflow compares to MLflow, Airflow, and DVC

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

### Experiment tracking is complementary — expect to use both

This is the comparison people most often read as either/or, so to be explicit: **an experiment
tracker and oryxflow do different halves of the same project, and using both is the normal setup.**

A tracker is a *record of results*: it collects metrics, params, and artifacts from runs you already
did, and gives you a UI to sort and chart them. oryxflow is the *machinery that produces those
runs*: it decides which steps have to execute, reuses the ones that don't, and guarantees the
features your model just scored on were built by current code. A tracker can't tell you a logged run
was trained on a stale intermediate; oryxflow can't draw you a leaderboard. In practice the tracker
call lives *inside* an oryxflow task, so every logged run is also a cached, reproducible one.

Full treatment, with the integration pattern and when one tool alone is enough:
**[Experiment tracking with oryxflow](experiment-tracking.md)**.

## What it looks like

```python
import oryxflow
import pandas as pd

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

**Is there a lightweight alternative to Airflow for a data science project?**
Yes — oryxflow is a local-first Python library built for the research loop rather than production
ops: `pip install oryxflow`, no server, scheduler, database, or account. Airflow's job is running
scheduled pipelines on real infrastructure with retries and alerting; oryxflow's job is making one
analyst's pipeline fast and trustworthy to iterate on, caching each step and rerunning exactly what a
code, data, or parameter change affects. If you're reaching for Airflow only to get dependency order
and caching on your laptop, oryxflow is the smaller tool that does that part.

**Is oryxflow an MLflow alternative?**
Not a replacement — a complement, and they answer different questions. MLflow tracks and charts
experiment runs, so it tells you which run scored 0.91; oryxflow decides what actually has to rerun
to reproduce that run and whether its inputs are stale. Keep logging metrics to MLflow or Weights &
Biases from inside your oryxflow tasks. If what you wanted from a tracker was really caching and
reproducible reruns rather than a dashboard, oryxflow covers that on its own with no server or
account.

**How do I make my data science workflow reproducible?**
Declare each step of your analysis as an oryxflow task that saves its output, and the pipeline
becomes reproducible by construction: every result is addressed by the code and parameters that
produced it, each run records what ran and why, and re-running regenerates any result from the
recorded inputs. Reproducibility stops depending on remembering which cell you ran in what order.

**How does oryxflow know when to rerun a task?**
From the task's parameters and its code. Change a parameter, a data input, or the code and
oryxflow reruns exactly the affected outputs — cosmetic edits like comments or formatting don't
trigger a rerun.

**Do I have to manage file names and paths for cached results?**
No — that bookkeeping is the thing oryxflow takes over. A task saves its output with
`self.save(df)` and you read it back with `flow.outputLoad()`; oryxflow derives the storage location
from the task and its parameters, so there is no `features_v3_final.pkl` to name, no path constants
to thread through your code, and no note about which run used which settings. Change a parameter and
you get a separate cached result automatically instead of overwriting the old one, which is why you
can sweep an experiment matrix without curating a folder of files by hand.

**Can I use oryxflow for exploratory data analysis?**
Yes, and that's the recommended way in: start with plain exploratory scripts, then let the work grow
into a pipeline. The Claude Code plugin gives exploration a home — read-only probes under `eda/`,
with findings written up as you go — and when a probe turns out to be load-bearing,
`/oryxflow:migrate` lifts what you already wrote into cached tasks. You get a first look at a dataset
without ceremony and a reproducible pipeline once the analysis earns one, with no rewrite in between.

## Takeaway

- oryxflow makes iterative data analysis **trustworthy**: the right steps rebuild automatically, so
  you always get the result your current code and parameters imply — for humans and AI agents.
- **No storage or parameter boilerplate.** No filenames to invent, no paths to thread through, no
  record of which settings produced which output.
- It's **local-first and zero-infrastructure**: `pip install oryxflow`, no server or account.
- **Start small.** A first EDA script is fine; `/oryxflow:migrate` grows it into a pipeline when the
  work earns one.
- It **composes** with the tools you already use — trackers for dashboards, orchestrators for
  production.

Ready to build?

```bash
pip install oryxflow
```

- **[Quickstart](quickstart.md)** — nothing to a running, self-caching pipeline in minutes.
- **[Transition from scripts](transition.md)** — convert an existing analysis.
- **[Migrate a messy notebook project](migrate-notebook-to-pipeline.md)** — take a project that's
  already out of control and make it scalable.
- **[Experiment tracking with oryxflow](experiment-tracking.md)** — how it pairs with MLflow or
  Weights & Biases.
- **[Build with Claude Code](claude-plugin/index.md)** — let an AI agent scaffold and wire it,
  the trustworthy way.
