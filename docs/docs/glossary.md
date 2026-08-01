---
title: Glossary
description: Plain-language definitions of the oryxflow concepts — reproducible pipeline, data lineage, code-change reruns, task DAG, Claude Code plugin vs skill, parameter sweeps, and caching.
---

# oryxflow glossary

Short, plain-language definitions of the terms used across these docs and in oryxflow itself — the
vocabulary of reproducible data science, from task DAGs and lineage through to Claude Code plugins
and skills. Each entry links to where the concept is covered in full.

## What is a reproducible pipeline?

A reproducible pipeline is an analysis where every output can be traced to the exact code,
parameters, and inputs that produced it — so you can recreate any result on demand. oryxflow makes
your data-science work reproducible by default: each step is a task whose identity comes from its
code and its inputs, so a result is never attributed to code that didn't produce it. Its output is
saved under that identity, which is also what makes regenerating it cheap. See
[Why oryxflow](why-oryxflow.md).

## What is data lineage (provenance)?

Data lineage — also called provenance — is the record of what ran, when, with which parameters and
code, and why it recomputed. It turns "is this result stale?" and "was it built with the current
code?" into queries instead of guesses. oryxflow writes this record automatically as you run. See
[Why oryxflow](why-oryxflow.md).

## What is code-change invalidation?

Code-change invalidation means a pipeline reruns a step when its **code** changes — not only when
its parameters or data change. oryxflow compares what your code does, not how it's written, so
edits to comments or formatting are ignored, while a real logic change reruns that step and
everything downstream. See [Managing workflows](managing-workflows.md#automatic-code-invalidation).

## What is a task DAG?

A task DAG (directed acyclic graph) is your analysis expressed as steps — tasks — connected by
their dependencies, with no cycles. You declare each task; the engine works out the order, so a
step can never run before the step it depends on. A change reruns exactly what it affects, and
everything it doesn't affect is served from its saved output. See [Writing tasks](tasks.md).

## What is the difference between a Claude Code plugin, a skill, and a slash command?

They're nested, not competing. A **plugin** is the installable package you add to Claude Code. A
**skill** is one thing a plugin can contain — a bundle of instructions and conventions the agent
loads *on its own* when the context matches, without you invoking anything. A **slash command** is
an action you invoke explicitly, like `/oryxflow:init-project`. A plugin can also ship hooks and
other pieces.

oryxflow ships a plugin containing the `oryxflow` **skill** (the data-science conventions and cache
disciplines, which auto-activate when you edit pipeline files) plus a handful of **slash commands**
you call deliberately — `/oryxflow:init-project` to scaffold, `/oryxflow:migrate` to convert an
existing script or notebook, and a few more. It is **not** an MCP server. In practice you install
the plugin once and the skill just works in the background while you describe the analysis you want.
See [Build with Claude Code](claude-plugin/index.md) and
[the command reference](claude-plugin/commands.md).

## What is a cached intermediate?

A cached intermediate is the saved output of a pipeline step, reused instead of recomputed on the
next run. oryxflow saves every task's output under the identity of the code and parameters that
made it, so an intermediate can't be handed to you as current once either has changed — the thing
that goes wrong with hand-rolled pickle files. Reuse is the payoff: re-running a pipeline only pays
for what actually changed. See [Task I/O formats](targets.md).

## What is a parameter sweep?

A parameter sweep runs the same analysis across many configurations — model × features × window —
to compare results. In oryxflow each configuration's output is kept separately under the parameters
that produced it, so the comparison is between results you can each trace back, not files that
overwrote one another. Only what a configuration actually changes is recomputed — the shared
upstream steps run once — so a sweep costs far less than re-running everything per combination. See
[Parameter sweeps without rerunning](../blog/posts/parameter-sweeps-without-rerunning.md).

## What is a task id and task family?

Every oryxflow task has a **task family** (its name) and a **task id** that also encodes its
parameters, so two runs with different parameters are distinct cached outputs you can tell apart.
This is how oryxflow keeps results from different configurations from overwriting each other. See
[Parameters](advparam.md).
