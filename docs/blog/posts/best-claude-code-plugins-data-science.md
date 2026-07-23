---
date: 2026-07-23
slug: best-claude-code-plugins-for-data-science
categories:
  - AI agents
description: A practical roundup of the best Claude Code plugins and tools for data science, chosen by the job each one does — reproducibility, data access, notebooks, and experiment tracking.
---

# The best Claude Code plugins and tools for data science

*The landscape is early, so stop shopping for a "best plugin" and start choosing by the
job you need done — the one below keeps AI-generated analysis reproducible.*

<!-- more -->

If you let Claude Code loose on a data-science project, the first thing you notice is how
much it can do in one session: pull data, write features, fit a model, plot the result. The
second thing you notice is how easily it loses the thread. Across a few turns the agent
forgets which steps already ran, re-executes a ten-minute feature build for no reason, or —
worse — edits an upstream step and evaluates a model on the *old* output without realizing
anything went stale. The code looks fine. The result is quietly wrong.

That is the real problem a good Claude Code setup for data science has to solve. Not "can
the agent write pandas" — it can — but **can you trust what it produced, and could you
reproduce it tomorrow?** Agents are stateless between turns; your pipeline is stateful. The
tools that matter are the ones that close that gap.

## How we picked

There is no crowded, mature market of data-science Claude Code plugins yet, so we're not
ranking twenty near-identical products. We're grouping the genuinely useful options **by
the job they do** and judging each on four things that actually decide whether AI-assisted
analysis holds up:

- **Reproducibility** — can you (or a teammate, or the agent next week) recreate a result
  exactly, and know what it was built from?
- **State management across turns** — does the tool help the agent track what's already
  computed and what went stale, so it doesn't build on outdated work?
- **Local-first and private** — does your data and lineage stay on your machine, with no
  telemetry or mandatory cloud service?
- **Does the job it claims** — honest scope. A tool that connects to your warehouse is not
  pretending to make your analysis reproducible, and vice versa.

A quick word on honesty: this space moves fast and many "plugins" are really MCP servers
(Model Context Protocol connectors) or thin wrappers. Where we're confident a specific tool
exists, we name it. Where we aren't, we describe the **category** so you can search for the
current best option yourself rather than trust a made-up product name.

## Building reproducible pipelines — the oryxflow Claude Code plugin

This is the job most AI-assisted data-science setups get wrong, and it's the one
[oryxflow](https://github.com/oryxintel/oryxflow) is built for. oryxflow is a small,
local-first Python library that turns ordinary scripts and notebooks into a **cached,
dependency-aware task graph**: you declare tasks with parameters and `requires()`
dependencies, and the engine runs them in order, skipping any whose output already exists
and rerunning exactly the ones affected by a change.

The companion [Claude Code plugin](https://github.com/oryxintel/oryxflow-claude-plugin)
is what makes an agent good at using that model. It's a **skill plus a handful of slash
commands** that activates automatically when you're working in an oryxflow project. It
front-loads the correct idioms so the agent reuses cached work instead of recomputing,
verifies that an edit actually reran the tasks it should have, and doesn't build on stale
data. In practice that means:

- **`/oryxflow:init-project`** scaffolds a ready-to-run project structure.
- **`/oryxflow:migrate`** restructures a messy notebook or linear script into cached,
  parameterized tasks **one step at a time**, so you always have a working pipeline.
- **`/oryxflow:check-standards`** keeps names, style, and docstrings consistent.
- **`/oryxflow:init-gitlfs`** and **`/oryxflow:update-project`** handle data versioning and
  keeping an older project current.

Why it's the strongest fit for *this* job: reproducibility is a property of your
**computation graph**, and oryxflow owns that graph — parameters and code changes become
new cached identities automatically, so you can't accidentally evaluate a new model on old
features. It's local-first with no telemetry; your data and lineage stay on your machine.

One honest limit, stated plainly: **oryxflow makes analysis reproducible, not correct.** It
guarantees you can recreate a result and that the result was built from current inputs. It
does not check that your feature logic is sound or your metric is the right one — that's
still your job. What it removes is the whole class of "was this trained on the new data?"
uncertainty that makes agent-generated pipelines untrustworthy. See
[Why library + plugin is a matched pair](../../docs/claude-plugin/why.md) and
[why oryxflow](../../docs/why-oryxflow.md) for the fuller argument.

## Connecting to your data — MCP data connectors

Before you can build a pipeline you have to reach the data, and this is where the broader
**MCP (Model Context Protocol) ecosystem** shines. There are MCP servers for **databases and
warehouses**, **filesystems**, and **APIs**, letting Claude Code query a table or read a
file directly as part of a turn. If your bottleneck is "the agent can't see my data," a data
connector is the right tool — and it's a genuinely different job from making the resulting
analysis reproducible.

The pairing is natural: use an MCP connector to *reach* the raw data, then wrap the pull in
an oryxflow task so the fetched result is cached and lineage-tracked rather than re-queried
on every turn. Local-first varies by connector — a filesystem server is fully local, a
managed-warehouse one obviously isn't — so pick per your privacy needs.

## Notebooks and code execution — execution tools

A lot of data-science work still lives in notebooks, and there are **notebook and code-
execution tools** — including generic Jupyter/notebook MCP servers — that let an agent run
cells and read back outputs interactively. These are great for exploration and for the
messy, visual first pass where you don't yet know what the pipeline should be.

Their honest limitation is the same statelessness problem we opened with: a notebook is a
pile of cells with hidden execution order, and an agent iterating in one has no built-in
notion of what's stale. That's exactly why the common path is **explore in a notebook, then
graduate the keeper steps into a cached pipeline** — the subject of
[From notebook to a reproducible, cached pipeline](notebook-to-pipeline.md).

## Experiment tracking — MLflow (including its experimental MCP)

Once you're producing results worth comparing, you want a **tracker**. **MLflow** is the
best-known, and it now ships an **official (experimental) MCP server** that lets an agent
query your logged runs, parameters, and metrics conversationally.

This is a **complementary** job, not a competing one. Tracking answers *"which run scored
0.91, and what were its hyperparameters?"* — a logging and comparison problem.
Reproducibility answers *"which steps do I need to rerun to recreate that run, and which are
already computed?"* — a computation problem. A tracker will faithfully log a score without
any idea whether the features feeding it are stale. Put your tracker calls **inside** your
cached tasks and you get both: a reproducible graph and a searchable record. We go deeper in
[MLflow or pipeline caching?](mlflow-or-pipeline-caching.md).

## At a glance

| Tool / category | Job it does | Local-first? | Plugin or MCP? |
| --- | --- | --- | --- |
| **oryxflow plugin** | Reproducible, cached, lineage-tracked pipelines the agent can iterate on | Yes, no telemetry | Claude Code plugin (skill + commands) |
| **Database / warehouse connectors** | Let the agent query your data | Varies by connector | MCP |
| **Filesystem / API connectors** | Reach files and external APIs | Filesystem: yes; API: varies | MCP |
| **Notebook / execution tools** | Run cells, interactive exploration | Usually yes (local kernel) | MCP / tool |
| **MLflow (incl. experimental MCP)** | Track and compare runs and metrics | Self-host: yes | Library + experimental MCP |

## FAQ

### Is the oryxflow plugin an MCP server?

No. The oryxflow plugin is a **Claude Code plugin** — a skill plus slash commands — not an
MCP server. It doesn't run as a separate connector process; it activates inside your Claude
Code session when you're working in an oryxflow project and shapes how the agent writes and
runs tasks. MCP connectors (for databases, files, APIs) solve a different job — reaching
data — and the two compose well together.

### What's the best plugin for keeping AI-generated pipelines reproducible?

If the specific worry is that an agent will build on stale data or produce a result you
can't recreate, the oryxflow plugin is the strongest fit, because it's backed by a caching
task-graph engine that makes reproducibility a structural property rather than a discipline
you have to remember. Connectors and notebook tools are excellent at their jobs — getting
data in and exploring it — but they don't own your computation graph, so they can't
guarantee that the result you're looking at was built from current inputs.

### Do I have to choose one tool?

No, and you probably shouldn't. The categories map to different jobs: a connector to reach
data, a notebook tool to explore, a caching engine to make the pipeline reproducible, a
tracker to compare runs. The pattern that works is to reach data with a connector, cache and
wire the steps with oryxflow, and log the outcomes to your tracker from inside the tasks.

## Takeaway

The Claude Code data-science plugin landscape is young, so the winning move isn't to find
the one blessed plugin — it's to pick by the **job** in front of you. For reaching data, use
an MCP connector; for exploring, a notebook tool; for comparing runs, a tracker like MLflow.
And for the job that decides whether any of it is trustworthy — reproducible, lineage-
tracked pipelines an agent can iterate on without building on stale work — the oryxflow
plugin is the layer that makes the other tools' output something you can stand behind. It
won't tell you your analysis is *correct*; it will guarantee it's *reproducible*, which is
the half agents keep getting wrong.

```bash
pip install oryxflow
```

**Read next:** [The caching DAG that keeps AI agents honest](caching-dag-for-ai-agents.md) ·
[When not to use oryxflow](when-not-to-use-oryxflow.md) ·
[From notebook to a reproducible, cached pipeline](notebook-to-pipeline.md) ·
[Plugin commands](../../docs/claude-plugin/commands.md) · Plugin repo:
<https://github.com/oryxintel/oryxflow-claude-plugin>
