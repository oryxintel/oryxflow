---
title: Build with Claude Code
description: The oryxflow Claude Code plugin makes AI-driven data analysis faster, cheaper, and more trustworthy — it teaches your coding agent to use the cache correctly, verify reruns, and never build on stale data.
---

# Build trustworthy AI data analysis with Claude Code

AI coding agents now write real data-science pipelines — feature engineering, model training,
experiment sweeps. They write plausible code fast. The hard part isn't the code; it's making
that code **trustworthy**: not silently rerunning expensive steps, not building on stale
intermediates, and always knowing which data and code produced a result.

The **oryxflow Claude Code plugin** is how you get that. It pairs the oryxflow library — which
carries the caching, lineage, and reproducibility an agent can't hold in its head — with a skill
that makes the agent a *disciplined user* of that cache. The result is **faster, cheaper, and
more trustworthy AI data analysis**: the agent reuses cached work instead of paying to recompute
it, verifies its own edits actually took effect, and leaves a queryable record of what ran and
why.

!!! tip "The short version"

    Install the plugin, then just describe what you want. It scaffolds a project, wires the DAG,
    and follows the [house conventions](https://github.com/oryxintel/oryxflow-claude-plugin/blob/main/skills/oryxflow/conventions.md)
    so the pipeline is reproducible by default — no manual task wiring, no stale-cache surprises.

## Install

```text
/plugin marketplace add https://github.com/oryxintel/oryxflow-claude-plugin.git
/plugin install oryxflow@oryxflow
```

Once installed, the `oryxflow` skill **auto-activates** whenever you work in an oryxflow project
(editing `tasks.py` / `flow.py` / `run.py` / `cfg.py` / `flow_params.py`) — you don't invoke it,
it's just on.

!!! note

    The skill activates inside an oryxflow **project**. If you install it into an empty directory
    and nothing happens, run [`/oryxflow:init-project`](commands.md) first (or ask the agent to
    set up an oryxflow project) so there's a pipeline for the skill to work on.

## Why this matters

A coding agent's weakness in data work isn't syntax — it's **invisible state**. Across a long
session it loses track of what's already computed and whether it's still valid, then trains on
stale features or re-runs a 40-minute job it didn't need to. Those are trust failures, and they
are exactly what oryxflow externalizes into a cache and a lineage log.

The plugin makes the agent *use* that machinery correctly. It:

- **starts every session with `oryxflow.events.print_status()`** — pending warnings, last runs,
  recent failures — so it never assumes a stale cache is fresh;
- **verifies after each edit that the intended tasks actually reran** (`result.reasons` /
  `events.runs()`), so a code-hash blind spot can't pass silently;
- **answers every staleness or expensive-recompute warning with the right exit** (recompute /
  `accept_code` / pin — see [Automatic code invalidation](../managing-workflows.md#automatic-code-invalidation));
- **logs decision-relevant scalars** via `self.logger`, so they persist as lineage across
  sessions and become the agent's memory next time.

These are the same disciplines documented in the
[CLAUDE.md snippet for AI-agent projects](../managing-workflows.md#claudemd-snippet-for-ai-agent-projects)
— shipped as a skill so they load automatically and stay current with the library, instead of a
copy you paste and forget.

## In this section

<div class="grid cards" markdown>

-   :material-console: **[Commands](commands.md)**

    The five slash commands — scaffold a project, migrate an existing analysis, check standards,
    and put data under Git LFS.

-   :material-check-decagram: **[Trustworthy AI data analysis](trust.md)**

    Why you shouldn't take an agent's numbers on faith — and how the plugin makes its work *cheap
    to verify* instead: session status, rerun verification, and durable lineage.

-   :material-file-tree: **[Project structure that stays clean](project-structure.md)**

    The load-bearing scaffold that keeps AI-generated code from rotting — separation of concerns
    that's the shape of the code, not just its filing — and grows with the project.

-   :material-format-list-checks: **[Coding standards the agent applies](coding-standards.md)**

    Canonical names, code grouped by subject, docstrings as documentation — loaded into the
    agent's context so they shape the code as it's written, not audited after.

-   :material-shield-check: **[Why library + plugin is a matched pair](why.md)**

    The division of labor: what the library carries, what the agent carries, and why the pairing
    gets *more* valuable as a project grows.

</div>

## Learn more

- Plugin repository and issues: <https://github.com/oryxintel/oryxflow-claude-plugin>
- House conventions the plugin follows: <https://github.com/oryxintel/oryxflow-claude-plugin/blob/main/skills/oryxflow/conventions.md>
- Plugin changelog: <https://github.com/oryxintel/oryxflow-claude-plugin/blob/main/docs/CHANGELOG.md>
- New to oryxflow? Start with **[Why oryxflow](../why-oryxflow.md)** and the
  **[Quickstart](../quickstart.md)**.

This library is the engine the plugin drives; the full API is documented throughout the rest of
these docs.
