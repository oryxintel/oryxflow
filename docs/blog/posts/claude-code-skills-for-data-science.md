---
date: 2026-07-23
slug: claude-code-skills-for-data-science
categories:
  - AI agents
description: What Claude Code skills are, how they differ from plugins, slash commands, and MCP servers, and why a skill is the right tool for keeping AI-written data analysis reproducible — with the oryxflow skill as a worked example.
---

# Claude Code skills for data science: what they are and why they matter

*A skill is the part of a Claude Code plugin that teaches the agent how to work. For data
science, that turns out to be exactly what's missing from "AI writes the analysis fast."*

<!-- more -->

If you've used Claude Code on a data-science project, you know the two feelings that follow
each other closely: *this is fast*, then *wait, can I trust what it just did?* The agent pulls
data, engineers features, fits a model, and plots a result in one session — and somewhere in
there it re-runs a ten-minute step it didn't need to, or evaluates a model on an output that
went stale three prompts ago. The code looks fine. The number is quietly wrong.

**Claude Code skills** are the mechanism that closes that gap. This post explains what a skill
actually is, how it differs from the other things people call "plugins," and why a skill — not a
data connector, not a notebook runner — is the piece that makes AI-written data analysis
reproducible.

## What is a Claude Code skill?

A **skill** is a bundle of instructions and conventions that Claude Code loads into its context
**automatically, when they're relevant** — you don't invoke it. Think of it as procedural
knowledge the agent picks up the moment it recognizes the situation: "you're editing a pipeline
file, so here's how this project expects pipelines to be built and verified."

That auto-activation is the whole point. Instead of you re-explaining your conventions every
session, or pasting a style guide into every prompt, the skill supplies them exactly when the
agent needs them and stays out of the way otherwise. A skill can carry naming rules, project
structure, the right way to use a library, checks to run after an edit — anything that answers
*how should the agent work here?*

## Skills vs plugins vs slash commands vs MCP servers

These terms get used interchangeably, and they shouldn't be. They're different layers:

| Term | What it is |
| --- | --- |
| **Plugin** | The installable package you add to Claude Code. It can contain skills, slash commands, hooks, and more. |
| **Skill** | Instructions/conventions inside a plugin that the agent loads *on its own* when the context matches. No invocation. |
| **Slash command** | An action you trigger *explicitly*, like `/oryxflow:init-project`. |
| **MCP server** | A separate process that exposes external tools or data to the agent over the Model Context Protocol. A connector — not a skill. |

The distinction that matters most for data science: a **skill changes how the agent works** with
what's already on your machine, while an **MCP server connects the agent to something external**.
Reproducibility is a discipline-of-work problem, so it's a **skill** problem — not something a
connector solves.

## Why data science needs a skill, specifically

A coding agent's weakness in data work isn't writing pandas — it's **invisible state across
turns**. Agents are effectively stateless between steps; your pipeline is stateful. Over a long
session the agent loses track of what's already computed and whether it's still valid, then:

- **builds on stale intermediates** — a feature changed, a cached file didn't, and the model
  trains on yesterday's data;
- **loses lineage** — nobody can say which code and inputs produced `model_final_v3.pkl`;
- **wastes compute** — a one-line downstream edit re-runs the expensive data pull.

None of these are math errors. They're mechanics-of-the-pipeline errors, and they get *worse* as
the agent writes more of the code. A skill is the right fix because the fix is procedural: *check
what's already computed before recomputing; verify an edit actually reran what it should have;
record what ran and why.* That's conventions-of-work — exactly what a skill encodes.

## What a good data-science skill does

A reproducibility skill makes the agent a **disciplined user of a cache and a lineage log**.
Concretely, it has the agent:

- **start each session by reading cache state** — pending staleness warnings, recent runs and
  failures — so it never assumes a stale result is fresh;
- **verify after each edit** that the steps which should have rerun actually did, so a silent
  miss can't slip through;
- **answer every staleness or expensive-recompute prompt** with the right move — recompute,
  accept an output-equivalent refactor, or pin — instead of guessing;
- **record decision-relevant results as lineage**, so they become the agent's memory across
  sessions.

## A worked example: the oryxflow skill

[oryxflow](https://github.com/oryxintel/oryxflow) is a local-first Python library that turns
scripts and notebooks into a cached, dependency-aware task graph: you declare tasks with
parameters and `requires()` dependencies, and the engine runs them in order, skips whatever's
already computed, and reruns exactly what a parameter, data, or **code** change affects.

The oryxflow plugin ships a **skill plus slash commands** — not an MCP server. The `oryxflow`
skill auto-activates when you work in an oryxflow project and front-loads the correct idioms so
the agent reuses cached work, verifies its own edits, and never builds on stale data. The slash
commands cover the explicit actions: `/oryxflow:init-project` to scaffold, `/oryxflow:migrate`
to restructure a loose notebook into a pipeline, `/oryxflow:check-standards` to keep names and
docstrings consistent.

The result is the brand promise, delivered mechanically: **faster, cheaper, and more trustworthy
AI data analysis** — reproducible and lineage-tracked by default.

## How to use skills for data science

Skills come packaged in plugins, so you install the plugin once and the skill is simply *on*
from then on:

```text
/plugin marketplace add https://github.com/oryxintel/oryxflow-claude-plugin.git
/plugin install oryxflow@oryxflow
```

After that, describe the analysis you want. The skill works in the background — you don't call
it. If you install into an empty directory and nothing happens, scaffold a project first with
`/oryxflow:init-project` so there's a pipeline for the skill to act on.

## Can I build my own skill?

Yes — a skill is fundamentally a written set of conventions the agent loads when relevant, so the
hard part isn't packaging, it's *knowing what good looks like* for your domain. For data science
that's the reproducibility discipline above: read state first, verify edits, answer staleness
prompts deliberately, record lineage. If you'd rather not write that from scratch, the oryxflow
skill already encodes it and works on any oryxflow project.

## Takeaway

- A **Claude Code skill** is auto-loading know-how — conventions the agent picks up when the
  context matches, without being invoked. It's distinct from a slash command (explicit action)
  and an MCP server (external connector).
- For data science, the missing piece is **reproducibility across turns**, and that's a
  discipline-of-work problem — which makes it a **skill** problem.
- The [oryxflow](https://github.com/oryxintel/oryxflow) skill is a worked example: it makes
  Claude Code cache, verify, and track lineage by default. See
  [Claude Code for data science](../../docs/claude-code-for-data-science.md) for the full
  picture, or [the best Claude Code plugins for data science](best-claude-code-plugins-data-science.md)
  for how it fits alongside data connectors and notebook tools.
