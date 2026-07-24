---
date: 2026-07-23
slug: best-practices-ai-assisted-data-analysis
categories:
  - AI agents
description: AI-assisted data analysis best practices for making AI-generated pipelines trustworthy AI data science — reproducible by structure, verified by human judgment.
faq:
  - q: "What are best practices for AI-assisted data analysis?"
    a: "Structure the work as a dependency graph instead of a linear script, cache expensive steps, keep a durable link between code and output, retain auditable lineage, verify that edits actually reran, separate exploration from the pipeline, version your data, and never outsource statistical judgment. The first seven you can hand to tooling; oryxflow and its Claude Code plugin enforce them, and the last stays with you."
  - q: "How do I keep AI-generated data analysis reproducible and trustworthy?"
    a: "Give the agent a structure that makes reproducibility automatic: a caching, dependency-aware task graph that reruns only what a code or data change affects and logs what ran. oryxflow provides code-change invalidation and a greppable .oryxflow/events.jsonl trail, so any result traces back to its inputs. Reproducible is not correct, though, so you still sanity-check joins, hold out validation data, and watch for leakage."
  - q: "What tools help make AI data analysis reproducible?"
    a: "Coding agents write the analysis, notebooks display it, and trackers like MLflow record runs, but none guarantee the computation is reproducible. That reproducibility layer is where a local-first caching library fits. oryxflow turns a data-science script into a cached, dependency-aware graph with code-change invalidation and local lineage, and its Claude Code plugin teaches the agent to work inside that structure. Orchestrators like Airflow are a complementary scheduling layer."
---

# Best practices for AI-assisted data analysis

*AI coding agents write plausible analysis fast. The hard part — is it reproducible, and is it right? — hasn't changed. These practices are about making AI-generated analysis you can actually trust.*

<!-- more -->

Ask a coding agent to load a dataset, engineer features, train a model, and compare a few
configurations, and it will produce clean, plausible code in seconds. That speed is real and it
is worth having. But "plausible code, fast" and "a correct, reproducible analysis you can stand
behind" are different bars, and the distance between them is exactly where AI-generated work
quietly goes wrong.

The good news: most of that gap is a *workflow* problem, not an intelligence problem. If you give
the agent a structure that makes reproducibility automatic, it stops making a whole class of
mistakes — stale intermediates, silent re-runs, results nobody can trace. What that structure
*cannot* do is make your statistics correct. So the practices below split cleanly: the first
seven you can hand to your tooling, and the last one you can never hand to anyone.

[oryxflow](https://github.com/oryxintel/oryxflow) is a small, local-first Python library that
turns a data-science script into a cached, dependency-aware graph of tasks — zero infrastructure,
no server, no telemetry. Its [Claude Code plugin](https://github.com/oryxintel/oryxflow-claude-plugin)
teaches an agent to work inside that structure. Together they enforce most of these practices for
you; `/oryxflow:init-project` is the on-ramp.

## 1. Structure work as a dependency graph, not a linear script

An agent loses pipeline state across turns. A long linear script gives it no reliable memory of
what has already run or whether it is still valid — so it re-derives that picture every turn and
gets it wrong. Model the work as tasks with declared dependencies instead. Each step names its
inputs, the engine runs them in order, and "what depends on what" is data the agent can read
rather than reconstruct.

```python
import oryxflow

oryxflow.set_dir('data/')

class GetData(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = load_raw()                  # your loader
        self.save(df)

@oryxflow.requires(GetData)
class Features(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()            # upstream output, typed and loaded for you
        self.save(build_features(df))

oryxflow.Workflow(Features, {}).run().outputLoad()
```

Note there is no file-path plumbing and no `to_parquet`/`read_parquet`. The base class you pick
(`TaskPqPandas`, `TaskPickle`, `TaskCachePandas`, …) drives type-based, zero-config I/O — the
single most common place hand-written analysis code rots.

## 2. Cache expensive steps so iteration is cheap

The agent iterates. Every turn it might touch feature code, model params, or a plot. If each turn
re-pays for the big join and the slow fit, iteration is expensive and the agent starts taking
shortcuts to avoid the wait. oryxflow skips any task whose output already exists, so the costly
upstream steps run once and every later turn is fast. Cheap iteration is not just ergonomics — it
removes the incentive to cut corners.

## 3. Keep a durable link between code and output

This is the one that bites hardest. The agent edits feature code, forgets to regenerate the saved
features, and trains on stale data. Nothing errors. The pipeline runs; the numbers are just
wrong. oryxflow watches your task code and, when the body of a step changes, automatically
invalidates that step and everything downstream — so the next run recomputes exactly what changed
and nothing else. You never evaluate new code on old output, and you never blow away the whole
cache to be safe.

## 4. Keep lineage you can audit

"Which code and which data produced this result?" should have an answer you can look up, not one
you reconstruct from memory. oryxflow records run events to `.oryxflow/events.jsonl` locally — a
plain, greppable trail of what ran, when, and why it was (or wasn't) recomputed. When a number
looks off, that file is where you start.

## 5. Verify the rerun actually happened

Do not trust that an edit took effect just because the agent said so. After a change, confirm the
affected task actually recomputed — check that its output timestamp moved, or read the lineage
trail from practice 4. Agents are confident narrators; "I've updated the features and retrained"
is a claim, not evidence. `flow.preview()` shows you what the engine considers complete versus
pending before you run, so you can see the plan and catch a step that *should* be stale but isn't.

## 6. Keep exploratory work separate from the pipeline

Early exploratory analysis should stay loose — scratch cells, quick plots, throwaway checks.
Don't prematurely formalize it, and don't let it silently become load-bearing either. When a
piece of exploration earns its place — you'll rerun it, others depend on it, it feeds a result —
promote it into a task deliberately. The plugin's `/oryxflow:migrate` command does that
conversion for you, lifting a notebook or script into cached tasks when it's ready, so the
boundary between "playing" and "pipeline" stays honest.

## 7. Version your data so results are regenerable

Reproducible code on top of a mutable, unversioned dataset is only half-reproducible. Track your
data alongside your code so any result can be regenerated from a known state.
`/oryxflow:init-gitlfs` sets up Git LFS for the data directory, so inputs and outputs are
versioned the same way the code is.

## 8. Never outsource judgment — reproducible is not correct

Here is the honesty that the other seven practices exist to protect: **no tool makes your
statistics correct.** oryxflow guarantees that the same code and data give the same result, that
stale steps recompute, that lineage is auditable. It does *not* check that your join keys are
right, that your validation is honest, or that your features don't leak the target. Those are
judgment, and judgment does not delegate — not to a library, and emphatically not to the agent.

So keep these firmly in human hands, every time:

- **Sanity-check joins and aggregations.** Row counts before and after, spot-check a few keys,
  confirm the grain is what you think it is. A silent fan-out or dropped rows survives any amount
  of caching.
- **Hold out real validation data** and keep it untouched until the end. An agent optimizing a
  metric will happily overfit to whatever you let it see.
- **Watch for leakage and lookahead.** A feature computed with information from the future, or a
  target that sneaks into the inputs, produces beautiful, reproducible, worthless results.
- **Read the numbers skeptically.** An accuracy that jumped suspiciously, a distribution that
  shifted, a metric that's too good — treat these as bugs to explain, not wins to ship.

Reproducibility is what lets you *investigate* correctness efficiently: because the pipeline is
stable and traceable, when a number looks wrong you can trust that the code and data in front of
you are what produced it. That's the foundation judgment stands on — not a substitute for it.

## The practices at a glance

| Practice | What it prevents | How oryxflow / the plugin helps |
| --- | --- | --- |
| Dependency graph, not linear script | Agent losing pipeline state across turns | Tasks with `@requires`; the engine runs the DAG in order |
| Cache expensive steps | Re-paying for the big join every turn; corner-cutting | Skips any task whose output exists |
| Link code to output | Training new code on stale data | Automatic AST code-change invalidation of the step + downstream |
| Auditable lineage | "No idea what produced this number" | `.oryxflow/events.jsonl`, local and greppable |
| Verify the rerun happened | Trusting an edit that didn't take | `flow.preview()` shows complete vs pending |
| Separate EDA from pipeline | Scratch work silently becoming load-bearing | `/oryxflow:migrate` promotes it when it earns it |
| Version your data | Results you can't regenerate | `/oryxflow:init-gitlfs` |
| Never outsource judgment | Reproducible-but-wrong analysis | **Nothing** — this one is yours |

Orchestrators (Airflow, Prefect, Dagster) and experiment trackers (MLflow) are complementary
layers here, not competitors — they schedule and record; oryxflow is the local, code-tight inner
loop the agent iterates in.

## Takeaway

AI makes analysis *fast to write*. Trust comes from making it *reproducible by structure* and
*correct by judgment* — two different jobs. Let oryxflow and its plugin enforce the reproducible
half automatically, and keep the correctness half where it belongs: with a skeptical human reading
the numbers. `/oryxflow:init-project` sets up the foundation; you bring the judgment.

```bash
pip install oryxflow
```

## Frequently asked questions

### What are best practices for AI-assisted data analysis?

Structure the work as a dependency graph instead of a linear script, cache expensive steps, keep
a durable link between code and output, retain auditable lineage, verify that edits actually
reran, separate exploration from the pipeline, version your data, and never outsource statistical
judgment. The first seven you can hand to tooling; oryxflow and its Claude Code plugin enforce
them, and the last stays with you.

### How do I keep AI-generated data analysis reproducible and trustworthy?

Give the agent a structure that makes reproducibility automatic: a caching, dependency-aware task
graph that reruns only what a code or data change affects and logs what ran. oryxflow provides
code-change invalidation and a greppable .oryxflow/events.jsonl trail, so any result traces back
to its inputs. Reproducible is not correct, though, so you still sanity-check joins, hold out
validation data, and watch for leakage.

### What tools help make AI data analysis reproducible?

Coding agents write the analysis, notebooks display it, and trackers like MLflow record runs, but
none guarantee the computation is reproducible. That reproducibility layer is where a local-first
caching library fits. oryxflow turns a data-science script into a cached, dependency-aware graph
with code-change invalidation and local lineage, and its Claude Code plugin teaches the agent to
work inside that structure. Orchestrators like Airflow are a complementary scheduling layer.

**Read next**

- [Claude Code for data science](../../docs/claude-code-for-data-science.md)
- [Why a caching DAG makes your AI coding agent a better data scientist](caching-dag-for-ai-agents.md)
- [When *not* to use oryxflow](when-not-to-use-oryxflow.md)
- [From notebook to pipeline](notebook-to-pipeline.md)
- [Why oryxflow](../../docs/why-oryxflow.md)
- [Build with Claude Code: the oryxflow plugin](../../docs/claude-plugin/index.md)
- [Why a plugin, not just a library](../../docs/claude-plugin/why.md)
- [Managing workflows](../../docs/managing-workflows.md)
- Plugin repo: <https://github.com/oryxintel/oryxflow-claude-plugin>
