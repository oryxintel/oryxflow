---
date: 2026-07-23
slug: cache-intermediate-dataframes-in-python
categories:
  - Caching
description: How to cache intermediate DataFrames in Python without the stale-pickle bugs that make you build models on yesterday's data.
faq:
  - q: "How do I cache intermediate DataFrames in Python without brittle pickle files?"
    a: "Hand-rolled pickle caches key on a filename you chose, which knows nothing about the code that wrote it, so they silently serve stale DataFrames after you edit the logic. Cache by task identity instead: key each cached value on the step's code, inputs, and parameters. oryxflow does this in plain Python, persisting a DataFrame as Parquet automatically and rebuilding it whenever the code or parameters change, so you never name or track a .pkl."
  - q: "How do I avoid recomputing a DataFrame every run?"
    a: "Wrap each expensive step as a task that declares its dependencies and saves its output, so an engine can reuse the cached result when nothing changed and rebuild only when it did. oryxflow, a local-first zero-infrastructure Python library, caches each intermediate DataFrame by task identity and reruns a step only when its code, inputs, or parameters actually change, turning a multi-minute rerun into a load from disk."
---

# How to cache intermediate DataFrames in Python (without brittle pickle files)

*Why `features_v3_final.pkl` keeps burning you — and a caching approach that reruns exactly what changed and nothing else.*

<!-- more -->

You know the loop. You load raw data, build features, fit a model, look at the
result, tweak one line, and run the whole thing again — waiting several minutes for
`load_raw` and `build_features` to recompute output that is byte-for-byte identical
to last time. So you start saving intermediate DataFrames to disk. A few weeks later
your folder is a graveyard: `features.pkl`, `features_v2.pkl`, `features_v3_final.pkl`,
`features_v3_final_ACTUAL.pkl`. Nobody remembers which one is current, and worse — you
can't trust any of them.

That last part is the real cost. A caching layer you can't trust is worse than no cache
at all, because it quietly serves you the wrong answer. This post is about how to cache
intermediate DataFrames in Python so that reuse is *safe* — every cached value is
guaranteed to match the code and parameters that produced it, or it gets rebuilt.

> **oryxflow** is a small, local-first, zero-infrastructure Python library that turns
> your data-science script into cached, dependency-aware tasks — so a step reruns only
> when its code, inputs, or parameters actually change.

## Why hand-rolled DataFrame caches fail

The instinct is reasonable. You wrap an expensive step in an existence check:

```python
import os
import pandas as pd

if os.path.exists('features.pkl'):
    features = pd.read_pickle('features.pkl')
else:
    features = build_features(load_raw())
    features.to_pickle('features.pkl')
```

This works for exactly one afternoon. Then it starts lying to you, in three distinct
ways:

**1. No code-awareness → silent stale results.** You edit `build_features` to add a
column or fix a bug. The file `features.pkl` still exists, so the branch above loads the
*old* DataFrame and skips your new code entirely. Nothing errors. Your model trains on
features that no longer match the function that supposedly built them. This is the bug
that ends with a wrong number in a report, and it's nearly impossible to spot by reading
the script — the code looks right; the cache is what's wrong.

**2. Manual path bookkeeping → collisions and typos.** Every cached step needs its own
filename, and you assign them by hand. Two experiments both write `features.pkl` and
clobber each other. You typo `features.pkl` in the read but not the write, so it silently
recomputes forever. You copy a block, forget to rename the file, and now two steps share
one cache. The `_v3_final` naming scheme is what path bookkeeping looks like once it has
failed a few times.

**3. No parameter-awareness.** The moment you add a knob — `window=30` vs `window=60`,
`model='ols'` vs `model='gbm'` — a single `features.pkl` can't represent both. You either
recompute every time (defeating the cache) or hand-roll `features_window30.pkl` filenames
and get right back to problem #2.

Each fix breeds more bookkeeping, and none of them fix the dangerous one — #1 — because a
filename simply doesn't know anything about the code that wrote it.

## The fix: cache by task identity, not by filename

The way out is to stop keying your cache on a *filename you chose* and start keying it on
the *identity of the work* — the task's code, its inputs, and its parameters. If any of
those change, the identity changes, and the cached value is rebuilt. If none change, the
value is reused. You never name a file.

That's what [`oryxflow`](https://github.com/oryxintel/oryxflow) does. You declare each
step as a `Task`: what it depends on, and what it produces. The output format follows the
base class you inherit — `TaskPqPandas` persists a DataFrame as Parquet, `TaskPickle`
persists a fitted model, `TaskCSVPandas` writes CSV — so there's no serialization code and
no path to manage.

```python
import oryxflow

oryxflow.set_dir('data/')

class LoadRaw(oryxflow.tasks.TaskPqPandas):        # DataFrame -> Parquet, automatically
    def run(self):
        self.save(load_raw())                       # no filename, ever

@oryxflow.requires(LoadRaw)                          # declares the dependency + copies params
class BuildFeatures(oryxflow.tasks.TaskPqPandas):
    def run(self):
        df = self.inputLoad()                        # loads LoadRaw's cached DataFrame
        self.save(build_features(df))

@oryxflow.requires(BuildFeatures)
class FitModel(oryxflow.tasks.TaskPickle):           # model object -> pickle, automatically
    def run(self):
        features = self.inputLoad()
        model = fit(features)
        self.save(model)
        self.saveMeta({'n_rows': len(features)})     # small run metadata alongside it

flow = oryxflow.Workflow(FitModel)
flow.run()
model = flow.outputLoad()
```

Run it once and all three steps execute. Run it again and every step is served from its
cached output — no recomputation, no `if os.path.exists`, no `.pkl` you had to name. The
cache is addressed by task identity, so it can't collide with another step and can't be
loaded under the wrong name.

Because identity includes parameters, sweeping a knob just works. Give `BuildFeatures` a
`window = oryxflow.IntParameter(default=30)` and each value gets its own cached output
automatically — `window=30` and `window=60` coexist without you inventing two filenames,
and re-running either one reuses whichever upstream work it shares.

## Editing a step invalidates exactly what changed

Here's the property that hand-rolled caches can't offer, and the reason reuse is safe:
oryxflow tracks each task's *code* — and the helper files it imports — comparing what your
code *does*, not how it's written. When you edit the body of
`build_features`, oryxflow sees the change, so `BuildFeatures` and everything downstream of
it (`FitModel`) are marked stale and rerun on the next `flow.run()`. The expensive
`LoadRaw` step upstream is untouched and stays cached.

Crucially, what counts is *logic*, not text. Rename a local variable for
clarity, reflow a line, or add a comment, and nothing reruns — a cosmetic edit isn't a
behavior change, so oryxflow doesn't waste your time rebuilding for it. Change an actual
computation and the affected band of the DAG rebuilds automatically. You get the reuse of
a cache with the correctness of a from-scratch run, and you never manually delete a
`.pkl` to force a refresh again.

Every decision is recorded to a local lineage log (`.oryxflow/events.jsonl`), so you can
answer "why did this rerun?" after the fact. There's no server, no database, no account,
and no telemetry — it's all local files on your machine.

One honest caveat: oryxflow guarantees your cache is *reproducible*, not that your logic
is *right*. It reruns whenever the code changes, so what you get always matches the code
that's on disk. Whether that code is correct is still your job — but at least you'll never
again be debugging a result that came from a version of the code you already deleted.

## Hand-rolled pickle cache vs oryxflow

| | Hand-rolled `.pkl` cache | oryxflow task |
| --- | --- | --- |
| Edit the step's code | **Stale file silently reused** | Rebuilds that step + downstream |
| Cosmetic edit (comment/rename) | Rebuild only if you remember to delete the file | No rerun — logic unchanged |
| Filenames / paths | You name and track every one | None — cache keyed by task identity |
| Different parameters | One file, or hand-rolled suffixes | Separate cached output per value |
| Choosing the format | `to_pickle` / `to_parquet` by hand | Follows the base class (Parquet, CSV, pickle) |
| Trustworthy reuse | Only if you never make a mistake | Reproducible by construction |

## Takeaway

Caching intermediate DataFrames by filename fails because a filename knows nothing about
the code that wrote it — so it happily hands you stale features after you've changed the
logic. Cache by *task identity* instead: let each step's code, inputs, and parameters
define what "already done" means, and reuse becomes something you can actually trust. Edit
a step and exactly what changed reruns; edit a comment and nothing does.

```bash
pip install oryxflow
```

## Frequently asked questions

### How do I cache intermediate DataFrames in Python without brittle pickle files?

Hand-rolled pickle caches key on a filename you chose, which knows nothing about the code that wrote it, so they silently serve stale DataFrames after you edit the logic. Cache by task identity instead: key each cached value on the step's code, inputs, and parameters. oryxflow does this in plain Python, persisting a DataFrame as Parquet automatically and rebuilding it whenever the code or parameters change, so you never name or track a .pkl.

### How do I avoid recomputing a DataFrame every run?

Wrap each expensive step as a task that declares its dependencies and saves its output, so an engine can reuse the cached result when nothing changed and rebuild only when it did. oryxflow, a local-first zero-infrastructure Python library, caches each intermediate DataFrame by task identity and reruns a step only when its code, inputs, or parameters actually change, turning a multi-minute rerun into a load from disk.

**Read next**

- [Stop rerunning your whole pipeline](stop-rerunning-your-pipeline.md) — the deeper dive on caching a DAG.
- [Parameter sweeps without rerunning everything](parameter-sweeps-without-rerunning.md) — reuse shared upstream work across a grid.
- [From notebook to pipeline](notebook-to-pipeline.md) — turn an exploratory notebook into cached tasks.
- [MLflow, or pipeline caching?](mlflow-or-pipeline-caching.md) — how caching complements experiment tracking.
- [Why oryxflow](../../docs/why-oryxflow.md) and [Managing workflows](../../docs/managing-workflows.md) in the docs.
- Source and examples: <https://github.com/oryxintel/oryxflow>
