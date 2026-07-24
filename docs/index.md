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
    a: "Skip it for throwaway exploration — a quick CSV load, a group-by, one plot — where a plain notebook is faster and a task graph is just overhead. oryxflow earns its keep once a project gains depth (a stale early step silently corrupts everything below it), expensive steps (caching makes the inner loop tractable), or experiment matrices. Those are exactly the conditions where hand-managed scripts and AI coding agents tend to go wrong."
---

# oryxflow

**Faster, cheaper, and more trustworthy data analysis — for humans and AI coding agents.**
oryxflow turns a data-science script into a reproducible, lineage-tracked pipeline that reruns
only what changed. It's a Python library with no server, no database, and no account:
`pip install oryxflow` and you're done.

Working with an AI agent? The **[Claude Code plugin](docs/claude-code-for-data-science.md)**
teaches Claude Code to build your data analysis as a cached, reproducible pipeline — so
AI-written analysis is reproducible by default.

You declare each step of your analysis as a **task**: what it depends on and what it produces. The
engine runs them in the right order and skips anything already computed. Change a parameter, the
data, or the code, and it **reruns exactly what that change affects** — then hands you any result
by name.

It also records **what ran, when, and why**, so "is this result stale?", "was it produced by the
current code?", and "did I already run this?" become queries, not guesses. The payoff: outputs
you can **trust** and reproduce, with no wasted recomputation. Sharing a pipeline replaces the
fragile chain of scripts and files you used to manage by hand. Caching is how it works;
**trust is what you get** — [see the full positioning](docs/why-oryxflow.md).

<!--phmdoctest-share-names-->
```python
import oryxflow
import pandas as pd

oryxflow.set_dir('data/')

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
them. That is the core payoff: re-running a pipeline only pays for what actually changed.

## Where to next

<div class="grid cards" markdown>

-   :material-shield-check: **[Why oryxflow](docs/why-oryxflow.md)**

    The positioning in full: reproducibility, lineage, and trustworthy AI data analysis — and
    when *not* to reach for it.

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
Skip it for throwaway exploration — a quick CSV load, a group-by, one plot — where a plain notebook
is faster and a task graph is just overhead. oryxflow earns its keep once a project gains depth (a
stale early step silently corrupts everything below it), expensive steps (caching makes the inner
loop tractable), or experiment matrices. Those are exactly the conditions where hand-managed
scripts and AI coding agents tend to go wrong.

## Learn more

- **Real-life project template:** [d6tflow-template](https://github.com/d6t/d6tflow-template)
- **Why this matters:** [4 Reasons Why Your Machine Learning Code is Probably Bad](https://medium.com/@citynorman/4-reasons-why-your-machine-learning-code-is-probably-bad-c291752e4953)
- **API details:** [API Reference](docs/reference.md)
