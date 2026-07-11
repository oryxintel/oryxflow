.. _managing-complex-workflows:

Managing Complex Workflows
==============================================

As a project grows, the expensive part is rarely the final result — it's the *granular* work
underneath it. Fetching data from a slow or paid API. Training a model once per hyperparameter
setting. Re-running that work every time you tweak something downstream is what makes iteration
painful.

oryxflow's answer is to make each granular unit its own **task**, cached independently. The engine
tracks completeness per task, so when you change one thing it re-runs exactly what changed — and
leaves the expensive, unchanged work alone. This page walks through the pattern data scientists
reach for constantly:

#. **Iterate granular** — one task per item (per hyperparameter, per state, per input file), each
   cached on its own.
#. **Aggregate** — combine those outputs one level up into a single result.
#. **Selectively reset** — when you change one part of the pipeline, invalidate just that family
   of tasks and let everything downstream recompute. The expensive leaves stay cached.

Why this is (mostly) automatic
------------------------------------------------------------

A task is "complete" when its output exists **for its current parameters**. The task id is derived
from the task's parameters, so:

* **Change a parameter** and you get a new id → no output yet → the task runs. Old outputs for the
  old parameters are untouched.
* **Add a new item to the grid** (a new hyperparameter value, a new state) and only that new task
  runs; every existing one is already complete and is skipped.

Both cases are handled for you — this is oryxflow's parameter management. Because completeness is
checked recursively down the dependency graph, an aggregating task automatically recomputes when
any of its inputs change or a new input appears, with no bookkeeping on your part.

The **one** thing oryxflow can't detect is a change to your ``run()`` **code** — completeness keys
on outputs and parameters, not on source. When you edit a task's logic you tell oryxflow to reset
that task. The rest of this page is about doing that *selectively*, so a code change to a cheap
step never forces the expensive step to re-run.

Worked example: hyperparameter tuning
------------------------------------------------------------

The classic case: load a dataset once (expensive), train a model once per hyperparameter value
(the granular loop), then aggregate the scores to pick a winner.

.. code-block:: python

    import oryxflow
    import pandas as pd

    oryxflow.set_dir('data/')

    ALPHAS = [0.01, 0.1, 1.0, 10.0]          # the grid you iterate over — your own domain data

    class LoadData(oryxflow.tasks.TaskCachePandas):
        """Expensive: a slow/paid data pull you never want to repeat needlessly."""
        def run(self):
            self.save(load_training_data())   # imagine a long API call

    @oryxflow.requires(LoadData)              # wires requires(), copies params
    class TrainModel(oryxflow.tasks.TaskCachePandas):
        """One granular task per hyperparameter value. Cached independently."""
        alpha = oryxflow.FloatParameter()
        def run(self):
            df = self.inputLoad()             # the shared dataset, loaded once
            score = fit_and_score(df, alpha=self.alpha)
            self.save(pd.DataFrame({'alpha': [self.alpha], 'score': [score]}))

    class Tune(oryxflow.tasks.TaskCachePandas):
        """Aggregate: stack every trained model's score into one frame."""
        def requires(self):
            return {a: TrainModel(alpha=a) for a in ALPHAS}
        def run(self):
            df = self.inputLoadConcat()       # one row per alpha; 'alpha' column tags each
            self.save(df.sort_values('score', ascending=False))

Run it and read off the best hyperparameter:

.. code-block:: python

    flow = oryxflow.Workflow(Tune)
    flow.run()
    results = flow.outputLoad()               # every alpha's score, one frame
    best_alpha = results.iloc[0]['alpha']

The ``requires()`` dict is what fans the DAG out into one ``TrainModel`` per ``alpha``, and
``self.inputLoadConcat()`` stacks their outputs, tagging each row with that task's parameters so
your ``alpha`` column survives the concat. (The mechanics of dict-``requires()`` and
``inputLoadConcat`` are covered as reference in :doc:`Advanced: Dynamic Tasks <advtasksdyn>`.)

Extend the grid — nothing wasted
------------------------------------------------------------

Decide you want to try more values? Add them to ``ALPHAS`` and run again:

.. code-block:: python

    ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0]    # added 100.0
    flow.run()

Only ``TrainModel(alpha=100.0)`` runs — the four you already trained are complete and are skipped,
and the expensive ``LoadData`` is not touched at all. ``Tune`` recomputes because it now has a new
input. You didn't reset anything; adding a parameter value is enough.

Selectively reset when you change code
------------------------------------------------------------

Now suppose you improve ``TrainModel`` — say you add a second scoring metric to its ``run()``. That
is a *code* change, so you reset it explicitly. The point is to reset **only** the family you
changed, so the shared ``LoadData`` stays cached:

.. code-block:: python

    flow = oryxflow.Workflow(Tune)
    flow.reset_upstream(Tune, only=TrainModel)   # every TrainModel in the DAG, nothing else
    flow.run()

``reset_upstream(Tune, only=TrainModel)`` walks the whole graph upstream of ``Tune`` and
invalidates just the ``TrainModel`` instances — every ``alpha`` at once, found via the DAG so you
never hand-list them. ``LoadData`` is a *different* family and is left complete, so the slow data
pull does **not** repeat. On the next ``run()`` all four models retrain (their code changed) and
``Tune`` recomputes on top (recursive completeness), while ``LoadData`` is served from cache.

Contrast the three reset scopes you'll actually use:

* ``flow.reset(TrainModel(alpha=0.1))`` — one specific instance. Use when you're debugging a single
  case.
* ``flow.reset_upstream(Tune, only=TrainModel)`` — one *family*, everywhere it appears upstream.
  Use when you changed a task's code and want every instance of it recomputed, cheap dependencies
  preserved.
* ``flow.reset_upstream(Tune)`` — the whole upstream, including ``LoadData``. Use only when the raw
  inputs themselves are stale.

This is the core discipline: **reset at the level you changed, not above it.** It's what keeps a
tweak to a fast step from triggering hours of expensive re-fetching or re-computation.

Scaling up: hierarchies and independent experiments
------------------------------------------------------------

The same three moves scale in two directions.

**Deeper hierarchies.** Aggregators compose: a per-state task feeds a per-country task feeds a
per-sector task, each level a ``requires()`` fan-out combined with ``inputLoadConcat()``. The whole
tree is still one DAG, so preview, the run summary, and selective reset all reach every level.

**Independent experiments.** When you want to manage several runs separately — each with its own
output and its own reset scope — drive the top with :doc:`WorkflowMulti <workflow>` over a params
grid instead of one more aggregator, then combine across flows with ``outputLoadConcat``:

.. code-block:: python

    flow = oryxflow.WorkflowMulti(Sector, params={'sector': ['Retail', 'Office']})
    flow.run()
    dfall = flow.outputLoadConcat(Sector)                # all sectors, one tagged frame
    flow.reset_upstream(Sector, only=CountryFeatures)    # reset one family across every flow

A complete, runnable version of this multi-level dev loop — iterate on one ``(sector, country)``
first, then roll the change out to every flow *without re-fetching the expensive per-state source* —
is in ``docs/example-flow-multi.py``. The full reference for dict-``requires()``,
``inputLoadConcat``, ``outputLoadConcat``, and the ``only=`` reset filter is
:doc:`Advanced: Dynamic Tasks <advtasksdyn>`.

.. tip::

   This is exactly what the :doc:`Claude Code plugin <claude-plugin>` is built to manage. Describe
   the hierarchy in plain language and it writes the fan-out ``requires()`` and the
   ``inputLoadConcat()`` aggregators; when you iterate, it scopes the reset for you — resetting just
   the family you changed (``reset_upstream(..., only=...)``) so the expensive leaf tasks are
   preserved.
