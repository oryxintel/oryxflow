---
title: Why library + plugin is a matched pair
description: How the oryxflow library and its Claude Code plugin divide the work of trustworthy AI data analysis — the library carries the state an agent can't hold; the skill carries the disciplines the library can't enforce.
---

# Why library + plugin is a matched pair

The oryxflow library and the Claude Code plugin solve two halves of the same problem, and they're
most valuable together. This page is the honest account of who carries what — and why the pairing
gets *stronger* as a project grows, when most tooling buckles.

## The agent's real weakness: invisible state

A coding agent writing data-science code isn't tripped up by syntax. It's tripped up by state it
can't see across turns:

- **Stale intermediates** — it writes a cached file early, changes the code later, forgets to
  regenerate, and trains on stale data. No error is raised; the numbers are just wrong.
- **Expensive recompute in the inner loop** — its whole style is run → observe → edit → run, and
  in a plain script every loop recomputes the slow steps.
- **Path and load bookkeeping** — it hardcodes output paths, loses track of what's saved where,
  and occasionally loads the wrong file into the wrong step.

These are *memory* problems, not intelligence problems — and they're structural, because the
agent's recollection of "did I already run this, is it still valid?" degrades over a long session.

## The division of labor

The library and the plugin split the problem cleanly:

**The library carries the state the agent is structurally bad at holding.**

- The dependency graph is *data* (your `requires()` edges), not something the agent must
  remember.
- Re-running is cheap and correct by default — completed tasks load from cache; a code change
  reruns exactly the affected tasks and everything downstream, automatically.
- There are no filenames to get wrong — results are addressed by task identity.
- Every run appends a queryable lineage record of what ran, with which code and params, and why.

**The plugin carries the disciplines the library can't enforce from inside.**

- Start each session by reading cache state (`events.print_status()`) before assuming anything.
- After every edit, **verify the rerun actually happened** — a `ran=0` after a change means the
  edit landed in a hash blind spot (a data file, a dynamic call), and the fix is `reset()`.
- Answer every staleness or expensive-recompute warning with the right exit.
- Select the right named input when a task has multiple parents and outputs — a spot agents
  otherwise fumble.

The library removes the state-tracking; the skill supplies the verification. Neither half is
complete alone: a library can't make the agent *check* its work, and a prompt can't give the
agent a durable cache and lineage log.

## What neither half does — and why that's stated plainly

Being trustworthy means being clear about the boundary. oryxflow guarantees a result was produced
by the code and inputs it records — **not** that the result is *correct*. A perfectly reproducible
pipeline can still hand you a wrong number: a many-to-many join that should be many-to-one, a
leaked test set, a timezone shift that moves rows a day. None of those raise; each completes
cleanly and prints something plausible.

Those are caught by **habit, not machinery** — validate the merge, check the frame's shape and
null counts, quote every number from a saved artifact. The plugin ships exactly those habits
*alongside* the code, at the moment you're writing the task rather than in a review afterward.
The judgment calls — is this method right for the question, is this effect within noise — remain
yours. See
[What caching does not protect against](../managing-workflows.md#what-caching-does-not-protect-against)
for the full boundary.

## Why the pairing scales *up*

For throwaway exploration — load a CSV, group by, plot one thing — a task DAG is overhead; keep it
in a plain notebook. But the value inverts, super-linearly, as a project gains **depth** (a silent
stale intermediate near the top corrupts everything below), **expensive nodes** (the cache is the
difference between a tractable inner loop and one where every experiment costs minutes or
dollars), and **experiment matrices** (hand-managing output paths across a Cartesian product is
hopeless). Those are exactly the traits that make an AI agent error-prone without a DAG — and
exactly where the library-plus-plugin pairing helps most.

That's the whole point: this pairing gets *more* valuable as complexity rises, not less — the
opposite of tooling that collapses under scale.

## Get started

```text
/plugin marketplace add https://github.com/oryxintel/oryxflow-claude-plugin.git
/plugin install oryxflow@oryxflow
```

- **[Commands](commands.md)** — scaffold, migrate, and maintain a project.
- **[Trustworthy AI data analysis](trust.md)** — how the agent's work becomes cheap to verify.
- **[Project structure that stays clean](project-structure.md)** — the load-bearing scaffold.
- **[Coding standards the agent applies](coding-standards.md)** — the conventions that ship with
  the skill.
- **[Why oryxflow](../why-oryxflow.md)** — the positioning in full.
- **[Managing complex workflows](../managing-workflows.md)** — the cache, lineage, and
  invalidation the plugin drives.
