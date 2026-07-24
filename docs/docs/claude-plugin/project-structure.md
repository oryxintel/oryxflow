---
title: Data-science project structure for Claude Code
description: The oryxflow Claude Code plugin scaffolds a load-bearing data-science project structure — separation of concerns across config, tasks, workflow, and analysis — that keeps AI-generated analysis code from rotting, and grows with the project instead of collapsing.
---

# A data-science project structure that stays clean as it grows

**A folder template tells you where a data-science file goes. It doesn't stop the analysis inside
from rotting.** The oryxflow Claude Code plugin scaffolds a data-science project structure that's
*load-bearing* — one that makes the messy shape harder to write than the clean one — so an AI
agent (which defaults to the flat script and the reused variable) produces a project that stays
well-formed as it grows.

Data-science code rots in a predictable way: one notebook becomes two, then a folder of them plus
a few scripts, all reading the same directory, each with its own copy of the cleaning logic.
Names drift, functions never appear, and six weeks later nobody can say which cell produced the
headline number. Hand the work to a coding agent and the failure arrives *faster*, because the
agent writes the path of least resistance by default.

## Start from a runnable scaffold

`/oryxflow:init-project` copies a minimal, runnable project into an empty directory — you start
writing real tasks immediately instead of hand-building folders and wiring:

```text
project/
├── tasks.py          # task definitions — what each step produces
├── cfg.py            # global config: environment, dates, credentials
├── flow_params.py    # workflow parameters (kept separate from global config)
├── flow.py           # the workflow instance — defined once, imported everywhere
├── run.py            # execute the workflow: python run.py
├── visualize.py      # load outputs for analysis and reporting
└── docs/oryxflow-data.md   # a place to record what you learn about the data
```

`python run.py` works from the first minute; you replace the placeholder tasks with your real
pipeline. It never overwrites files you already have.

## Why the structure is load-bearing, not decorative

A picture-perfect directory tree can still contain a single notebook that re-cleans the data on
every run and reuses `df` for four different frames. The layout was satisfied; the code still
rotted. oryxflow's structure is different because it's the *shape of the code*, not just its
filing:

- **The pipeline is a graph, not a top-to-bottom script.** Each step declares its dependencies
  (`@oryxflow.requires(...)`). "Runs top to bottom instead of as a DAG" — the single most-cited
  data-science code sin — isn't available to write. And because only what changed recomputes, the
  reproducible version is *also* the fast one, so nobody is tempted back to the flat script to
  save time.
- **Naming a task forces decomposition.** A task is named for the output it produces — a noun
  like `FeatureMatrix` or `TrainedModel`, not a verb like `GetData`. To add a step you must say
  what it produces and what it consumes, which drags "an absence of functions" toward its
  opposite: the work arrives already cut into named, single-purpose pieces.
- **Separation of concerns is real, not suggested.** Config in `cfg.py`, parameters in
  `flow_params.py`, definitions in `tasks.py`, the workflow instance in `flow.py`, execution in
  `run.py`, analysis in `visualize.py`. These are the seams the imports run along (`from flow
  import flow`, everywhere) — you change behavior by editing the layer that owns it, which is what
  keeps the thousand-line everything-script from forming.
- **Outputs are durable artifacts, not scrollback.** Every task saves a typed result you reload
  by asking for the task that made it. The headline number lives in a file you can re-open next
  month, not a printed line that scrolled away.

None of this is novel — it's the ordinary discipline the best-practice checklists recommend, with
one difference that matters: it's enforced by the shape of the thing rather than left to whether
you (or the agent) remember to be disciplined this afternoon.

## Bring an existing mess into it

If you already have a notebook or script that works, you don't rewrite it in one risky pass.
`/oryxflow:migrate` restructures an ad-hoc analysis into cached, parameterized tasks **one step
at a time**, so at every point you have a working pipeline — never a half-rewritten one. The end
state is reproducible and lineage-tracked, and the expensive steps stop rerunning on every edit.
See [From notebook to a reproducible, cached pipeline](../../blog/posts/notebook-to-pipeline.md)
for what that looks like step by step.

## It grows with the project

Most projects stay flat — one `tasks.py`, `run.py`, `flow.py`, `flow_params.py` — and that's the
right shape for the majority that stay research-only. When a project *does* grow, the plugin
nudges you along a graduated path instead of over-building up front:

1. **Naming families** — broad-to-narrow prefixes (`Features*`, `Model*`) cluster related tasks
   so a branch reads together.
2. **Comment section-headers** divide phases within one file — carrying it well past 500 lines
   before any split.
3. **Split into modules** (`tasks_features.py`, `tasks_model.py`) only when the file is genuinely
   long or a separable subsystem appears. This is cache-safe: a task's identity is its class name,
   not its module path, so *moving* a task doesn't invalidate its cache.
4. **A production tier** — frozen parameters and selective resets — appears when work goes to
   prod, kept separate from the fast experiment loop.

The agent offers the next structural step on a concrete trigger (a genuinely long file, going to
prod, a separable subsystem) — restructuring as the project earns it, which is exactly the
discipline data scientists tend to skip.

## Takeaway

The template gives you the filing cabinet. Making the structure *load-bearing* — a shape the code
has to take — gets you a project that stays well-formed even when the thing writing it is an agent
drawn to the mess. It won't make the analysis correct; a clean, reproducible, well-organized
pipeline can still answer the wrong question. But it removes the whole class of "which cell made
this, and can I run it again?" — which is a large, real fraction of what trustworthy AI-assisted
work requires.

**Read next**

- [Coding standards for AI data analysis](coding-standards.md) — the conventions that ride alongside
  the layout.
- [Trustworthy AI data analysis](trust.md) — verifying what the agent actually did.
- [Plugin commands](commands.md) — `init-project`, `migrate`, and the rest.
- [Managing complex workflows](../managing-workflows.md) — the cache and lineage the structure
  sits on.
