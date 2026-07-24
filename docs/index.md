---
description: oryxflow makes AI data analysis faster, cheaper, and more trustworthy — a Python library and Claude Code plugin that builds your analysis as a reproducible, cached pipeline that reruns only what changed.
faq:
  - q: "Is oryxflow an MCP server?"
    a: "No. oryxflow ships a Claude Code plugin — a skill plus slash commands — backed by an open-source Python library. The reproducibility work happens locally in that library, not over MCP."
  - q: "Do I need an AI coding agent to use it?"
    a: "No. oryxflow is a plain Python library: pip install oryxflow and use it by hand. The Claude Code plugin is optional — it teaches an AI agent to use that same library correctly."
  - q: "Where does my data go?"
    a: "Nowhere. oryxflow is local-first and zero-infrastructure — no server, no database, no account, no telemetry. Your code, your cache, your repo."
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

## Common questions

**Is oryxflow an MCP server?**
No. oryxflow ships a Claude Code plugin — a skill plus slash commands — backed by an open-source
Python library. The reproducibility work happens locally in that library, not over MCP.

**Do I need an AI coding agent to use it?**
No. oryxflow is a plain Python library: `pip install oryxflow` and use it by hand. The Claude Code
plugin is optional — it teaches an AI agent to use that same library correctly.

**Where does my data go?**
Nowhere. oryxflow is local-first and zero-infrastructure — no server, no database, no account, no
telemetry. Your code, your cache, your repo.

## Learn more

- **Real-life project template:** [d6tflow-template](https://github.com/d6t/d6tflow-template)
- **Why this matters:** [4 Reasons Why Your Machine Learning Code is Probably Bad](https://medium.com/@citynorman/4-reasons-why-your-machine-learning-code-is-probably-bad-c291752e4953)
- **API details:** [API Reference](docs/reference.md)
