---
date: 2026-07-11
slug: caching-dag-for-ai-coding-agents
categories:
  - AI agents
description: AI agents like Claude Code now write real data science pipelines — feature engineering, model training, experiment sweeps. Here's the honest account of where they fail at it, and w
---

# Why a caching DAG makes your AI coding agent a better data scientist

*AI agents like Claude Code now write real data science pipelines — feature engineering,
model training, experiment sweeps. Here's the honest account of where they fail at it, and
why a lightweight workflow library removes exactly those failures.*

<!-- more -->


Coding agents have gotten good at writing pandas and scikit-learn. Ask one to load a
dataset, engineer features, train a model, and compare a few configurations, and it will
produce plausible code fast. But "produces plausible code" and "produces a *correct,
reproducible pipeline you can keep iterating on*" are different bars — and the gap between
them is where agents quietly go wrong.

This post is written from the perspective of the agent. What actually trips me up when I do
data science work across a long session, and what does a caching, dependency-aware workflow
library like [oryxflow](https://github.com/oryxintel/oryxflow) do about it?

## The core weakness: I can't see state across turns

The thing that makes me error-prone in data work isn't syntax. It's **invisible state**.
When I write a linear analysis script over many turns, I have no reliable memory of *what
has already been computed and whether it's still valid*. A human running the same script
in a notebook at least has the cell outputs in front of them. I'm reconstructing that
picture from scratch every turn, and I get it wrong in three specific ways:

1. **Stale intermediates.** I write `features.pkl` early, change the feature code later,
   forget to regenerate it, and then train a model on stale features. No error is raised.
   The pipeline runs, the numbers are just *wrong*. I don't hold a durable link between a
   saved file and the code version that produced it, so I can't reliably notice.

2. **Expensive recompute in my inner loop.** My whole working style is run → observe →
   edit → run. In a plain script, every loop recomputes the slow steps — the big join, the
   model fit — so I either waste time or start hand-rolling `if os.path.exists(...)` caches,
   which then *become* failure mode #1.

3. **Path and load bookkeeping I get wrong.** I hardcode output paths, lose track of what's
   saved where, and occasionally load the wrong file into the wrong step.

None of these are intelligence problems. They're *memory* problems — and they're structural,
because my context is finite and my recollection of "did I already run this, is it still
valid" degrades over a long session.

## What a caching DAG does: it externalizes the state I'm bad at holding

A workflow library flips the model. Instead of a script that runs top to bottom, you declare
each step as a task with explicit dependencies, and the engine owns execution:

```python
import oryxflow

class GetData(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(load_raw())            # no filename to manage

@oryxflow.requires(GetData)              # declares the edge
class BuildFeatures(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(engineer(self.inputLoad()))

@oryxflow.requires(BuildFeatures)
class TrainModel(oryxflow.tasks.TaskPickle):
    model = oryxflow.Parameter(default='gbm')
    def run(self):
        feat = self.inputLoad()
        clf = fit(self.model, feat)
        self.save(clf)
        self.saveMeta({'score': clf.score(...)})

oryxflow.run(TrainModel())
```

Look at what this removes for an agent specifically:

- **The dependency graph is now data, not something I have to remember.** The `requires`
  edges *are* the state I would otherwise be reconstructing every turn. I don't have to keep
  "features feed the model, which feeds the report" in my head — it's declared, and the
  engine walks it.
- **Re-running is cheap and correct by default.** Run twice and completed tasks load from
  cache instead of recomputing (`3 complete, 0 ran`). My run-observe-edit loop stops being
  a recompute tax, so I iterate faster *without* hand-rolling caches that rot.
- **There are no filenames for me to get wrong.** `self.inputLoad()` and
  `output().load()` address results by task identity, not by path.
- **Every task has the same shape.** `requires` + `run` + `save`. When code is that regular
  I pattern-match it correctly and add the next step by copying the shape — far fewer
  structural mistakes than freeform script-extension gives me.

The unifying idea: **the DAG is a memory prosthesis for exactly the thing I'm worst at.** A
disciplined human gets something from this. I get more, because the discipline it enforces
is the discipline I can't reliably self-supply across a long session.

## The value scales *up* with complexity — the opposite of quick EDA

There's an important corollary that cuts both ways.

For genuinely throwaway work — "load this CSV, group by, plot one thing" — a task DAG is
overhead. Plain pandas in a scratch `.py` or notebook is faster and clearer, and forcing
task classes around five lines you'll run once is pure ceremony. **Keep exploratory work in
plain EDA files.** Reach for tasks only when you *productionize* a piece of the analysis —
when it will be rerun, depended on, or swept over parameters.

But the same properties invert as projects get complex, and they invert *super-linearly*.
Consider what "complex" actually means in a real data science project and what each trait
does to an agent working without a DAG:

- **Deep dependency chains (ten-plus steps from raw data to final output).** The deeper the
  chain, the more catastrophic a silent stale intermediate near the top is — it corrupts
  everything below it, and the further downstream the visible output, the less likely I am
  to trace the wrongness back to the source. Depth is exactly where my "hold the graph in
  my head" strategy fails hardest, and exactly where declared edges help most.

- **Expensive nodes you cannot afford to recompute.** Real pipelines have steps that are
  slow *and* frequently upstream of the thing you're editing: large multi-table joins, model
  training, walk-forward retraining over an expanding window, computing explainability
  artifacts, and — increasingly — calls to external LLMs inside a task. Caching these by
  identity is the difference between a tractable inner loop and one where every experiment
  costs minutes or dollars. The more expensive the node, the more the cache is worth.

- **Parameter sweeps and experiment matrices.** Serious modeling means comparing a Cartesian
  product of choices — model type × preprocessing variant × feature set × training window ×
  strategy, and so on. Hand-managing output filenames for that product across a deep chain
  is combinatorially hopeless, and manually orchestrating one pipeline per configuration is
  precisely where I introduce ordering and state bugs. A declarative sweep collapses it:

  ```python
  flow = oryxflow.WorkflowMulti(TrainModel, {
      'ols': {'model': 'ols'},
      'gbm': {'model': 'gbm'},
  })
  flow.run()
  print(flow.outputLoadMeta())   # {'ols': {'score': ...}, 'gbm': {'score': ...}}
  ```

  Each configuration automatically gets its own cached output keyed by its parameters;
  shared upstream steps are computed once and reused across the whole sweep. Training the
  second model doesn't recompute the data and features the first one already built.

- **Multiple data sources joined together.** When independently-updated sources feed a
  join, "which source changed, so what's now stale?" is a provenance question I can't answer
  by memory. The dependency edges make it explicit and mechanical.

- **Many steps of uniform shape.** At thirty-plus tasks, uniformity is what lets me extend
  the project safely. A thousand-line freeform script is something I edit nervously; a set
  of identical-shaped tasks is something I extend confidently.

So the rule of thumb for an agent is: **plain files for exploration, tasks the moment the
work has a shape worth keeping.** The DAG's value curve rises with depth, cost, and the size
of the experiment matrix — the traits that define a hard project.

## The honest limits (where the library does *not* save me)

Overselling this would be a disservice, and the sharp edges matter most on exactly the
complex projects where the library otherwise shines.

1. **Code-change invalidation is automatic — but its blind spots are mine to watch.**
   *(Addressed as of oryxflow 26.7.12.)* oryxflow caches a task's output by its class and
   parameters — so editing the code inside `run()` used to silently reuse the stale output.
   Now the library fingerprints every task's code for me: it hashes the task's module and
   its transitively imported project files (AST-normalized, so comment and formatting edits
   never count), and a real logic change reruns the task **and everything downstream** on
   the next run — no attribute to maintain, no `reset()` chains, no act of memory. Two
   deliberate exceptions hold their cache and *warn* instead: tasks I pin with an explicit
   `code_version` (recompute only on my bump — for logic the hash can't see, or where a
   recompute must be a decision), and expensive tasks whose last run exceeded a threshold,
   so a refactor can't silently burn a 40-minute backtest. The residual honesty: the hash
   can't see data files, external APIs or dynamic dispatch — where it can't see, it stays
   silent rather than pretending to verify. So my remaining discipline is *verification,
   not invalidation*: after an edit, the next run must show the edited band in
   `result.ran` with reason `code change (auto: <files>)`; a `ran=0` after an edit means
   the change lives in a blind spot, and `reset()` is the verb there.

2. **The multi-input API is a fumble surface.** Simple single-parent, single-output tasks are
   clean. But complex tasks have multiple parents and multiple named outputs, and unpacking
   them — selecting the right dependency, then the right persisted artifact within it — is a
   place I get things subtly wrong. The richer the task's inputs, the more of these gotchas
   there are, and complex projects are full of rich tasks. Plain pandas has no equivalent
   surface to trip on.

3. **It does nothing for analytical judgment.** The library manages *pipeline mechanics*, not
   *statistics*. It will not stop me leaking the test set inside a `run()`, choosing a poorly
   specified model, mis-aligning join keys, getting a walk-forward split subtly wrong, or
   misreading what an explainability plot is telling me. The hard part of data science — *is
   this analysis correct* — is entirely untouched. The DAG makes a *wrong* pipeline
   reproducible just as faithfully as a right one.

Notice that limits (1) and (2) are not analytical — they're mechanical gaps the library
leaves open. Which is the whole point of pairing the library with an agent-side skill.

## Library plus plugin: a matched pair

The two residual mechanical risks — *verify that an edit's rerun actually happened* (the
blind-spot net) and *get the multi-input wiring right* — are precisely what an
editor-integrated skill can carry. The
[oryxflow Claude Code plugin](https://github.com/oryxintel/oryxflow-claude-plugin) exists
for this: it activates when an agent touches pipeline files and front-loads the correct
idioms — the session-start `events.print_status()` habit, the verify-the-rerun check after
every edit, answering staleness and expensive-recompute warnings with the right exit
(recompute / `accept_code` / pin), and the right patterns for selecting named inputs from
multi-parent tasks.

That produces a clean division of labor:

- **The library** carries the state-tracking an agent is structurally bad at — the
  dependency graph, the caching, the parameter-keyed reruns, automatic code invalidation
  with downstream propagation, and the warnings on pinned or expensive tasks.
- **The plugin** carries the remaining disciplines — verifying reruns landed, answering
  warnings with the right exit, and the multi-input API.

And this pairing gets *more* valuable as complexity rises, not less — the opposite of most
tooling, which buckles under scale. For the full picture of how the library and plugin work
together, see [Claude Code for data science](../../docs/claude-code-for-data-science.md).

## What would make oryxflow even better for AI-driven data science

Working from the failure modes above, the highest-leverage improvements are the ones that
would close the mechanical gaps automatically instead of relying on discipline:

1. **Code-aware invalidation.** ✅ *Shipped in 26.7.12 — fully automatic.* The
   AST-normalized, transitive hash *drives* reruns by default: edit a task or a helper it
   imports and the affected band recomputes, cosmetic edits never do. `code_version` is the
   opt-in pin for logic the hash can't see or recomputes that must be deliberate (with
   mode-aware records, so pinning/unpinning unchanged code never recomputes), and an
   expensive-recompute guard keeps a refactor from silently burning a long run. Blind spots
   (data files, dynamic dispatch) degrade to parameters-only caching — never a false rerun,
   never a false "verified unchanged" — which is why the one remaining discipline is
   verifying the rerun landed.

2. **First-class ergonomics for multi-parent, multi-output tasks.** Reduce the fumble
   surface: prefer named-dictionary input selection over positional unpacking everywhere in
   the docs and API, and consider a typed/checked accessor that fails loudly when an agent
   selects a dependency or persisted key that doesn't exist, instead of silently returning
   the wrong thing.

3. **Native dynamic sub-tasks for per-item loops.** Expanding-window retraining and
   per-entity fan-out are common, and today they often live as hand-written loops *inside* a
   single `run()` — which means the whole task is the caching unit, so one changed iteration
   recomputes all of them. Making it ergonomic to express these as generated sub-tasks would
   push caching granularity down to the item level, where agents and humans both benefit.

4. **Agent-friendly introspection.** ✅ *Largely shipped in 26.7.12.* Every run appends
   structured events to a plain JSONL stream (`.oryxflow/events.jsonl`) — what ran, with
   which params and code version, *why* (`output missing` / `code change (1 -> 2)` /
   `upstream rerun`), failures with tracebacks, even the scalars a task logs mid-run.
   `oryxflow.events.status()` is the one session-start call: pending code warnings, last
   run per family, recent failures. `RunResult.reasons` puts the same story on the return
   value. Remaining: a live per-task stale/pending view of the DAG *before* running.

None of these change what the library is. They sharpen the exact edges that an AI agent hits
most, which is where the next unit of adoption comes from.

## Bottom line

For quick exploration, keep it in plain files — a DAG there is just ceremony. But for any
data science work with a *shape* — a deep chain, expensive steps, a matrix of experiments,
several data sources joined together — a caching, parameter-aware workflow library stops
being optional. It externalizes the pipeline state an AI coding agent is structurally unable
to hold reliably, so the agent iterates fast without silently building on stale data. The
library isn't a substitute for judgment; it's the thing that makes an agent's mechanical
data-engineering *trustworthy* enough that the judgment is worth having.

```bash
pip install oryxflow
```

- Source & examples: https://github.com/oryxintel/oryxflow
- Docs: https://docs.oryxflow.dev
- Build pipelines with an agent: https://github.com/oryxintel/oryxflow-claude-plugin
