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

The **one** thing completeness can't key on automatically is your ``run()`` **code**. For that,
each task carries a ``code_version`` token: bump it when you change the task's logic and the task
*and everything downstream* recomputes on the next run — no resets to chain (see
:ref:`code-versioning` below). ``reset()`` remains for the cases no code token covers: deleting
outputs, or forcing a recompute when something the system can't see changed (a data file, an
external API).

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

.. _code-versioning:

Code changes: bump ``code_version``, don't reset
------------------------------------------------------------

Suppose you improve ``TrainModel`` — say you add a second scoring metric to its ``run()``.
Parameters didn't change, so the cache would happily serve the stale output. Declare a
``code_version`` and bump it in the same edit as the logic change:

.. code-block:: python

    @oryxflow.requires(LoadData)
    class TrainModel(oryxflow.tasks.TaskCachePandas):
        code_version = 2          # was 1; bump whenever this task's logic changes
        alpha = oryxflow.FloatParameter()
        def run(self):
            ...

On the next ``run()`` every ``TrainModel`` recomputes **and so does everything downstream of it**
(``Tune`` here), overwriting in place at the same paths. ``LoadData`` is untouched. The propagation
is automatic: each task's *code fingerprint* folds its dependencies' fingerprints, so a bump
upstream invalidates the whole band below it — the granular equivalent of
``reset_upstream(..., only=...)`` with zero bookkeeping. ``code_version`` can be an int or a
string (``'v2-log-features'``).

Tasks without a ``code_version`` behave exactly as before — the feature is inert until you opt a
task in, and opting in never invalidates an existing cache: the first run after you *add*
``code_version`` treats the existing output as current ("grandfathering") and just records the
baseline.

**Safe adoption on-ramp.** Because of grandfathering, add ``code_version`` to existing tasks in a
change that edits *nothing else* — then every task grandfathers against output its current code
really produced, and every later bump is clean. If you add it *because you just changed the code*,
also ``reset()`` that task once (grandfathering would otherwise bless the stale output; a
best-effort mtime guard warns when it can detect this, but don't rely on it — and note the guard
is noisy right after ``git checkout``/``clone``, which resets source mtimes).

How much to adopt — you don't need it on every class
------------------------------------------------------------

``code_version`` is per-task opt-in, and the propagation does most of the covering for you:
because each task's fingerprint folds its dependencies' fingerprints, a task *without*
``code_version`` still reruns when a versioned upstream bumps. So declare it only on tasks whose
**own** logic you want tracked — typically your key output tasks and the expensive upstreams
feeding them — and let everything in between ride the propagation. Versioned and unversioned
tasks coexist freely in one pipeline.

The token is one line of ceremony per task, and that line is the point: the cache decision
("this logic changed, downstream must recompute") becomes an explicit, diffable part of the
same edit — visible in the code review and in ``git log``, instead of living in a side-channel
``reset()`` someone ran (or forgot to run) in a session.

**Working without ``code_version``** (a task — or a whole project — that hasn't opted in)
is classic oryxflow: parameters drive identity, and after editing a task's logic you
``flow.reset(Task)`` before running — the completeness cascade recomputes everything downstream.
That loop works; what you give up is the safety net. For unversioned tasks no staleness warning
fires and nothing forces the rerun, so a forgotten reset silently serves stale output — which is
exactly the failure mode ``code_version`` exists to catch. ``reset()`` also remains the right
verb *with* versioning for what no code token can see: changed data files, external APIs, a
suspect cache (see the table below).

The staleness warning and its three exits
------------------------------------------------------------

Forgetting to bump is the failure mode this feature exists to catch, so oryxflow watches your code
as an *advisory*: every run it hashes the task's defining module and every project-local module it
transitively imports (AST-normalized, so **comments, docstrings and formatting edits never
warn**). If the code changed but the ``code_version`` didn't, the run warns — visibly, without
``enable_logging()``::

    StalenessWarning: task TrainModel: pipeline/train.py changed since cached run; code_version
    still 2 -- reusing cached output. Bump code_version to recompute, or
    oryxflow.accept_code(TrainModel) only if certain the output is equivalent -- when unsure,
    bump (best-effort check: can't see data files or dynamic calls).

Answer every warning with one of its three exits — they are not equal in risk:

* **Bump** ``code_version`` — the change affects output. Safe by default: it recomputes.
* **Reset** (``flow.reset(...)``) — recompute regardless, no version bookkeeping. Also safe.
* ``oryxflow.accept_code(TrainModel)`` — the change is output-equivalent (rename, refactor,
  added log line): re-stamps the stored hashes without rerunning. **This is the one exit that
  can silently bless a stale output** — use it only when you're certain; when unsure, bump
  (recompute is cheap insurance). ``accept_code()`` with no argument bulk-accepts everything
  currently warned (note: hashing is file-level, so one helper edit flags every task importing
  that file — bulk accept is the ergonomic answer when you've judged the edit equivalent).

Be honest about the blind spots: the hash can't see data-file contents, external APIs, dynamic
imports or monkeypatching. Where it can't see, it stays silent — a missing warning never means
"verified unchanged". And the warning follows the *import* graph while reruns follow the
*dependency* graph: a downstream task that consumes a changed helper's output only via
``requires()`` (without importing the file) won't warn — the bump is what carries staleness
across a pure data dependency.

Which verb, when
------------------------------------------------------------

.. list-table::
   :header-rows: 1
   :widths: 42 24 34

   * - I changed…
     - Do this
     - Why
   * - a value/knob that is a ``Parameter``
     - nothing
     - new identity auto-reruns; old output kept side by side
   * - logic (this task's ``run()`` or a helper it imports), output will differ
     - bump ``code_version``
     - propagates downstream, recomputes
   * - code, but output is provably identical (rename, extract, log line)
     - ``accept_code()`` — only if certain; when unsure, bump
     - re-stamps without recompute; the one non-recomputing exit
   * - nothing the system can see, but I need a fresh compute (data file changed, suspect cache)
     - ``reset()``
     - forces recompute when no code fingerprint moved
   * - I want the outputs gone
     - ``reset()``
     - delete
   * - logic of a task that has **no** ``code_version`` (not opted in)
     - ``reset()`` it before running — or adopt ``code_version`` now
     - no fingerprint moves and no warning fires; the rerun is on you
   * - first time adding ``code_version`` to a task I also just edited
     - bump **and** ``reset()`` once (or add it in an edit-free change first)
     - grandfathering would otherwise bless stale output

Keeping old versions side by side
------------------------------------------------------------

By default a bump overwrites in place. To keep previous versions at readable paths — the
compare-two-versions workflow — set ``keep_versions = True`` on the task (ideally with a string
version):

.. code-block:: python

    class TrainModel(oryxflow.tasks.TaskCachePandas):
        code_version = 'v2-log-features'
        keep_versions = True     # outputs live at data/TrainModel/v2-log-features/...

Bumping then writes to a new ``v.../`` directory and the old one stays on disk. Note that
*turning on* ``keep_versions`` for an existing task relocates its output path (it gains the
version segment), so the task recomputes once and the old non-versioned artifact is left behind —
it's not a transparent toggle.

How the records travel
------------------------------------------------------------

The code fingerprints live in one small JSON file per data directory
(``<dirpath>/.oryxflow-code-status.json``). It describes those exact artifacts, so **move or restore a data
directory whole** — file and artifacts together, always via the same channel. An artifact without
a record is simply grandfathered as current (the same trust level every file has today); partial
restores are on you. Correctness depends only on this file, never on the event log.

The event stream: what ran, when, and why
------------------------------------------------------------

Every run appends structured events to a plain-text JSONL stream — always on, costing
microseconds (writes are asynchronous), so the record exists even when your script discards the
run result. The file layout is a contract you can script against:

* current month: ``.oryxflow/events.jsonl`` (a stable head — ``tail -30 .oryxflow/events.jsonl``
  always shows the latest activity)
* offloaded months: ``.oryxflow/events-YYYYMM.jsonl`` (immutable once offloaded)
* all history: glob ``.oryxflow/events*.jsonl``

Events include ``run_started`` / ``task_ran`` / ``task_failed`` / ``run_finished`` /
``code_warning`` / ``code_accepted`` / ``task_log``. Each ``task_ran`` carries the full recipe —
params, ``code_version``, code fingerprint, source hashes, git SHA, duration — and the **reason**
it ran (``output missing`` / ``code change (1 -> 2)`` / ``upstream rerun``), so "why did this
recompute?" and "was this produced by current code?" are queries, not guesses. Anything a task
logs via ``self.logger`` is captured as ``task_log`` events — log your decision-relevant scalars
(``self.logger.info("corr_avg={}", corr)``) and they become next session's memory.

From Python:

.. code-block:: python

    oryxflow.events.print_status()   # session-start orientation, printed: pending code
                                     # warnings, last run per family, recent failures
    oryxflow.events.status()      # the same as data (a dict) -- it returns, doesn't print
    oryxflow.events.runs(task_family='TrainModel', last=2)   # diff params/code_version/hashes
    oryxflow.events.runs(flow='Retail')                      # per-flow when using WorkflowMulti

From the shell (no Python needed)::

    tail -30 .oryxflow/events.jsonl
    grep TrainModel .oryxflow/events*.jsonl
    jq 'select(.type=="task_ran") | {task_id, reason, duration_s}' .oryxflow/events.jsonl

``run()`` returns the same story in memory: ``result.reasons`` maps each task that ran to why, and
``result.warnings`` lists unacknowledged code changes. **Verify your invalidations took**: after a
bump, the next run must show the intended tasks in ``result.ran`` with reason
``code change (1 -> 2)`` — ``ran=0`` after an intended invalidation means it didn't reach the
cache; ``ran=0`` on an untouched pipeline is the healthy "cache is trusted" signal.

Add ``.oryxflow/`` to your ``.gitignore`` — run records are high-frequency exhaust, not source.
Disable entirely with ``oryxflow.settings.events = False``.

What caching does *not* protect against
------------------------------------------------------------

Everything above solves one family of problems — the *mechanical* one: staleness, provenance, and
memory. That family genuinely yields to tooling, and a cached result you've kept fresh is worth
trusting *as a computation*. But be clear about the boundary: oryxflow guarantees a result was
produced by the code and inputs it records — **not** that the result is correct. A perfectly
versioned, fully reproducible pipeline can still hand you a wrong number, and nothing here will
flag it:

* a join that should be many-to-one runs many-to-many and inflates every downstream aggregate;
* a percentage computed against the wrong denominator, or a ``groupby`` that silently drops
  null-keyed rows;
* a dtype coercion that eats a ZIP code's leading zero, or a timezone shift that moves rows a day;
* a backtest that peeks at the future, or a correlation read off overlapping windows as if the
  points were independent;
* a number quoted from memory or eyeballed off a chart instead of from the saved artifact.

None of these raise; each completes cleanly and prints something plausible. Versioning the code
just makes the wrong join *reproducible*. These are caught by **habit, not machinery** — validate
the merge and assert the row relationship, look at the frame's shape and null counts before
stating a finding, quote every number from an artifact you can re-open. (Note this is a different
thing from the hash blind spots above: a changed data file is invisible but has a verb —
``reset`` the loader; a wrong join has no verb, only vigilance.) That is why the
:doc:`Claude Code plugin <claude-plugin>` ships those conventions alongside the library, delivered
at the point you're writing the task rather than in a review afterward. And the judgment calls —
is this method right for the question, is this effect within noise, does the data actually behave
the way I assumed — are yours; no cache decides them.

.. _claude-md-snippet:

CLAUDE.md snippet for AI-agent projects
------------------------------------------------------------

Projects driven by AI coding agents (without the oryxflow Claude plugin) should paste this into
their ``CLAUDE.md``:

.. code-block:: markdown

    ## oryxflow cache & provenance rules

    1. Session start / after `/clear`: call `oryxflow.events.print_status()` — pending code
       warnings, last run per task family, recent failures — before assuming anything about
       cache state (`events.status()` returns the same as a dict for filtering; it prints
       nothing). No-Python fallback: `tail -30 .oryxflow/events.jsonl`.
    2. When you change a task's logic (its `run()` or a helper module it uses): bump that task's
       `code_version` **in the same edit**. Do not hand-chain `reset()` calls for code changes —
       the bump propagates downstream automatically. If the task has NO `code_version` (not
       opted in), `reset()` it before running instead — no warning fires for unversioned tasks,
       so the rerun is on you. `code_version` is per-task opt-in; downstream tasks without it
       still rerun when a versioned upstream bumps.
    3. First time adding `code_version` to a task: if you're adding it because you just changed
       the code, also `reset()` that task once — grandfathering treats existing output as current.
       Safest: adopt `code_version` in a change that edits nothing else.
    4. Answer every staleness warning with one of its three exits — bump (semantic change),
       `oryxflow.accept_code(TaskX)` (output-equivalent; only if certain — when unsure, bump), or
       reset (recompute regardless). Never leave one firing across runs.
    5. After a run, read the returned result: `result.reasons` says why each task ran;
       `result.warnings` lists unacknowledged code changes. Verify invalidations took: after a
       bump the next run must show the task in `result.ran` with reason `code change (1 -> 2)`;
       `ran=0` after an intended invalidation is a bug, not a convenient skip. Never hand-roll
       aggregation — `MultiRunResult` exposes `.ran`/`.complete`/`.failed`/`.reasons`/`.warnings`
       across flows, and the per-build verdict is logged durably as `run_finished` events.
    6. "The numbers changed and I don't know why": compare the last two runs —
       `oryxflow.events.runs(task_family='TaskX', last=2)` — and diff params, code_version,
       source_hashes.
    7. Log decision-relevant scalars inside `run()` via `self.logger.info(...)` — they're
       captured as `task_log` events and become next session's memory.
    8. Experiments you want side by side: string `code_version` plus `keep_versions = True`.
    9. Raw stream: current = `.oryxflow/events.jsonl`; offloaded months = `events-YYYYMM.jsonl`;
       all history = glob `events*.jsonl`. Prefer `events.runs()`/`status()` in Python.
    10. Data-file or external-API changes are invisible to the code hash — `reset()` is the verb
        for those.

Selectively reset when code versioning doesn't apply
------------------------------------------------------------

``reset`` remains the right tool when there's no code token to move: a data file changed, you
suspect a corrupt cache, or you simply want outputs deleted. The point is still to reset **only**
the family that changed, so the shared ``LoadData`` stays cached:

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
