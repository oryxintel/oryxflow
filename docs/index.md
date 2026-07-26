---
description: oryxflow makes AI data analysis faster, cheaper, and more trustworthy — a Python library and Claude Code plugin that builds your analysis as a reproducible, cached pipeline that reruns only what changed.
faq:
  - q: "How do I stop rerunning my whole pipeline every time I change one step?"
    a: "oryxflow caches each step's output and reruns only what a code, data, or parameter change affects, plus everything downstream of it. Change one feature and the untouched upstream steps load instantly from cache instead of recomputing. It's a local-first Python library: pip install oryxflow, declare each step as a task, and re-running only pays for what actually changed."
  - q: "How do I cache intermediate DataFrames in Python without brittle pickle files?"
    a: "Declare each step as an oryxflow task that saves its DataFrame, and the engine caches it, addresses it by task identity instead of a hand-managed filename, and reloads it on the next run. You never wire up to_pickle / read_pickle paths or track which file is current — you ask for a result by the task that made it, and stale outputs rerun automatically when the code changes."
  - q: "Is there a lightweight alternative to Airflow or MLflow for a local data science project?"
    a: "oryxflow is a local-first Python workflow library that sits between notebooks and heavyweight orchestrators — no server, scheduler, database, or account. Where Airflow orchestrates production DAGs and MLflow tracks experiments, oryxflow makes one analyst's pipeline reproducible and cached: it reruns only what changed and records what produced each result. Reach for it when a notebook has outgrown itself but Airflow or MLflow would be overkill."
  - q: "How do I run a parameter sweep without rerunning the upstream steps every time?"
    a: "Parameters flow through the task graph, so oryxflow reruns only the tasks a given parameter actually changes and reuses the shared upstream cache across every combination in the sweep. Compare ten model configs and the data-loading and feature steps run once, not ten times. Each run is tagged by its parameters, so results stay reproducible and you can load any combination's output by name."
  - q: "Is there a Claude Code plugin to make AI-generated data analysis reproducible and trustworthy?"
    a: "Yes — the oryxflow Claude Code plugin. It teaches your coding agent to build the analysis as a cached, reproducible pipeline: reusing expensive results, verifying its own reruns, and never training on stale intermediates. oryxflow guarantees a result was produced by the code and inputs it recorded — reproducible, not automatically correct — so you can check AI-written analysis instead of trusting it blindly. It ships as a skill plus slash commands, not an MCP server."
  - q: "When should I not use oryxflow?"
    a: "You don't need a task graph for a first look at a dataset — a quick CSV load, a group-by, one plot is fine as a plain script. You don't have to choose upfront, though: start there and run /oryxflow:migrate when the work gains depth, cost, or parameter combinations, and what you already wrote becomes cached tasks. oryxflow earns its keep the moment a stale early step can silently corrupt everything below it, an expensive step makes the edit-run loop painful, or you're sweeping an experiment matrix — which is also where hand-managed scripts and AI coding agents go wrong. It isn't a production orchestrator (use Airflow or Prefect) or an experiment dashboard (use MLflow or Weights & Biases); it composes beside both."
---

# oryxflow

**Faster, cheaper, and more trustworthy data analysis — for humans and AI coding agents.**
oryxflow turns a data-science script into a pipeline that caches every step, reruns exactly what
a change affects, and records how each result was made. It's a Python library with no server, no
database, and no account: `pip install oryxflow` and you're done.

Working with an AI agent? The **[Claude Code plugin](docs/claude-code-for-data-science.md)**
teaches Claude Code to build your data analysis this way — so the agent reuses expensive results
instead of burning your time and tokens redoing them, and never trains a model on stale data. You
get analysis you can **trust**, not just analysis that runs. Using a different agent? The docs are
[machine-readable too](docs/ai-ready.md) — point it at
[`llms-full.txt`](https://docs.oryxflow.dev/llms-full.txt) and it has read all of oryxflow in one
request.

## Why oryxflow

Four things you get on day one — the full argument is in
**[Why oryxflow](docs/why-oryxflow.md)**:

- **You always get the right result.** Change a parameter, the data, or a task's code and oryxflow
  reruns exactly what that change affects. You can't accidentally evaluate a new model on stale
  features.
- **No file mess, no parameter bookkeeping.** You never name, path, or version an intermediate file
  again — no `features_v3_final.pkl`, no `to_pickle`/`read_pickle` plumbing, no spreadsheet of which
  run used which settings. Ask for a result by the task and parameters that made it.
- **Seconds instead of minutes.** Finished steps load from cache, so the 10-minute data pull runs
  once, not once per edit.
- **An answer to "is this stale?"** oryxflow records what ran, when, with which code and inputs, and
  why it recomputed — so staleness and provenance are queries, not guesses.

**Start small; it scales with you.** A first pass can be a plain script or a quick exploratory
probe — you don't need a task graph on day one. As the work gains steps, cost, and parameter
combinations (as it always does), the plugin's `/oryxflow:migrate` command lifts what you already
wrote into cached tasks. No rewrite, and no cliff between "exploring" and "pipeline" —
[migrate a messy project](docs/migrate-notebook-to-pipeline.md).

## oryxflow in brief

You declare each step of your analysis as a **task**: what it depends on and what it produces. The
engine runs them in dependency order, skips anything already computed, and hands you any result by
name.

<!--phmdoctest-share-names-->
```python
import oryxflow
import pandas as pd

class GetData(oryxflow.tasks.TaskPqPandas):        # output saved as parquet
    def run(self):
        self.save(pd.DataFrame({'x': range(10)}))

@oryxflow.requires(GetData)                        # declare the dependency
class ProcessData(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()                      # GetData's output, already loaded
        df['x2'] = df['x'] ** 2
        self.save(df)

flow = oryxflow.Workflow(ProcessData)
flow.run()                                         # runs GetData, then ProcessData
df = flow.outputLoad()                             # load the result by name
```

Run `flow.run()` again and nothing happens — both outputs already exist, so the engine skips
them. That is the core payoff: re-running a pipeline only pays for what actually changed. Caching is
how it works; **trust is what you get**. Next step:
**[Quickstart](docs/quickstart.md)** — a real pipeline in a few minutes.

## How oryxflow compares to Airflow, MLflow, DVC, and notebooks

oryxflow doesn't replace an experiment tracker or a production orchestrator — it fills the gap
between an ad-hoc script and a heavyweight platform, and composes with both. What's distinctive is
the combination of local-first simplicity, invalidation that notices a **code** change, and
always-on lineage.

| | Local, zero-infra | Automatic caching & reruns | Reruns on a **code** change | Queryable lineage | Experiment dashboard | Production scheduling |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **oryxflow** | ✅ | ✅ | ✅ automatic | ✅ | — (use a tracker) | — (use an orchestrator) |
| Notebooks + pickle files | ✅ | ❌ hand-rolled | ❌ | ❌ | ❌ | ❌ |
| MLflow / W&B | partial | ❌ (tracks, doesn't rerun) | ❌ | logs runs | ✅ | ❌ |
| Airflow / Prefect / Dagster | ❌ server/infra | opt-in / configured | ❌ | run history | partial | ✅ |
| DVC | ✅ | ✅ (file-hash stages) | on declared file deps | via Git | ❌ | ❌ |

The per-tool detail is in
[Why oryxflow](docs/why-oryxflow.md#how-oryxflow-compares-to-mlflow-airflow-and-dvc), and the
head-to-heads are on the blog: [vs Airflow](blog/posts/oryxflow-vs-airflow.md),
[vs MLflow](blog/posts/mlflow-alternatives.md), [vs DVC](blog/posts/oryxflow-vs-dvc.md),
[vs Dagster](blog/posts/oryxflow-vs-dagster.md), [vs Prefect](blog/posts/oryxflow-vs-prefect.md),
and [the whole field](blog/posts/oryxflow-vs-the-field.md).

## Where to next

<div class="grid cards" markdown>

-   :material-shield-check: **[Why oryxflow](docs/why-oryxflow.md)**

    The argument in full: trustworthy AI data analysis, no file mess, honest tool comparisons —
    and how it scales from a first EDA script upward.

-   :material-download: **[Installation](docs/installation.md)**

    Install oryxflow and its optional extras (cloud storage, export, dask).

-   :material-rocket-launch: **[Quickstart](docs/quickstart.md)**

    From nothing to a running, self-caching pipeline in a few minutes.

-   :material-book-open-variant: **[Documentation](docs/index.md)**

    The full guide: tasks, workflows, parameters, I/O formats, and logging.

-   :material-robot: **[Build with Claude Code](docs/claude-plugin/index.md)**

    The official plugin makes AI-written data analysis trustworthy — it scaffolds the project,
    wires the DAG, and teaches the agent to use the cache correctly.

-   :material-sitemap: **[Managing complex workflows](docs/managing-workflows.md)**

    Automatic code invalidation, selective resets, and multi-experiment flows.

-   :material-broom: **[Migrate a messy notebook project](docs/migrate-notebook-to-pipeline.md)**

    Nine notebooks and a folder of `clean_v3.csv`? Restructure it so a wrong number stops being
    possible — by hand, or in one command.

-   :material-post: **[Blog](blog/index.md)**

    Reproducibility and trust, tool comparisons (vs Airflow, MLflow, DVC), and trustworthy
    AI-assisted data science.

</div>

## Frequently asked questions

**How do I stop rerunning my whole pipeline every time I change one step?**
oryxflow caches each step's output and reruns only what a code, data, or parameter change affects,
plus everything downstream of it. Change one feature and the untouched upstream steps load
instantly from cache instead of recomputing. It's a local-first Python library:
`pip install oryxflow`, declare each step as a task, and re-running only pays for what actually
changed.

**How do I cache intermediate DataFrames in Python without brittle pickle files?**
Declare each step as an oryxflow task that saves its DataFrame, and the engine caches it,
addresses it by task identity instead of a hand-managed filename, and reloads it on the next run.
You never wire up `to_pickle` / `read_pickle` paths or track which file is current — you ask for a
result by the task that made it, and stale outputs rerun automatically when the code changes.

**Is there a lightweight alternative to Airflow or MLflow for a local data science project?**
oryxflow is a local-first Python workflow library that sits between notebooks and heavyweight
orchestrators — no server, scheduler, database, or account. Where Airflow orchestrates production
DAGs and MLflow tracks experiments, oryxflow makes one analyst's pipeline reproducible and cached:
it reruns only what changed and records what produced each result. Reach for it when a notebook has
outgrown itself but Airflow or MLflow would be overkill.

**How do I run a parameter sweep without rerunning the upstream steps every time?**
Parameters flow through the task graph, so oryxflow reruns only the tasks a given parameter
actually changes and reuses the shared upstream cache across every combination in the sweep.
Compare ten model configs and the data-loading and feature steps run once, not ten times. Each run
is tagged by its parameters, so results stay reproducible and you can load any combination's output
by name.

**Is there a Claude Code plugin to make AI-generated data analysis reproducible and trustworthy?**
Yes — the oryxflow Claude Code plugin. It teaches your coding agent to build the analysis as a
cached, reproducible pipeline: reusing expensive results, verifying its own reruns, and never
training on stale intermediates. oryxflow guarantees a result was produced by the code and inputs
it recorded — reproducible, not automatically *correct* — so you can check AI-written analysis
instead of trusting it blindly. It ships as a skill plus slash commands, not an MCP server.

**When should I not use oryxflow?**
You don't need a task graph for a first look at a dataset — a quick CSV load, a group-by, one plot
is fine as a plain script. You don't have to choose upfront, though: start there and run
`/oryxflow:migrate` when the work gains depth, cost, or parameter combinations, and what you already
wrote becomes cached tasks. oryxflow earns its keep the moment a stale early step can silently
corrupt everything below it, an expensive step makes the edit-run loop painful, or you're sweeping an
experiment matrix — which is also where hand-managed scripts and AI coding agents go wrong. It isn't
a production orchestrator (use Airflow or Prefect) or an experiment dashboard (use MLflow or
Weights & Biases); it composes beside both.

## Learn more

- **Scaffold a real project:** [`/oryxflow:init-project`](docs/claude-plugin/commands.md) in Claude Code
- **Why this matters:** [4 reasons your machine learning code is probably bad](blog/posts/4-reasons-your-ml-code-is-bad.md)
- **API details:** [API Reference](docs/reference.md)
