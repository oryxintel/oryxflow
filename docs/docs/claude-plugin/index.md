---
title: Build with Claude Code
description: The oryxflow Claude Code plugin makes AI data analysis trustworthy and reproducible — your coding agent verifies its own reruns, never builds on stale data, and records what produced every result.
---

# Build trustworthy AI data analysis with Claude Code

**The oryxflow Claude Code plugin makes an AI coding agent's data analysis trustworthy and
reproducible.** You get numbers you can believe and results you can regenerate months later —
and because nothing recomputes twice, the trustworthy path is also the fast, cheap one. It ships
as a skill plus slash commands — not an MCP server.

AI coding agents now write real data-science pipelines — feature engineering, model training,
experiment sweeps. They write plausible code fast. The hard part isn't the code; it's knowing
whether you can **believe the number** it just handed you, and whether you could **produce it
again** next month.

The **oryxflow Claude Code plugin** is how you get both. It pairs the oryxflow library — which
carries the state an agent can't hold in its head — with a skill that makes the agent verify its
own work. Three things follow, in the order they matter:

1. **You can trust the result.** A change to the data, a parameter, or the code reruns exactly
   what that change affects, and the agent checks that the rerun actually happened — so a number
   can't quietly sit on stale inputs.
2. **You can reproduce it.** Every result is tied to the code and inputs that made it in a
   queryable lineage record, so "how did we get this?" is answerable next month, by someone else.
3. **It costs you less, not more.** Reproducibility is normally a tax you pay in rerun time.
   Here it isn't: completed work is reused rather than recomputed, so you're not waiting on the
   10-minute data pull again or spending tokens watching an agent re-run it.

!!! tip "The short version"

    Install the plugin, then just describe what you want. It scaffolds a project, wires the DAG,
    and follows the [house conventions](https://github.com/oryxintel/oryxflow-claude-plugin/blob/main/skills/oryxflow/conventions.md)
    so the analysis is trustworthy and reproducible by default — no manual task wiring, no
    stale-data surprises.

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

## Why can't I just trust the agent's numbers?

Because in data work a mistake usually doesn't announce itself. A coding agent's weakness here
isn't syntax — it's **invisible state**. Across a long session it loses track of what's already
computed and whether it's still valid, then trains on stale features or hands you a number built
from last week's inputs. Nothing errors. The result just looks the same whether it's right or
wrong.

So the useful question isn't *"is the agent trustworthy?"* — it's *"can I check its work without
rebuilding it?"* oryxflow makes the answer yes by externalizing the state the agent can't hold,
and the plugin makes the agent *use* that machinery correctly. It:

- **starts every session by reading what's actually current** — pending staleness warnings, last
  runs, recent failures — so it never mistakes a stale result for a fresh one;
- **verifies after each edit that the intended tasks actually reran**, so a change that should
  have invalidated downstream work can't pass silently;
- **answers every staleness warning with the right move** — recompute, accept an
  output-equivalent refactor, or pin — instead of guessing (see
  [Automatic code invalidation](../managing-workflows.md#automatic-code-invalidation));
- **records decision-relevant results as lineage**, so any result stays reproducible and
  explainable across sessions.

These are the same disciplines documented in the
[CLAUDE.md snippet for AI-agent projects](../managing-workflows.md#claudemd-snippet-for-ai-agent-projects)
— shipped as a skill so they load automatically and stay current with the library, instead of a
copy you paste and forget.

## In this section

<div class="grid cards" markdown>

-   :material-check-decagram: **[Trustworthy AI data analysis](trust.md)**

    Why you shouldn't take an agent's numbers on faith — and how the plugin makes its work *cheap
    to verify* instead: session status, rerun verification, and durable lineage.

-   :material-console: **[Commands](commands.md)**

    The five slash commands — scaffold a project, migrate an existing analysis, check standards,
    and put data under Git LFS.

-   :material-file-tree: **[Data-science project structure](project-structure.md)**

    The load-bearing scaffold that keeps AI-generated data-science code from rotting — separation
    of concerns that's the shape of the code, not just its filing — and grows with the project.

-   :material-format-list-checks: **[Stop AI data analysis turning into a mess](coding-standards.md)**

    Canonical names, code grouped by subject, docstrings as documentation — loaded into the
    agent's context so they shape the analysis code as it's written, not audited after.

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
