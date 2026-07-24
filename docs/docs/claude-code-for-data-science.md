---
title: Claude Code for data science
description: The oryxflow plugin turns Claude Code into a data-science agent whose analysis is reproducible by default — it caches your pipeline, reruns only what changed, and never builds on stale data. What Claude Code plugins and skills for data science do, and how to make AI data analysis you can trust.
faq:
  - q: "Does oryxflow work with any AI coding agent, or only Claude Code?"
    a: "The library is a plain Python package — it works no matter who writes the code, including you by hand. The plugin packages the disciplines specifically for Claude Code; other agents can follow the same CLAUDE.md conventions manually."
  - q: "How do I stop Claude Code from rerunning expensive steps or building on stale data?"
    a: "Install the oryxflow Claude Code plugin. It teaches the agent to cache every step, verify after each edit that the right tasks actually reran, and answer staleness warnings instead of ignoring them — so it reuses expensive results and never trains on stale intermediates. The library externalizes the 'did I already run this, is it still valid?' state the agent can't reliably hold across a long session."
  - q: "Is there a Claude Code skill or plugin for data science?"
    a: "Yes — oryxflow ships an official Claude Code plugin for data science: a skill plus slash commands that make an AI agent's analysis reproducible and cached by default. The skill auto-activates in an oryxflow project and applies data-science conventions as the agent writes; the slash commands scaffold a project, migrate an existing notebook, and check standards. It runs on a local Python library — no server or account, and not an MCP server."
  - q: "Do I have to restructure my project to use it?"
    a: "No — adopt it one task at a time. Point the agent at an existing script with /oryxflow:migrate, or start fresh with /oryxflow:init-project."
---

# Claude Code for data science: plugins and skills for reproducible AI data analysis

**oryxflow makes AI data analysis faster, cheaper, and more trustworthy.** It's a Claude Code
plugin, backed by a Python library, that teaches your coding agent to build the work as a
reproducible pipeline. The agent reuses expensive results instead of recomputing them — and never
trains a model on stale data.

If you only remember one thing: AI writes the analysis fast, but the hard part — *is it
reproducible, and did the right data produce this result?* — is exactly what a plugin can enforce
and a raw agent can't.

## What are Claude Code plugins and skills for data science?

Claude Code plugins extend the agent with new abilities; **skills** are the part that teaches it
*how to work* — conventions and procedures that load into context automatically when they're
relevant. For data science, the useful plugins fall into a few jobs: connecting to your data,
running notebooks, scaffolding a project, and — the one most tools skip — keeping the analysis
**reproducible** as the agent iterates.

The oryxflow plugin owns that last job. It installs an `oryxflow` skill that activates whenever
you work in a data-science pipeline, plus slash commands to scaffold and migrate projects. The
skill makes the agent a *disciplined* user of a cache and a lineage log: it checks what's already
computed before recomputing, verifies its own edits actually took effect, and records what ran
and why. That's the difference between an agent that writes plausible pandas and one whose output
you can reproduce next week.

To be precise about what it is: oryxflow ships a **Claude Code plugin (a skill plus slash
commands)** — not an MCP server. It drives the open-source, MIT-licensed oryxflow library, which
does the actual caching and lineage on your machine.

## What's the difference between a Claude Code plugin and a skill?

They're nested, not competing. A **plugin** is the installable package you add to Claude Code; a
**skill** is one thing a plugin can contain — a bundle of instructions and conventions the agent
loads *on its own* when the context matches, without you invoking anything. A plugin can also ship
slash commands, hooks, and other pieces.

The oryxflow plugin ships both: the `oryxflow` **skill** (the reproducibility conventions, which
auto-activate when you edit pipeline files) and a handful of **slash commands** you call
explicitly — [`/oryxflow:init-project`](claude-plugin/commands.md) to scaffold, `/oryxflow:migrate`
to convert an existing script, and a few more. In practice you install the plugin once, and from
then on the skill just *works* in the background while you describe the analysis you want.

## The problem: AI writes data analysis fast — but is it reproducible?

A coding agent's weakness in data work isn't syntax; it's **invisible state**. Over a long
session it loses track of what's already computed and whether it's still valid, then quietly
builds on stale intermediates or re-runs a 40-minute job it didn't need to. Nothing errors. The
number is just wrong, or the run just cost you ten minutes and a pile of tokens.

These are trust failures, and they get *worse* as the agent writes more of the code:

- **Stale intermediates** — a feature changes, a cached file doesn't, and the model trains on
  yesterday's data.
- **Lost lineage** — no one can say which code and inputs produced `model_final_v3.pkl`.
- **Wasted recomputation** — a one-line downstream edit re-runs the expensive data pull.

None of these are math errors. They're mechanics-of-the-pipeline errors — and they're the ones an
agent introduces most.

## How oryxflow makes Claude Code analysis reproducible

The library carries the discipline the agent can't hold in its head, and the skill makes the
agent *use* it correctly. Concretely, the plugin has the agent:

- **start every session by reading cache state** — pending staleness warnings, last runs, recent
  failures — so it never assumes a stale result is fresh;
- **verify after each edit that the intended steps actually reran**, so a change that should have
  invalidated downstream work can't pass silently;
- **answer every staleness or expensive-recompute warning with the right move** — recompute,
  accept an output-equivalent refactor, or pin — instead of guessing;
- **record decision-relevant results as lineage**, so they become the agent's memory across
  sessions.

Underneath, the library gives each step a reproducible identity from its parameters and its code,
caches its output, and reruns exactly what a parameter, data, or **code** change affects. That
delivers the brand promise mechanically: **faster, cheaper, and more trustworthy AI data
analysis** — reproducible and lineage-tracked by default. See [Why oryxflow](why-oryxflow.md) for the full picture.

## Install

```text
/plugin marketplace add https://github.com/oryxintel/oryxflow-claude-plugin.git
/plugin install oryxflow@oryxflow
```

Once installed, the `oryxflow` skill **auto-activates** whenever you work in an oryxflow project —
you don't invoke it, it's just on. If you install it into an empty directory and nothing happens,
run [`/oryxflow:init-project`](claude-plugin/commands.md) first (or just ask the agent to set up a
project) so there's a pipeline for the skill to work on. Full walkthrough:
[Build with Claude Code](claude-plugin/index.md).

## When to use it — and when not to

Being honest about fit is part of being trustworthy.

**Use it when** the analysis has a shape worth keeping — it'll be rerun, depended on, or swept
over parameters, and especially when an AI agent is writing much of the code:

- feature engineering with expensive intermediate steps,
- model training and evaluation you iterate on,
- parameter sweeps and experiment matrices,
- research code that must be reproduced, compared, and handed off.

**Reach for something else when:**

- it's a five-line, run-once exploration — plain pandas in a notebook is clearer;
- you need production scheduling, retries, and SLAs — that's [Airflow, Prefect, or
  Dagster](../blog/posts/oryxflow-vs-airflow.md)'s job, not oryxflow's;
- you want a searchable dashboard of every run's metrics — that's an experiment tracker
  (MLflow, Weights & Biases), which oryxflow composes cleanly beside.

## How it compares to other Claude Code data-science tools

The plugin landscape is early, so choose by the **job you need done**, not by a "best plugin"
label. Most tools cover data access or notebook execution; oryxflow covers the reproducibility
layer that keeps AI-generated analysis trustworthy as it grows.

| Job to be done | Reach for |
| --- | --- |
| Keep AI analysis reproducible, cached, lineage-tracked | **oryxflow plugin** |
| Query a warehouse / connect a data source | a data-connector plugin |
| Run and edit notebooks | a notebook plugin |
| Track and chart experiment metrics in a UI | MLflow / Weights & Biases |

These aren't mutually exclusive — oryxflow sits *underneath* the analysis and composes with a
tracker or a data connector. For the full breakdown, see
[The best Claude Code plugins and tools for data science](../blog/posts/best-claude-code-plugins-data-science.md).

## What it looks like

You describe the analysis; the agent writes tasks like these, and the engine handles caching and
reruns:

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
```

Run `flow.run()` again and nothing recomputes — both outputs already exist. Edit `ProcessData`'s
code and only it (and anything downstream) reruns, automatically. That's what the agent is taught
to rely on and verify.

## Frequently asked questions

**Does oryxflow work with any AI coding agent, or only Claude Code?**
The library is a plain Python package — it works no matter who writes the code, including you by
hand. The *plugin* packages the disciplines specifically for Claude Code; other agents can follow
the same [CLAUDE.md conventions](claude-plugin/index.md) manually.

**How do I stop Claude Code from rerunning expensive steps or building on stale data?**
Install the oryxflow Claude Code plugin. It teaches the agent to cache every step, verify after
each edit that the right tasks actually reran, and answer staleness warnings instead of ignoring
them — so it reuses expensive results and never trains on stale intermediates. The library
externalizes the "did I already run this, is it still valid?" state the agent can't reliably hold
across a long session.

**Is there a Claude Code skill or plugin for data science?**
Yes — oryxflow ships an official Claude Code **plugin (skill + slash commands)** for data science
that makes an AI agent's analysis reproducible and cached by default. The skill auto-activates in
an oryxflow project and applies data-science conventions as the agent writes; the slash commands
scaffold a project, migrate an existing notebook, and check standards. It runs on a local Python
library — no server or account, and not an MCP server.

**Do I have to restructure my project to use it?**
No — adopt it one task at a time. Point the agent at an existing script with `/oryxflow:migrate`,
or start fresh with `/oryxflow:init-project`.

## Takeaway

- Claude Code writes data analysis fast; **oryxflow makes that analysis reproducible** — cached,
  lineage-tracked, and reproducible-by-default, so a code, data, or parameter change reruns exactly
  what it affects.
- It's a **plugin (skill + slash commands)**, not an MCP server, driving a local, MIT-licensed
  library — no server, no account, no telemetry.
- **Faster, cheaper, and more trustworthy AI data analysis**: the agent reuses expensive work,
  verifies its own edits, and leaves a record of what ran and why.

Ready to build?

```text
/plugin marketplace add https://github.com/oryxintel/oryxflow-claude-plugin.git
/plugin install oryxflow@oryxflow
```

- **[Build with Claude Code](claude-plugin/index.md)** — the full plugin section: commands,
  trust model, project structure.
- **[Why oryxflow](why-oryxflow.md)** — the positioning and how the library works.
- **[Quickstart](quickstart.md)** — from nothing to a self-caching pipeline in minutes.
