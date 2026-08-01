---
title: Migrate a messy notebook project to a pipeline
description: Turn an out-of-control Jupyter notebook or linear script into a scalable oryxflow pipeline, so a stale intermediate can't produce a confident wrong number.
faq:
  - q: "How do I restructure a messy data-science project into a pipeline?"
    a: "Read the notebook or script first and cut it at its seams: every block that consumes some inputs and produces an intermediate — a loaded frame, a cleaned frame, a feature matrix, a fitted model — becomes one task, named for the intermediate it produces. Wire the tasks with @oryxflow.requires, end each run() with self.save(), and hoist the magic constants into parameters. Then build the tasks up in dependency order, running the flow after each one, so a break surfaces at the task that caused it instead of five tasks later."
  - q: "Do I have to convert the whole notebook at once?"
    a: "No, and you shouldn't. Convert the step that hurts most first — usually the slow data load — run it once so its result caches, and let the rest of your cells keep working, now fed by flow.outputLoad(). Add the next step only when it earns it. You have a working pipeline after every single step, so there is never a half-rewritten state to recover from."
  - q: "How do I know the migration didn't change my numbers?"
    a: "Keep the original notebook or script in place and treat it as the specification: it is the only record of what the pipeline is supposed to do, and it is the oracle you check against. Once the migrated flow runs end to end, compare a headline number from flow.outputLoad() against the same number in the original. A migration that runs but yields different numbers has silently changed behavior, so diagnose the difference rather than declaring success because the run finished without an error."
  - q: "Does exploratory analysis have to become oryxflow tasks?"
    a: "No. Read-only probes — checking a schema, eyeballing a distribution, testing a parsing guess — stay plain scripts under eda/<subject>/<name>.py, each stating the question it answers, and they write no pipeline output. Promote a probe into a task only when it turns out to be load-bearing, meaning something downstream depends on its result. That way there is no cliff between exploring and having a pipeline."
  - q: "Why does an AI coding agent make a messy notebook project worse instead of better?"
    a: "Because the mess is invisible state, and an agent cannot see it. It cannot tell which cells you actually ran, which intermediate file is current, or which constant produced the number in the notebook's output, so it re-derives that state by guessing — and writes plausible code on top of a wrong guess. Moving the run order into @oryxflow.requires and the intermediates into a cache makes that state readable instead of guessable, which is why the same structure that helps a human helps an agent more."
---

# Migrate a messy notebook project into a pipeline

**This is the page for the project that got away from you.** Nine notebooks and four scripts that
all read the same folder. `clean_v3.csv` sitting next to `clean_v3_final.csv`. A threshold typed
into a cell six weeks ago that you can no longer connect to any result. And one headline number
you would rather not have to defend in a meeting.

The reason to restructure it is **not** tidiness. It is that in a project shaped like that, a
wrong answer is always available and nothing warns you: the cell you didn't rerun, the
intermediate that never refreshed, the parameter you changed after the chart was made. Migrating
to an oryxflow pipeline removes that whole class of failure — **the wrong number stops being
possible, and your AI coding agent stops guessing.**

You do not rewrite the project to get there. You migrate it one task at a time, keeping a working
pipeline at every step, with the original code left in place as the specification.

## What actually goes wrong

None of these show up as errors. That's the problem — they produce output, confidently.

- **The run order lives in your memory.** A notebook's correctness depends on which cells ran, in
  what order, against which variables still in the kernel. Reopen it in a month, hit Run All, and
  you get a different number with no explanation.
- **Intermediates outlive the code that made them.** `clean_v3.csv` was written by a cell you have
  since edited or deleted. Everything downstream still reads it happily. There is no way to ask
  the file which code produced it.
- **Magic constants can't be mapped to results.** A cutoff of `0.15` is in a cell somewhere, and
  the chart you sent last week used `0.20`. Both charts exist; only one is labeled, and neither
  records what it was built with.
- **A stale step answers as confidently as a fresh one.** You fix the cleaning logic, forget that
  the feature file was built before the fix, and the model trains on the old features. Every cell
  runs green. The result is simply wrong.

### An AI coding agent makes this worse, not better

Handing a messy project to a coding agent accelerates the failure rather than fixing it. The
agent **cannot see which cell ran**, which intermediate is current, or which constant produced
the number sitting in a notebook's saved output — so it reconstructs that state by inference, and
then writes fast, plausible code on top of whatever it inferred. Being wrong is cheap and silent;
being confidently wrong is the default.

So the structure that helps a human helps an agent more, because it turns invisible state into
something readable: the run order becomes a decorator, the intermediates become a cache keyed by
code and parameters, and "is this current?" becomes a question with an answer instead of a guess.
That's the subject of [Trustworthy AI data analysis](claude-plugin/trust.md).

## What you migrate toward

Each piece of the target structure removes a specific class of error — that's the whole point of
the exercise, and it's worth reading as a mapping rather than as a style guide:

| In the messy project | What replaces it | The error it removes |
| --- | --- | --- |
| Run order remembered by hand | `@oryxflow.requires(Upstream)` on each task | Out-of-order execution: the engine always runs dependencies first |
| Magic constants in cells | Parameters declared on the task (`oryxflow.FloatParameter(...)`) | "Which value produced this?" — the value is part of the output's identity |
| `to_csv('clean_v3.csv')` handoffs | `self.save(...)` and `self.inputLoad()` | Reading an orphaned or stale file as if it were current |
| "Run these cells, in this order" | `python run.py` | A result nobody else can reproduce |
| Numbers printed to scrollback | A saved output you reload by naming its task | The headline figure that can't be found again |

Concretely, that lands as a small, boring project layout: task definitions in `tasks.py`,
parameters in `flow_params.py`, environment and source paths in `cfg.py`, the workflow instance in
`flow.py` (imported everywhere as `from flow import flow`), execution in `run.py`, and analysis in
`visualize.py` or a report notebook. Exploration stays out of the pipeline entirely, under `eda/`.
See [data-science project structure](claude-plugin/project-structure.md) for why that layout is
load-bearing rather than decorative.

## Two ways to do it

### 1. The one-command path with Claude Code

If you use [Claude Code](claude-code-for-data-science.md), install the oryxflow plugin and let it
drive the restructuring:

```text
/plugin marketplace add https://github.com/oryxintel/oryxflow-claude-plugin.git
/plugin install oryxflow@oryxflow
```

Order matters — there are two commands, and migrate builds *into* what init-project creates:

1. **`/oryxflow:init-project`** — scaffolds the runnable project structure if the directory
   doesn't have one yet. It never overwrites files you already have. Skip it only if `tasks.py`
   and `flow.py` already exist.
2. **`/oryxflow:migrate`** — reads your notebooks and scripts (it reads them, it does not run
   them), builds the step-to-task map, and shows you the plan.

It's **plan-then-apply**, deliberately. You see the proposed task map — each source step, the task
name it becomes, what it saves, what it depends on — plus which constants become parameters, where
the plots and probes and helpers land, and anything that *doesn't* decompose cleanly (an
interactive cell needing a human in the loop, two concerns tangled in one block) flagged for you
to resolve rather than silently split. Nothing is written until you say go. Then it builds up one
task at a time, running the flow after each, so a break surfaces at the task that caused it
instead of five tasks later.

One rule worth knowing before you start: **it never deletes your source.** The original notebooks
and scripts are the spec it migrates *from* and the oracle it checks results against, so they stay
exactly where they are. See [plugin commands](claude-plugin/commands.md) for the full command set.

### 2. By hand, incrementally

The same migration done manually is a short loop: pick one step, make it a task, run it, move on.
The mechanics of turning functions into task classes — the `run()` body, `requires()`,
parameters — are covered in the [transition guide](transition.md); the
[notebook-to-pipeline walkthrough](../blog/posts/notebook-to-pipeline.md) tells the same story as a
narrative if you'd rather read it start to finish.

Start with the step that hurts most, which is almost always the slow load. Wrap it in a task, run
it once, and feed the rest of your existing cells from `flow.outputLoad()`. That one step already
buys you the lineage — the result is now tied to the code and parameters that made it — and, since
it's saved under that identity, you stop paying for the load every time. You rewrote almost
nothing, and you can stop there for a week if you want.

## How to cut a notebook into tasks

The hard part of migrating isn't the syntax, it's deciding where one task ends and the next
begins. Use the seams that are already in the code:

- **Every block that consumes inputs and produces an intermediate is a candidate task.** A loaded
  frame, a cleaned frame, a feature matrix, a fitted model, an evaluation table — each of those is
  one task.
- **Every `read_csv` / `read_excel` / database pull at the top is a source task.** No `requires`,
  and its path comes from `cfg.py`, not a string typed into the body.
- **Every `to_csv(...)` followed later by a `read_csv(...)` is one dependency edge.** That pair
  becomes `self.save(...)` upstream and `self.inputLoad()` downstream, and the intermediate file
  simply goes away — oryxflow owns where data lands.
- **Every magic constant becomes a parameter or a config value.** Thresholds, date ranges, model
  choices belong in `flow_params.py`; the source data directory and environment belong in
  `cfg.py`.
- **Name each task for the OUTPUT it produces, never the verb.** `SalesClean`, `SalesFeatures`,
  `MarginModel` — not `GetData`, `Process`, `RunModel`. The output is what downstream code asks
  for and what the cache is keyed on, so the name reads as a noun. Order the words broad to narrow
  so related tasks share a leading token and cluster together.

### Before: one linear script

Here's a typical notebook flattened into the script it really is. Three problems are load-bearing:
the CSV handoff in the middle, the two constants, and the fact that correctness depends on running
it top to bottom without ever editing a cell.

<!--phmdoctest-skip-->
```python
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

df = pd.read_csv('data/sales_raw.csv', parse_dates=['order_date'])   # slow, re-read every time
df = df[df['amount'] > 0]                                            # magic constant
df['region'] = df['region'].str.strip().str.lower()
df.to_csv('data/clean_v3.csv', index=False)                          # hand-managed handoff

df = pd.read_csv('data/clean_v3.csv', parse_dates=['order_date'])    # ...is this file current?
df['amount_log'] = np.log1p(df['amount'])
df['days_since'] = (pd.Timestamp('2026-01-01') - df['order_date']).dt.days   # magic date

X = df[['amount_log', 'days_since']]
model = LinearRegression().fit(X, df['margin'])
print(model.score(X, df['margin']))                                  # the number, in scrollback
```

### After: four tasks

The same work, cut at its seams. Each task is named for what it produces, declares what it needs,
and ends in `self.save(...)`. The two constants are now parameters, so the cached output of each
task is tied to the values that made it.

<!--phmdoctest-skip-->
```python
from datetime import date
import numpy as np
import pandas as pd
import oryxflow
from sklearn.linear_model import LinearRegression


class DataSales(oryxflow.tasks.TaskPqPandas):
    """Raw order rows, exactly as delivered by the source export.

    Out: one row per order; order_date parsed to datetime.
    """
    def run(self):
        self.save(pd.read_csv('data/sales_raw.csv', parse_dates=['order_date']))


@oryxflow.requires(DataSales)
class SalesClean(oryxflow.tasks.TaskPqPandas):
    """Order rows with non-positive amounts dropped and region normalized.

    In:  raw orders (from DataSales).
    Out: same columns; region lowercased and stripped.
    """
    amount_min = oryxflow.FloatParameter(default=0.0)

    def run(self):
        df = self.inputLoad()
        df = df[df['amount'] > self.amount_min]
        df['region'] = df['region'].str.strip().str.lower()
        self.save(df)


@oryxflow.requires(SalesClean)
class SalesFeatures(oryxflow.tasks.TaskPqPandas):
    """Model-ready features, one row per order.

    In:  cleaned orders (from SalesClean).
    Out: amount_log, days_since, margin. days_since is measured from asof_date.
    """
    asof_date = oryxflow.DateParameter(default=date(2026, 1, 1))

    def run(self):
        df = self.inputLoad()
        df['amount_log'] = np.log1p(df['amount'])
        df['days_since'] = (pd.Timestamp(self.asof_date) - df['order_date']).dt.days
        self.save(df[['amount_log', 'days_since', 'margin']])


@oryxflow.requires(SalesFeatures)
class MarginModel(oryxflow.tasks.TaskPickle):
    """Fitted linear model predicting margin from the order features.

    In:  feature matrix (from SalesFeatures).
    Out: the fitted estimator (pickled).
    """
    def run(self):
        df = self.inputLoad()
        X, y = df[['amount_log', 'days_since']], df['margin']
        self.save(LinearRegression().fit(X, y))
```

Pick the task type by what the step saves: `TaskPqPandas` for a DataFrame (the default and the
fastest), `TaskPickle` for a model or any other Python object, `TaskJson` for a small dict. A step
that genuinely produces two outputs declares them — `persists = ['train', 'valid']` — saves a dict
with those keys, and downstream you read one with `self.inputLoad(keys='train')`. More task types
and patterns are in [writing tasks](tasks.md).

Running it is one object, and it's the object every other file in the project imports:

<!--phmdoctest-skip-->
```python
flow = oryxflow.Workflow(MarginModel, {'amount_min': 0.0})

flow.preview()                            # print the execution plan, run nothing
flow.run()                                # runs DataSales -> SalesClean -> ... in order

model = flow.outputLoad()                 # the final task's output
df_features = flow.outputLoad(SalesFeatures)   # any intermediate, by name
```

Notice what disappeared: you never call `DataSales` yourself, you never name an intermediate file,
and the parameters you swept are recorded rather than remembered. Change `amount_min` and you get
a *new* cached result alongside the old one, not an overwritten file. See
[running workflows](run.md) and [workflow parameters](advparam.md).

## Migrate incrementally, and let exploration stay exploration

Two habits keep a migration from turning into a rewrite.

**Convert one step at a time, in dependency order.** Extract the config and parameters first, then
add the root loader and run it, then add each downstream task and run again. A pipeline that runs
after every step means a failure points at the task you just wrote. Re-run after an edit and
oryxflow reruns the edited task and everything downstream on its own — see
[automatic code invalidation](managing-workflows.md#automatic-code-invalidation).

**Exploration doesn't have to become tasks at all.** Checking a schema, eyeballing a distribution,
testing a parsing guess — that work is read-only, produces no pipeline artifact, and belongs in a
plain script under `eda/<subject>/<name>.py`, each stating the question it answers. Promote a probe
into a task only when it turns out to be load-bearing, meaning something downstream now depends on
its result. That's what makes the structure scale in both directions: simple scripts on day one,
any complexity later, and no cliff in between where you have to stop and rearchitect.

## How you know the migration is correct

A migration that runs is not a migration that's right. Four checks, in order:

1. **Keep the original as the spec.** Don't delete or overwrite the notebooks and scripts you
   migrated from. They are the only record of what the pipeline is supposed to do, and the oracle
   for the next check. Move them under a `legacy/` folder if they're in the way.
2. **`flow.preview()` before you run anything.** It prints the task tree — what will run, what's
   already cached — so you can confirm the graph you built is the graph you meant. If a dependency
   is missing, you see it here, not in a wrong number later.
3. **Compare a headline number.** Run the flow, then `flow.outputLoad()` the result and check one
   real figure against the same figure in the original. This is the step people skip, and it's the
   only one that catches silently changed behavior.
4. **Run it twice.** The second `flow.run()` should do nothing at all — every task already current.
   That's the proof the graph is wired correctly and the engine can tell what's fresh;
   `print(result.summary())` on the returned result spells out how many tasks ran versus were
   skipped, and `result.ran` lists exactly which recomputed.

<!--phmdoctest-skip-->
```python
result = flow.run()
print(result.summary())        # e.g. "4 ran successfully" the first time, 0 the second
print(result.ran)              # which tasks actually recomputed
```

From then on, `python run.py` reproduces the whole analysis from raw data, and that command is the
answer to "how was this made?"

## What not to migrate

Migrating everything is its own kind of mess. Leave two things alone:

- **Genuinely throwaway code.** If you're poking at a new dataset for twenty minutes and will
  never run any of it twice, task scaffolding is pure overhead. A good rule: migrate a step the
  second time you wait for it to recompute something that didn't change.
- **Production scheduling.** oryxflow is built for the research loop, not for cron, retries,
  alerting, and SLAs across a fleet. If you need those, an orchestrator does that job and oryxflow
  sits beside it, keeping the pipeline itself reproducible and its lineage on record.

Also not this page: if your project is *already* pipeline-shaped but imports the old `d6tflow`
package, that's a package rename rather than a restructuring — see
[migrating from d6tflow](../blog/posts/migrate-from-d6tflow.md).

## Frequently asked questions

**How do I restructure a messy data-science project into a pipeline?**
Read the notebook or script first and cut it at its seams: every block that consumes some inputs
and produces an intermediate — a loaded frame, a cleaned frame, a feature matrix, a fitted model —
becomes one task, named for the intermediate it produces. Wire the tasks with `@oryxflow.requires`,
end each `run()` with `self.save()`, and hoist the magic constants into parameters. Then build the
tasks up in dependency order, running the flow after each one, so a break surfaces at the task
that caused it instead of five tasks later.

**Do I have to convert the whole notebook at once?**
No, and you shouldn't. Convert the step that hurts most first — usually the slow data load — run
it once so its result caches, and let the rest of your cells keep working, now fed by
`flow.outputLoad()`. Add the next step only when it earns it. You have a working pipeline after
every single step, so there is never a half-rewritten state to recover from.

**How do I know the migration didn't change my numbers?**
Keep the original notebook or script in place and treat it as the specification: it is the only
record of what the pipeline is supposed to do, and it is the oracle you check against. Once the
migrated flow runs end to end, compare a headline number from `flow.outputLoad()` against the same
number in the original. A migration that runs but yields different numbers has silently changed
behavior, so diagnose the difference rather than declaring success because the run finished without
an error.

**Does exploratory analysis have to become oryxflow tasks?**
No. Read-only probes — checking a schema, eyeballing a distribution, testing a parsing guess —
stay plain scripts under `eda/<subject>/<name>.py`, each stating the question it answers, and they
write no pipeline output. Promote a probe into a task only when it turns out to be load-bearing,
meaning something downstream depends on its result. That way there is no cliff between exploring
and having a pipeline.

**Why does an AI coding agent make a messy notebook project worse instead of better?**
Because the mess is invisible state, and an agent cannot see it. It cannot tell which cells you
actually ran, which intermediate file is current, or which constant produced the number in the
notebook's output, so it re-derives that state by guessing — and writes plausible code on top of a
wrong guess. Moving the run order into `@oryxflow.requires` and the intermediates into a cache
makes that state readable instead of guessable, which is why the same structure that helps a human
helps an agent more.

## Takeaway

- The payoff isn't tidier code, it's that **a stale intermediate or an unrecorded parameter can no
  longer hand you a confident wrong number** — and an AI coding agent stops guessing at state it
  can't see.
- Cut at the seams already in your code: each block that produces an intermediate becomes one
  task, named for that intermediate; the `to_csv` / `read_csv` handoffs become
  `self.save()` / `self.inputLoad()`; the magic constants become parameters.
- Migrate **incrementally**, keep the original as the spec, and verify by comparing one headline
  number — then confirm a second run does nothing.
- With Claude Code, `/oryxflow:init-project` then `/oryxflow:migrate` does the mapping and builds
  it up one task at a time, showing you the plan before it writes anything.

Next: the [transition guide](transition.md) for the hands-on conversion mechanics, the
[Claude Code plugin](claude-plugin/index.md) for the automated path,
[writing tasks](tasks.md) for the task types and patterns you'll reach for, and
[why oryxflow](why-oryxflow.md) for where this fits next to trackers and orchestrators.
