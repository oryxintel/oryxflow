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

Completeness also keys on your **code**: by default oryxflow fingerprints each task's defining
module and every project-local module it transitively imports (AST-normalized, so comments,
docstrings and formatting never count), and a real logic change reruns the task *and everything
downstream* on the next run — no version attribute to maintain, no resets to chain (see
:ref:`code-versioning` below). Tasks that declare an explicit ``code_version`` opt out of the
automatic tracking: there, only bumping the token recomputes. ``reset()`` remains for the cases
no code hash covers: deleting outputs, or forcing a recompute when something the system can't see
changed (a data file, an external API).

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

Code changes: handled automatically
------------------------------------------------------------

Suppose you improve ``TrainModel`` — say you add a second scoring metric to its ``run()``.
Parameters didn't change, so a parameter-only cache would happily serve the stale output. With
oryxflow you do **nothing**: on the next ``run()`` every ``TrainModel`` recomputes **and so does
everything downstream of it** (``Tune`` here), overwriting in place at the same paths.
``LoadData`` is untouched. The same holds when you edit a *helper module* the task imports — the
fingerprint covers the task's defining module and everything it transitively imports inside your
project, so the change is caught where it actually lives, not just in ``run()``.

Two properties make this safe to leave on:

* **Cosmetic edits never recompute.** Files are hashed after AST normalization, so comments,
  docstrings and formatting changes produce no fingerprint change at all.
* **Propagation is automatic.** Each task's fingerprint folds its dependencies' fingerprints, so
  a change upstream invalidates the whole band below it — the granular equivalent of
  ``reset_upstream(..., only=...)`` with zero bookkeeping — and the fold is stored per task, so
  it works across separate runs and sessions, not just within one build.

The trade-off is coarseness: hashing is file-level, so editing one function in a shared
``utils.py`` recomputes every task that imports that file. Before an expensive run,
``flow.preview()`` shows exactly which band is pending; if you judge the edit output-equivalent
(a rename, an extracted function, a log line), ``flow.accept_code()`` re-stamps the current code
as accepted without recomputing (see the three exits below). When unsure, let it rerun —
recompute is cheap insurance, a wrongly-blessed cache is not.

**Expensive tasks don't recompute silently.** A rerun overwrites the old output, so burning a
long run must be a decision, not a side effect of a refactor: an auto-tracked task whose *last*
materialization took longer than ``settings.code_version_auto_expensive_s`` (default 600
seconds) is held complete when its code changes and the run **warns** instead — with the same
three exits (``reset()`` to recompute, ``accept_code`` if output-equivalent, or pin it with
``code_version`` to manage it by deliberate bumps). Set the threshold to ``None`` to make every
auto code change recompute.

Pinning a task: the explicit ``code_version``
------------------------------------------------------------

Sometimes automatic is the wrong sensitivity — a training task so expensive that a
refactor-triggered recompute must be a deliberate decision, or logic the hash can't see (dynamic
dispatch, behavior driven by a config file). Declare an explicit ``code_version`` and that task's
own logic is **pinned**: it recomputes only when you bump the token, and code edits without a bump
produce a staleness *warning* instead of a rerun:

.. code-block:: python

    @oryxflow.requires(LoadData)
    class TrainModel(oryxflow.tasks.TaskCachePandas):
        code_version = 2          # pinned: was 1; bump deliberately to recompute
        alpha = oryxflow.FloatParameter()
        def run(self):
            ...

``code_version`` can be an int or a string (``'v2-log-features'``). The pin is per-task,
free to toggle, and self-healing — the ``code_version`` line itself is stripped by the AST
normalization (like comments and docstrings), so typing the pin in, deleting it, or bumping it
is never a *source* change; the token is compared as its own dimension. The record always
stores *both* the token and the source hashes as of the last materialization, and completeness
compares the dimension that matches the current mode:

* **Pinning** a task whose code is unchanged costs nothing (no recompute — the hashes prove the
  cached output matches the code). Pinning *in the same edit as a logic change* recomputes, as
  it should — the hash comparison catches what the pin would otherwise have blessed.
* **Unpinning** just resumes: if the code is unchanged since the output was materialized, no
  recompute. If you edited while pinned and never bumped (the pin's accepted risk), the resume
  catches exactly that masked edit and recomputes once.
* Neither direction of the toggle ripples downstream: dependents key on whether an upstream
  actually *rematerialized* (its output identity), not on which mode tracked it.

Pinned and automatic tasks coexist freely in one pipeline, and a pinned task still reruns when
its *upstream* recomputes — the pin covers its own logic, not its inputs.

The token also buys you a diffable record: the cache decision ("this logic changed, downstream
must recompute") becomes an explicit part of the same edit, visible in code review and
``git log``. That's why AI-agent projects often prefer pins on key tasks even though automatic
mode needs nothing.

To turn automatic tracking off entirely — parameters-only identity plus explicit pins — set
``oryxflow.settings.code_version_auto = False``.

**Adoption / upgrade is invisible.** Existing caches are never invalidated by turning this on
(or upgrading into it): output that predates the tracking has no record yet, so it is treated as
current and the present code recorded as its baseline ("grandfathering") — the first
*subsequent* logic edit is what triggers a recompute. One caveat, only for output that has
**no record at all** (pre-upgrade artifacts, or ``code_version_auto = False`` projects adding
their first pin): if you edit the logic in the same change that first brings the task under
tracking, the stale output gets blessed. A best-effort mtime guard warns when it can detect
this (``output predates current code``) and holds off stamping; answer it with ``reset()`` to
recompute, or ``flow.accept_code()`` to confirm the outputs are current — the instance/flow
form stamps record-less outputs a fresh baseline, which is the cheap mass-blessing after an
upgrade (the guard fires spuriously right after ``git checkout``/``clone``, which resets source
mtimes). Once a task has a record, this trap is closed mechanically: the stored hashes expose
the edit no matter how you toggle the pin.

Where the hash cannot see — and how you notice
------------------------------------------------------------

Automatic tracking follows Python ``import`` statements under your project root. It cannot see:
data-file contents, external APIs, installed packages, dynamic imports or monkeypatching, and
tasks defined outside the project (a notebook kernel, a REPL) aren't hashable at all — those
degrade silently to parameters-only identity, never to a false rerun *or* a false "verified
unchanged".

The observable symptom is a **skip you didn't expect**: you changed something, ran, and the run
reports ``0 ran``. Make checking that a habit — after any change you expect to recompute, the
next run must show the affected tasks in ``result.ran`` (or ``oryxflow.events.runs()``) with a
matching reason (``code change (auto: pipeline/train.py)``). ``ran=0`` after a change means the
change is invisible to the hash: ``reset()`` the task that consumes it, or give that task an
explicit ``code_version`` if it happens repeatedly. ``ran=0`` on an *untouched* pipeline is the
healthy "cache is trusted" signal.

The staleness warning and its three exits
------------------------------------------------------------

For **pinned** tasks, editing code without bumping the token is the failure mode to catch, so the
same code hash runs as an *advisory* there: if a pinned task's code changed but its
``code_version`` didn't, the run warns — visibly, without ``enable_logging()``::

    StalenessWarning: task TrainModel: pipeline/train.py changed since cached run; code_version
    still 2 -- reusing cached output. Bump code_version to recompute, or
    oryxflow.accept_code(TrainModel) only if certain the output is equivalent -- when unsure,
    bump (best-effort check: can't see data files or dynamic calls).

An unacknowledged warning would otherwise repeat on every build (a ``WorkflowMulti`` run is one
build per flow over shared upstreams), so the printed/logged warning dedupes per process: the
same message for the same task shows once, and re-arms when the condition changes or after the
task reruns or is accepted. Every occurrence is still recorded in ``result.warnings`` and the
event stream (``oryxflow.events.warnings()``) regardless.

Every code change — whether it triggered an automatic recompute or a pin warning — has the same
three exits, not equal in risk:

* **Recompute** — for automatic tasks just run (it already happens); for pinned tasks bump
  ``code_version``. Safe by default.
* **Reset** (``flow.reset(...)``) — recompute regardless, no version bookkeeping. Also safe.
* **Accept** — the change is output-equivalent (rename, refactor, added log line):
  ``flow.accept_code()`` (or ``oryxflow.accept_code(task)``) re-stamps the current code as
  accepted without rerunning — for an automatic task this is how you *skip* the recompute it
  would otherwise do. It accepts the task **and its whole upstream tree**, so call it on the
  most-downstream task you judge equivalent (the flow's default task covers everything).
  **This is the one exit that can silently bless a stale output** — use it only when you're
  certain; when unsure, recompute (cheap insurance). ``accept_code`` prints a one-line summary
  of what it re-stamped (and says so when it accepted nothing), so an accept that didn't reach
  its target is visible, not silent. ``accept_code(TaskX)`` with a class re-stamps only that
  family's *stored* records, and bare ``accept_code()`` bulk-accepts warned records; prefer the
  instance/``flow`` form — it is also the only form that can stamp a baseline for outputs with
  **no record yet** (the ``output predates current code`` warning), and anything the other
  forms miss simply recomputes (the safe direction).

A pin's warning follows the *import* graph while reruns follow the *dependency* graph: a
downstream task that consumes a changed helper's output only via ``requires()`` (without
importing the file) won't warn — propagation through the folded fingerprints is what carries
staleness across a pure data dependency.

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
   * - logic (a task's ``run()`` or a helper it imports), output will differ
     - nothing — just run
     - the code fingerprint moved; the task and everything downstream recompute
   * - logic of a **pinned** task (one that declares ``code_version``)
     - bump ``code_version``
     - the pin is the authority; without a bump you get a warning, not a rerun
   * - logic of an **expensive** task (last run > ``code_version_auto_expensive_s``)
     - answer the warning: ``reset()`` / ``accept_code`` / pin
     - the guard holds it cached so a refactor can't silently burn a long run
   * - code, but output is provably identical (rename, extract, log line)
     - ``flow.accept_code()`` — only if certain; when unsure, recompute
     - re-stamps without recompute; the one non-recomputing exit
   * - nothing the hash can see, but I need a fresh compute (data file changed, suspect cache)
     - ``reset()``
     - invisible to the fingerprint; force it at the task that ingests the change
   * - something, but the run skipped it (``0 ran`` after an edit)
     - ``reset()`` — or pin that task with ``code_version``
     - the change is in a hash blind spot; treat the skip as a signal, not a convenience
   * - I want the outputs gone
     - ``reset()``
     - delete
   * - pin added or removed (code untouched)
     - nothing
     - the record carries both dimensions; a pure mode flip never recomputes or ripples
   * - first time bringing an **untracked** output under tracking right after editing it
     - ``reset()`` once
     - no record exists yet, so grandfathering would bless the stale output (once a
       record exists this trap is caught automatically)
   * - nothing — but pre-existing outputs warn ``output predates current code``
     - ``flow.accept_code()`` if the outputs are current, else ``reset()``
     - the mtime guard can't tell a fresh checkout from a stale cache; accepting stamps
       record-less outputs a baseline in one call

Keeping old versions side by side
------------------------------------------------------------

By default a bump overwrites in place. To keep previous versions at readable paths — the
compare-two-versions workflow — set ``keep_versions = True`` on the task (ideally with a string
version):

.. code-block:: python

    class TrainModel(oryxflow.tasks.TaskCachePandas):
        code_version = 'v2-log-features'
        keep_versions = True     # outputs live at data/TrainModel/v2-log-features/...

Bumping then writes to a new ``v.../`` directory and the old one stays on disk.
``keep_versions`` keys off the explicit token only — automatically-tracked tasks (no
``code_version``) always overwrite in place. Note that *turning on* ``keep_versions`` for an
existing task relocates its output path (it gains the version segment), so the task recomputes
once and the old non-versioned artifact is left behind — it's not a transparent toggle.

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
it ran (``output missing`` / ``code change (auto: pipeline/train.py)`` / ``code change (1 -> 2)``
/ ``upstream rerun``), so "why did this recompute?" and "was this produced by current code?" are
queries, not guesses. Anything a task
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
code edit (or a pin bump), the next run must show the intended tasks in ``result.ran`` with a
matching reason (``code change (auto: pipeline/train.py)`` / ``code change (1 -> 2)``) —
``ran=0`` after an intended change means it didn't reach the cache (a hash blind spot, or a pin
that wasn't bumped); ``ran=0`` on an untouched pipeline is the healthy "cache is trusted" signal.

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

For projects driven by AI coding agents, the recommended setup is the
:doc:`oryxflow Claude Code plugin <claude-plugin>` — it ships these rules (and more: project
scaffolding, task templates, conventions) as a skill that loads automatically, stays current
with the library, and needs no per-project copy. If you can't use the plugin (a different agent,
a locked-down environment), paste this snapshot of the rules into the project's ``CLAUDE.md``:

.. code-block:: markdown

    ## oryxflow cache & provenance rules

    1. Session start / after `/clear`: call `oryxflow.events.print_status()` — pending code
       warnings, last run per task family, recent failures — before assuming anything about
       cache state (`events.status()` returns the same as a dict for filtering; it prints
       nothing). No-Python fallback: `tail -30 .oryxflow/events.jsonl`.
    2. Editing task or helper logic needs NO cache action — code invalidation is automatic
       (AST-hash of the task's module + repo-local imports; comments/formatting never count)
       and propagates downstream. Do not hand-chain `reset()` calls for code changes.
       Expensive tasks (last run > `settings.code_version_auto_expensive_s`, default 600s)
       warn instead of silently recomputing — answer with reset / accept_code / pin.
       Exception: a task that declares `code_version` is PINNED — automatic tracking of its own
       logic is off, so bump its token **in the same edit**. Pins toggle freely: adding or
       removing one on unchanged code never recomputes (and never ripples downstream); an edit
       masked while pinned is caught the moment the pin comes off.
    3. **Verify the rerun happened.** After any code edit, the next run must show the edited
       band in `result.ran` (or `oryxflow.events.runs()`) with a matching reason —
       `code change (auto: <files>)` or `code change (1 -> 2)`. `ran=0` after an edit means the
       hash didn't see the change (data file, installed package, dynamic call, notebook-defined
       task): `reset()` the affected task, or pin it with `code_version` if it recurs. `ran=0`
       on an untouched pipeline is the healthy signal.
    4. Expensive recompute you judge output-equivalent (pure refactor): `flow.accept_code()` /
       `oryxflow.accept_code(anchor_task)` re-stamps the task and its whole upstream tree
       without rerunning — only if certain; when unsure, let it rerun. `preview()` first to see
       the pending band. It prints what it re-stamped — "nothing accepted" means it didn't
       reach the target (use the instance/flow form, not the class/bare form). An
       `output predates current code` warning (outputs with no record yet, e.g. after an
       upgrade) is answered the same way: `flow.accept_code()` if the outputs are current,
       `reset()` if not. Answer every staleness warning with one of its exits — bump, accept,
       or reset. Never leave one firing across runs.
    5. After a run, read the returned result: `result.reasons` says why each task ran;
       `result.warnings` lists unacknowledged code changes. Never hand-roll aggregation —
       `MultiRunResult` exposes `.ran`/`.complete`/`.failed`/`.reasons`/`.warnings` across
       flows, and the per-build verdict is logged durably as `run_finished` events.
    6. "The numbers changed and I don't know why": compare the last two runs —
       `oryxflow.events.runs(task_family='TaskX', last=2)` — and diff params, code_version,
       source_hashes.
    7. Log decision-relevant scalars inside `run()` via `self.logger.info(...)` — they're
       captured as `task_log` events and become next session's memory.
    8. Experiments you want side by side: string `code_version` plus `keep_versions = True`
       (explicit token only — auto-tracked tasks overwrite in place).
    9. Raw stream: current = `.oryxflow/events.jsonl`; offloaded months = `events-YYYYMM.jsonl`;
       all history = glob `events*.jsonl`. Prefer `events.runs()`/`status()` in Python.
    10. Data-file or external-API changes are invisible to the code hash — `reset()` the task
        that ingests them (a downstream reset re-loads the cached old input).

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
pull does **not** repeat. On the next ``run()`` all four models retrain and ``Tune`` recomputes on
top (recursive completeness), while ``LoadData`` is served from cache.

Contrast the three reset scopes you'll actually use:

* ``flow.reset(TrainModel(alpha=0.1))`` — one specific instance. Use when you're debugging a single
  case.
* ``flow.reset_upstream(Tune, only=TrainModel)`` — one *family*, everywhere it appears upstream.
  Use when something the code hash can't see changed for that family and you want every instance
  recomputed, cheap dependencies preserved.
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
