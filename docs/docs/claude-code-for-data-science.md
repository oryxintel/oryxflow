---
title: Claude Code for data science
description: The oryxflow plugin makes AI data analysis trustworthy and reproducible — a Claude Code plugin and skill for data science that stops the agent building on stale data and records exactly what produced every result.
faq:
  - q: "Does oryxflow work with any AI coding agent, or only Claude Code?"
    a: "The library is a plain Python package — it works no matter who writes the code, including you by hand. The plugin packages the disciplines specifically for Claude Code; other agents can follow the same CLAUDE.md conventions manually."
  - q: "How do I stop Claude Code from building on stale data or rerunning expensive steps?"
    a: "Install the oryxflow Claude Code plugin. It teaches the agent to verify after each edit that the right steps actually reran, and to answer staleness warnings instead of ignoring them — so it never trains on stale intermediates, and every result stays tied to the code and data that produced it. Because finished steps are reused rather than recomputed, it also stops paying twice for the expensive ones. The library externalizes the 'did I already run this, is it still valid?' state the agent can't reliably hold across a long session."
  - q: "Is there a Claude Code skill or plugin for data science?"
    a: "Yes — oryxflow ships an official Claude Code plugin (skill + slash commands) for data science that makes an AI agent's analysis trustworthy and reproducible by default. The skill auto-activates in an oryxflow project and applies data-science conventions as the agent writes; the slash commands scaffold a project, migrate an existing notebook, and check standards. It runs on a local Python library — no server or account, and not an MCP server."
  - q: "Do I have to restructure my project to use it?"
    a: "No — adopt it one task at a time. Point the agent at an existing script with /oryxflow:migrate, or start fresh with /oryxflow:init-project."
  - q: "Is the oryxflow plugin overkill for a quick exploratory analysis?"
    a: "No, because it covers exploration too rather than demanding a pipeline upfront. Ask the agent to look at a new dataset and it writes a re-runnable read-only probe under eda/, documenting the question and recording anything it learns, instead of scattering one-off snippets. When a probe turns out to be load-bearing, /oryxflow:migrate lifts it into cached tasks. So you start with simple scripts and scale to any complexity later, with no rewrite in between."
---

# Claude Code for data science: trustworthy, reproducible AI data analysis

**oryxflow makes AI data analysis trustworthy and reproducible.** It's a Claude Code plugin and
skill for data science, backed by a Python library, that teaches your coding agent to build the
work as a pipeline instead of a pile of scripts. Three things follow, in the order they matter:

- **It stops being confidently wrong.** The agent can't quietly train a model on last week's
  features, because a change to the data, a parameter, or the code reruns exactly what that change
  affects — and the skill checks the rerun actually happened. You get the number your current code
  actually implies.
- **It stays reproducible.** Every result is tied to the code and inputs that produced it, in a
  record you can query months later. "Where did this number come from, and can I get it again?"
  has an answer that doesn't depend on anyone's memory of the session.
- **And it costs less, not more.** Reproducibility is normally a tax you pay in rerun time. Here
  it isn't: finished work is reused rather than recomputed, so you're not waiting on the 10-minute
  data pull again or spending tokens on an agent watching it run.

If you only remember one thing: AI writes the analysis fast, but the hard part — *did the right data
produce this result, and can I check that?* — is exactly what a plugin can enforce and a raw agent
can't.

## What are Claude Code plugins and skills for data science?

Claude Code plugins extend the agent with new abilities; **skills** are the part that teaches it
*how to work* — conventions and procedures that load into context automatically when they're
relevant. (For the precise distinction, see
[the glossary](glossary.md#what-is-the-difference-between-a-claude-code-plugin-a-skill-and-a-slash-command).)
For data science, the useful plugins fall into a few jobs: connecting to your data, running
notebooks, scaffolding a project, and — the one most tools skip — making sure the analysis is still
**accurate** ten iterations in, when the agent has edited half the pipeline and nothing has raised an
error.

The oryxflow plugin owns that last job. It installs an `oryxflow` skill that activates whenever
you work in a data-science pipeline, plus slash commands to scaffold and migrate projects. The
skill makes the agent a *disciplined* user of a cache and a lineage log: it checks what's already
computed before recomputing, verifies its own edits actually took effect, and records what ran
and why. That's the difference between an agent that writes plausible pandas and one whose numbers
you can stand behind — and reproduce next week.

To be precise about what it is: oryxflow ships a **Claude Code plugin (a skill plus slash
commands)** — not an MCP server. It drives the open-source, MIT-licensed oryxflow library, which
does the actual caching and lineage on your machine.

## The problem: AI writes data analysis fast — but is the number right?

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

## How oryxflow keeps Claude Code's data analysis accurate

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

Underneath, the library gives each step an identity derived from its parameters and its code and
reruns exactly what a parameter, data, or **code** change affects. The consequence that matters:
**the agent can't hand you a number built from stale inputs**, and every result it does hand you
comes with a record of what made it — so you can *check* the agent instead of taking its word.
The caching is the mechanism, not the point: it's what stops all of that from costing you a
rerun every time you want to be sure. See [Why oryxflow](why-oryxflow.md) for the full picture.

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

## Start with a quick EDA — it scales from there

You don't need a pipeline to justify installing this, and the plugin won't make you build one before
you've looked at the data. It covers both ends of a project's life:

- **Exploring.** Ask the agent to poke at a new dataset and it writes a read-only probe under
  `eda/<subject>/`, stating the question in a docstring and printing the answer legibly — instead of
  scattering one-off snippets it can't re-run next session. Findings that matter get written into the
  project's data doc as it goes, so you don't re-ask the same question in a week.
- **Growing.** When a probe turns out to be load-bearing — you keep re-running it, or something
  depends on its output — `/oryxflow:migrate` lifts it and the rest of the script into cached,
  parameterized tasks. It reads the existing code as the spec, shows you the step-to-task map, and
  writes files only when you approve; it never deletes your source.

So the honest answer to "isn't this overkill for a quick analysis?" is: **start simple, and let it
scale.** Simple scripts on day one, an arbitrarily complex pipeline later, with no rewrite in
between — which matters because data-science projects only ever grow more complicated. It pays off
hardest where an agent is otherwise most error-prone: expensive intermediate steps, model training
you iterate on, parameter sweeps, and research code someone else has to reproduce.

**Reach for something else when** you need production scheduling, retries, and SLAs — that's
[Airflow, Prefect, or Dagster](../blog/posts/oryxflow-vs-airflow.md)'s job, not oryxflow's — or when
what you want is a searchable dashboard of every run's metrics, which is an
[experiment tracker's](experiment-tracking.md) job (MLflow, Weights & Biases) and composes cleanly
beside oryxflow rather than replacing it.

## How it compares to other Claude Code data-science tools

The plugin landscape is early, so choose by the **job you need done**, not by a "best plugin"
label. Most tools cover data access or notebook execution; oryxflow covers the reproducibility
layer that keeps AI-generated analysis trustworthy as it grows.

| Job to be done | Reach for |
| --- | --- |
| Keep AI analysis trustworthy, reproducible, lineage-tracked | **oryxflow plugin** |
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

Edit `ProcessData`'s code and it — plus anything downstream — reruns automatically, so you can't
end up with an output that predates your own edit. That's the trust mechanism, and it's what the
agent is taught to verify. Run `flow.run()` again with nothing changed and nothing recomputes,
which is why the guarantee costs you nothing to keep.

## Frequently asked questions

**Does oryxflow work with any AI coding agent, or only Claude Code?**
The library is a plain Python package — it works no matter who writes the code, including you by
hand. The *plugin* packages the disciplines specifically for Claude Code; other agents can follow
the same [CLAUDE.md conventions](claude-plugin/index.md) manually.

**How do I stop Claude Code from building on stale data or rerunning expensive steps?**
Install the oryxflow Claude Code plugin. It teaches the agent to verify after each edit that the
right steps actually reran, and to answer staleness warnings instead of ignoring them — so it
never trains on stale intermediates, and every result stays tied to the code and data that
produced it. Because finished steps are reused rather than recomputed, it also stops paying twice
for the expensive ones. The library externalizes the 'did I already run this, is it still valid?'
state the agent can't reliably hold across a long session.

**Is there a Claude Code skill or plugin for data science?**
Yes — oryxflow ships an official Claude Code **plugin (skill + slash commands)** for data science
that makes an AI agent's analysis trustworthy and reproducible by default. The skill auto-activates
in an oryxflow project and applies data-science conventions as the agent writes; the slash commands
scaffold a project, migrate an existing notebook, and check standards. It runs on a local Python
library — no server or account, and not an MCP server.

**Do I have to restructure my project to use it?**
No — adopt it one task at a time. Point the agent at an existing script with `/oryxflow:migrate`,
or start fresh with `/oryxflow:init-project`.

**Is the oryxflow plugin overkill for a quick exploratory analysis?**
No, because it covers exploration too rather than demanding a pipeline upfront. Ask the agent to look
at a new dataset and it writes a re-runnable read-only probe under `eda/`, documenting the question
and recording anything it learns, instead of scattering one-off snippets. When a probe turns out to be
load-bearing, `/oryxflow:migrate` lifts it into cached tasks. So you start with simple scripts and
scale to any complexity later, with no rewrite in between.

## Takeaway

- Claude Code writes data analysis fast. oryxflow makes it **trustworthy and reproducible**: the
  agent can't hand you a number built from stale inputs, and every result stays tied to the code
  and data that made it.
- **A code, data, or parameter change reruns exactly what it affects** — which is what makes the
  agent's work checkable rather than something you have to take on faith. It's also why checking
  is free: nothing recomputes twice, so you're not paying an afternoon to be sure.
- **Start small.** Exploration stays exploration; `/oryxflow:migrate` grows it into a pipeline when
  the analysis earns one.
- It's a **plugin (skill + slash commands)**, not an MCP server, driving a local, MIT-licensed
  library — no server, no account, no telemetry.

Ready to build?

```text
/plugin marketplace add https://github.com/oryxintel/oryxflow-claude-plugin.git
/plugin install oryxflow@oryxflow
```

- **[Build with Claude Code](claude-plugin/index.md)** — the full plugin section: commands,
  trust model, project structure.
- **[Built for AI coding agents](ai-ready.md)** — the docs themselves are machine-readable
  (`llms.txt`, unit-tested core examples, docstring-generated reference), so any agent can learn
  oryxflow in one request.
- **[Why oryxflow](why-oryxflow.md)** — the positioning and how the library works.
- **[Quickstart](quickstart.md)** — from nothing to results you can trust and reproduce, in minutes.
- **[Migrate a messy notebook project](migrate-notebook-to-pipeline.md)** — already out of control?
  Start here.
