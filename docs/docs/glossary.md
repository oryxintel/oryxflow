---
title: Glossary
description: Plain-language definitions of the oryxflow concepts — reproducible pipeline, data lineage, code-change reruns, task DAG, Claude Code plugin vs skill, parameter sweeps, and caching.
---

# oryxflow glossary

Short, plain-language definitions of the terms used across these docs and in oryxflow itself.
Each entry links to where the concept is covered in full.

## What is a reproducible pipeline?

A reproducible pipeline is an analysis where every output can be traced to the exact code,
parameters, and inputs that produced it — so you can recreate any result on demand. oryxflow makes
your data-science work reproducible by default: each step is a cached task tied to its inputs and
code. See [Why oryxflow](why-oryxflow.md).

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
their dependencies, with no cycles. oryxflow runs the tasks in dependency order, skips any whose
output already exists, and reruns only what a change affects. You declare each task; the engine
works out the order. See [Writing tasks](tasks.md).

## What is the difference between a Claude Code plugin, a skill, and a slash command?

A **plugin** is the installable package you add to Claude Code. A **skill** is one thing a plugin
can contain — conventions the agent loads on its own when the context matches. A **slash command**
is an action you invoke explicitly, like `/oryxflow:init-project`. oryxflow ships a plugin
containing the oryxflow skill plus slash commands — not an MCP server. See
[Build with Claude Code](claude-plugin/index.md).

## What is a cached intermediate?

A cached intermediate is the saved output of a pipeline step, reused instead of recomputed on the
next run. oryxflow caches every task's output by its identity, so re-running a pipeline only pays
for what actually changed — no hand-rolled pickle files to manage or accidentally leave stale. See
[Task I/O formats](targets.md).

## What is a parameter sweep?

A parameter sweep runs the same analysis across many configurations — model × features × window —
to compare results. oryxflow computes each shared upstream step once and reruns only what each
configuration changes, so a sweep costs far less than re-running everything per combination. See
[Parameter sweeps without rerunning](../blog/posts/parameter-sweeps-without-rerunning.md).

## What is a task id and task family?

Every oryxflow task has a **task family** (its name) and a **task id** that also encodes its
parameters, so two runs with different parameters are distinct cached outputs you can tell apart.
This is how oryxflow keeps results from different configurations from overwriting each other. See
[Parameters](advparam.md).
