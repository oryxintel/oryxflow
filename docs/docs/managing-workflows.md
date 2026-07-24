# Managing Complex Workflows

As a project grows, the expensive part is rarely the final result — it's the *granular* work underneath it. Fetching data from a slow or paid API. Training a model once per hyperparameter setting. Re-running that work every time you tweak something downstream is what makes iteration painful.

oryxflow's answer is to make each granular unit its own **task**, cached independently. The engine tracks completeness per task, so when you change one thing it re-runs exactly what changed — and leaves the expensive, unchanged work alone. This page walks through the pattern data scientists reach for constantly:

1.  **Iterate granular** — one task per item (per hyperparameter, per state, per input file), each cached on its own.
2.  **Aggregate** — combine those outputs one level up into a single result.
3.  **Selectively reset** — when something the engine *can't* see changes (a data file, an external API), invalidate just that family of tasks and let everything downstream recompute. The expensive leaves stay cached. (Parameter and code changes need no reset — they rerun on their own; see below.)

## Worked example: hyperparameter tuning

The classic case: load a dataset once (expensive), train a model once per hyperparameter value (the granular loop), then aggregate the scores to pick a winner.

```python
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
```

Run it and read off the best hyperparameter:

```python
flow = oryxflow.Workflow(Tune)
flow.run()
results = flow.outputLoad()               # every alpha's score, one frame
best_alpha = results.iloc[0]['alpha']
```

The `requires()` dict is what fans the DAG out into one `TrainModel` per `alpha`, and `self.inputLoadConcat()` stacks their outputs, tagging each row with that task's parameters so your `alpha` column survives the concat. (The mechanics of dict-`requires()` and `inputLoadConcat` are covered as reference in [Advanced: Dynamic Tasks](advtasksdyn.md).)

## Extend the grid — nothing wasted

Decide you want to try more values? Add them to `ALPHAS` and run again:

```python
ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0]    # added 100.0
flow.run()
```

Only `TrainModel(alpha=100.0)` runs — the four you already trained are complete and are skipped, and the expensive `LoadData` is not touched at all. `Tune` recomputes because it now has a new input. You didn't reset anything; adding a parameter value is enough.

## What reruns, and when you do nothing

A task is "complete" when its output exists **for its current parameters and its current code**. Two of the three things that can make a result stale are handled for you, no action required:

- **You changed a parameter.** The task id is derived from the parameters, so a new value is a new id — no output yet, the task runs, and the old output for the old parameters is left untouched beside it. Add a value to the grid (a new `alpha`) and *only* that new task runs.
- **You changed the code.** oryxflow fingerprints each task's logic (next section). Edit a task's `run()` — or a helper it calls — and the task plus everything downstream recomputes on the next run.

The third — a change the engine *can't* see, like a new data file or an API response — is the only one you drive by hand, with `reset()` (see [Selectively reset when code versioning doesn't apply](managing-workflows.md#selectively-reset-when-code-versioning-doesnt-apply) below). Because completeness is checked recursively down the graph, an aggregator recomputes automatically whenever any of its inputs changes or a new one appears, with no bookkeeping on your part.

## Automatic code invalidation

Suppose you improve `TrainModel` — add a second scoring metric to its `run()`. The parameters didn't change, so a parameters-only cache would happily serve the stale output. oryxflow instead **fingerprints the code**: on the next run every `TrainModel` recomputes, and so does everything downstream of it (`Tune`), overwriting in place at the same paths. `LoadData` is untouched. You did nothing — no reset, no version to bump.

The fingerprint covers each task's own class **and every project-local symbol it references**, followed transitively across your modules — so editing a *helper function* the task calls is caught too, where the change actually lives, not just in `run()`. Two properties make this safe to leave on:

- **Cosmetic edits never recompute.** oryxflow compares what your code *does*, not how it's written, so comments, docstrings, and formatting changes never trigger a rerun.
- **Granularity is per symbol, not per file.** One monolithic `tasks.py` is fine: editing one task's `run()` reruns that task alone, and editing a shared helper recomputes exactly the tasks that reference it (directly or through other helpers). The rerun reason even names the changed symbol — `code change (auto: tasks.py::TrainModel)`.

Referencing another *task* in `requires()` is dependency wiring, not a code reference — it never pulls that task's body into your fingerprint; the dependency cascade carries that staleness instead. What symbol analysis can't split apart (module-level side effects, star imports, dynamically created classes) falls back to whole-file granularity — extra reruns at worst, never a missed one.

**Expensive tasks warn instead of silently recomputing.** A rerun overwrites the old output, so burning a long computation should be a decision, not a side effect of a refactor. A task whose last run took longer than `settings.code_version_auto_expensive_s` (default 600 seconds) is held complete when its code changes, and the run **warns** instead — answer it with one of the [three exits](managing-workflows.md#the-three-exits-for-any-code-change) (reset, `accept_code`, or pin it with `code_version`). Set the threshold to `None` to make every code change recompute.

To turn automatic tracking off entirely — parameters-only identity plus explicit pins — set `oryxflow.settings.code_version_auto = False`.

## When you changed something but nothing reran

The fingerprint follows Python `import` statements under your project root. It **cannot** see: data-file contents, external APIs, installed packages, dynamic imports or monkeypatching, and tasks defined in a notebook or REPL (which aren't hashable at all). A change in any of those is invisible to the hash, so the task keeps its cache.

The symptom is a **skip you didn't expect**: you changed something, ran, and the summary says `0 ran`. Make this one check a habit — after any change you expect to recompute, confirm the affected tasks appear in `result.ran` (or `oryxflow.events.runs()`) with a matching reason such as `code change (auto: pipeline/train.py::TrainModel)`:

- `ran=0` **after a change** means the change is in a blind spot. `reset()` the task that ingests it, or give that task an explicit `code_version` if it happens repeatedly.
- `ran=0` **on an untouched pipeline** is the healthy "cache is trusted" signal.

A blind spot never produces a *false* rerun or a false "verified unchanged" — it just degrades to parameters-only identity, the same trust level every cache file has always had.

## Pinning a task: the explicit `code_version`

Sometimes automatic is the wrong sensitivity — a training task so expensive that a refactor-triggered recompute must be a deliberate decision, or logic the hash can't see (behavior driven by a config file, dynamic dispatch). Declare an explicit `code_version` and that task's own logic is **pinned**: it recomputes only when you bump the token, and a code edit *without* a bump produces a staleness warning instead of a rerun.

```python
@oryxflow.requires(LoadData)
class TrainModel(oryxflow.tasks.TaskCachePandas):
    code_version = 2          # pinned: bump deliberately to recompute
    alpha = oryxflow.FloatParameter()
    def run(self):
        ...
```

The token can be an int or a string (`'v2-log-features'`). Pins are **free to toggle**: the `code_version` line is itself stripped by AST normalization (like a comment), so adding, removing, or bumping it is never itself a source change. Under the hood oryxflow stores both the token and the code hash at each run and compares whichever matches the current mode — which is what gives the toggle these useful properties:

- Pinning a task whose code is unchanged costs nothing — no recompute.
- Pinning *in the same edit as a logic change* still recomputes: the hash catches what the pin would otherwise have blessed.
- Unpinning just resumes automatic tracking; if you edited while pinned and never bumped, that one masked edit is caught and recomputes once.
- Toggling never ripples downstream — dependents key on whether an upstream actually *rematerialized*, not on which mode tracked it.

Pinned and automatic tasks mix freely in one pipeline, and a pinned task still reruns when its *upstream* recomputes — the pin covers its own logic, not its inputs. Why pin at all, if automatic needs nothing? The token makes the cache decision — "this logic changed, downstream must recompute" — an explicit, diffable part of the same edit, visible in code review and `git log`. That's why AI-agent projects often prefer pins on their key tasks.

!!! note

    **One first-time trap.** Bringing an output under tracking for the *first* time — a pre-upgrade artifact, or the first pin in a `code_version_auto = False` project — in the *same edit* that changes its logic can bless the stale output, because there is no prior record to compare against. oryxflow warns when it can detect this (`output predates current code`); answer it with `reset()` to recompute, or `flow.accept_code()` if the outputs really are current. Once a task has one record on disk, the trap is closed for good — the stored hashes expose any edit no matter how you toggle the pin.

## The three exits for any code change

Every code change — whether it auto-recomputed or a pin warned — has the same three exits, and they are **not** equal in risk:

- **Recompute** — for automatic tasks, just run (it already happens); for pinned tasks, bump `code_version`. Safe by default.
- **Reset** — `flow.reset(...)` recomputes regardless, with no version bookkeeping. Also safe.
- **Accept** — the change is output-equivalent (a rename, an extracted function, an added log line): `flow.accept_code()` (or `oryxflow.accept_code(task)`) re-stamps the current code as accepted *without* rerunning. For an automatic task this is how you *skip* a recompute it would otherwise do.

**Accept is the one exit that can silently bless a stale output** — use it only when you're certain; when unsure, recompute (cheap insurance; a wrongly-blessed cache is not). It stamps the task **and its whole upstream tree**, so call it on the most-downstream task you judge equivalent. It prints a one-line summary of what it re-stamped; "nothing accepted" means it didn't reach the target. A bare `flow.accept_code()` covers your whole pipeline — every task it can compute, whichever final it hangs from — so one call blesses a multi-final pipeline, and it works from a fresh script (a one-shot bless after an upgrade needs no prior run). You can also name the tasks yourself, one or several: `flow.accept_code([FinalA, FinalB])`. Prefer the instance/`flow` form over the bare/class form: it is the only one that can stamp a baseline for record-less outputs, and anything the other forms miss simply recomputes (the safe direction). On a `WorkflowMulti` use `flow.accept_code()` (all flows, or one with `flow=...`) — the module-level form doesn't know your flows' parameters. Tasks a flow reaches only *dynamically* (yielded inside a `run()`) aren't in the static tree; accept those with an explicit instance if they warn.

!!! note

    The staleness warning for a pinned task prints without `enable_logging()`:

        StalenessWarning: task TrainModel: pipeline/train.py::TrainModel changed since cached run;
        code_version still 2 -- reusing cached output. Bump code_version to recompute, or
        oryxflow.accept_code(TrainModel) only if certain the output is equivalent.

    It dedupes per process — one line per distinct message, however many flows or parameter values hit it — and re-arms when the condition changes or the tasks rerun. `result.warnings` lists each distinct warning once, so its length answers "how many pending warnings do I have?" no matter how many flows or parameter values are involved. `oryxflow.events.warnings()` still records every occurrence, so even if your environment suppresses the print the record is never lost, and a strict `-W error` setup won't turn the advisory into a failed build.

## Which verb, when

| I changed… | Do this | Why |
|----|----|----|
| a value/knob that is a `Parameter` | nothing | new identity auto-reruns; old output kept side by side |
| logic (a task's `run()` or a helper it imports), output will differ | nothing — just run | the code fingerprint moved; the task and everything downstream recompute |
| logic of a **pinned** task (one that declares `code_version`) | bump `code_version` | the pin is the authority; without a bump you get a warning, not a rerun |
| logic of an **expensive** task (last run \> `code_version_auto_expensive_s`) | answer the warning: `reset()` / `accept_code` / pin | the guard holds it cached so a refactor can't silently burn a long run |
| code, but output is provably identical (rename, extract, log line) | `flow.accept_code()` — only if certain; when unsure, recompute | re-stamps without recompute; the one non-recomputing exit |
| nothing oryxflow can detect, but I need a fresh compute (data file changed, suspect cache) | `reset()` | the change is outside the code oryxflow tracks; force it at the task that ingests the change |
| something, but the run skipped it (`0 ran` after an edit) | `reset()` — or pin that task with `code_version` | the change is somewhere oryxflow can't detect; treat the skip as a signal, not a convenience |
| I want the outputs gone | `reset()` | delete |
| pin added or removed (code untouched) | nothing | the record carries both dimensions; a pure mode flip never recomputes or ripples |
| first time bringing an **untracked** output under tracking right after editing it | `reset()` once | no record exists yet, so grandfathering would bless the stale output (once a record exists this trap is caught automatically) |
| nothing — but pre-existing outputs warn `output predates current code` | `flow.accept_code()` if the outputs are current, else `reset()` | the guard can't tell a fresh checkout from a stale cache; accepting stamps record-less outputs a baseline in one call |

## Keeping old versions side by side

By default a bump overwrites in place. To keep previous versions at readable paths — the compare-two-versions workflow — set `keep_versions = True` on the task (ideally with a string version):

```python
class TrainModel(oryxflow.tasks.TaskCachePandas):
    code_version = 'v2-log-features'
    keep_versions = True     # outputs live at data/TrainModel/v2-log-features/...
```

Bumping then writes to a new `v.../` directory and the old one stays on disk. `keep_versions` keys off the explicit token only — automatically-tracked tasks (no `code_version`) always overwrite in place. Note that *turning on* `keep_versions` for an existing task relocates its output path (it gains the version segment), so the task recomputes once and the old non-versioned artifact is left behind — it's not a transparent toggle.

## Selectively reset when code versioning doesn't apply

`reset` remains the right tool when there's no code token to move: a data file changed, you suspect a corrupt cache, or you simply want outputs deleted. The point is to reset **only** the family that changed, so the shared `LoadData` stays cached:

```python
flow = oryxflow.Workflow(Tune)
flow.reset_upstream(Tune, only=TrainModel)   # every TrainModel in the DAG, nothing else
flow.run()
```

`reset_upstream(Tune, only=TrainModel)` walks the whole graph upstream of `Tune` and invalidates just the `TrainModel` instances — every `alpha` at once, found via the DAG so you never hand-list them. `LoadData` is a *different* family and is left complete, so the slow data pull does **not** repeat. On the next `run()` all four models retrain and `Tune` recomputes on top (recursive completeness), while `LoadData` is served from cache.

Contrast the three reset scopes you'll actually use:

- `flow.reset(TrainModel(alpha=0.1))` — one specific instance. Use when you're debugging a single case.
- `flow.reset_upstream(Tune, only=TrainModel)` — one *family*, everywhere it appears upstream. Use when something the code hash can't see changed for that family and you want every instance recomputed, cheap dependencies preserved.
- `flow.reset_upstream(Tune)` — the whole upstream, including `LoadData`. Use only when the raw inputs themselves are stale.

This is the core discipline: **reset at the level you changed, not above it.** It's what keeps a tweak to a fast step from triggering hours of expensive re-fetching or re-computation.

## The event stream: what ran, when, and why

Every run appends structured events to a plain-text JSONL stream — always on, written asynchronously (microseconds), so the record exists even when your script discards the run result. The file layout is a contract you can script against:

- current month: `.oryxflow/events.jsonl` (a stable head — `tail -30` always shows the latest)
- offloaded months: `.oryxflow/events-YYYYMM.jsonl` (immutable once offloaded)
- all history: glob `.oryxflow/events*.jsonl`

Events include `run_started` / `task_ran` / `task_failed` / `run_finished` / `code_warning` / `code_accepted` / `task_log`. Each `task_ran` carries the full recipe — params, `code_version`, code fingerprint, source hashes, git SHA, duration — and the **reason** it ran (`output missing` / `code change (auto: pipeline/train.py)` / `code change (1 -> 2)` / `upstream rerun`), so "why did this recompute?" and "was this produced by current code?" are queries, not guesses. Anything a task logs via `self.logger` is captured as a `task_log` event — log your decision-relevant scalars (`self.logger.info("corr_avg={}", corr)`) and they become next session's memory.

```python
oryxflow.events.print_status()   # session-start orientation, printed: pending code
                                 # warnings, last run per family, recent failures
oryxflow.events.status()         # the same as data (a dict) -- it returns, doesn't print
oryxflow.events.runs(task_family='TrainModel', last=2)   # diff params/code_version/hashes
oryxflow.events.runs(flow='Retail')                      # per-flow when using WorkflowMulti
```

From the shell, no Python needed:

    tail -30 .oryxflow/events.jsonl
    grep TrainModel .oryxflow/events*.jsonl
    jq 'select(.type=="task_ran") | {task_id, reason, duration_s}' .oryxflow/events.jsonl

`run()` returns the same story in memory: `result.reasons` maps each task that ran to why, and `result.warnings` lists unacknowledged code changes.

Add `.oryxflow/` to your `.gitignore` — run records are high-frequency exhaust, not source. Disable entirely with `oryxflow.settings.events = False`.

## Where the freshness records live

The code fingerprints live in one small JSON file per data directory (`<dirpath>/.oryxflow-code-status.json`). It describes those exact artifacts, so **move or restore a data directory whole** — file and artifacts together, always via the same channel. An artifact without a record is simply treated as current (the same trust level every file has today); partial restores are on you. Correctness depends only on this file, never on the event log.

## What caching does *not* protect against

Everything above solves one family of problems — the *mechanical* one: staleness, provenance, and memory. That family genuinely yields to tooling, and a cached result you've kept fresh is worth trusting *as a computation*. But be clear about the boundary: oryxflow guarantees a result was produced by the code and inputs it records — **not** that the result is correct. A perfectly versioned, fully reproducible pipeline can still hand you a wrong number, and nothing here will flag it:

- a join that should be many-to-one runs many-to-many and inflates every downstream aggregate;
- a percentage computed against the wrong denominator, or a `groupby` that silently drops null-keyed rows;
- a dtype coercion that eats a ZIP code's leading zero, or a timezone shift that moves rows a day;
- a backtest that peeks at the future, or a correlation read off overlapping windows as if the points were independent;
- a number quoted from memory or eyeballed off a chart instead of from the saved artifact.

None of these raise; each completes cleanly and prints something plausible. Versioning the code just makes the wrong join *reproducible*. These are caught by **habit, not machinery** — validate the merge and assert the row relationship, look at the frame's shape and null counts before stating a finding, quote every number from an artifact you can re-open. (Note this is a different thing from the hash blind spots above: a changed data file is invisible but has a verb — `reset` the loader; a wrong join has no verb, only vigilance.) That is why the [Claude Code plugin](claude-plugin/index.md) ships those conventions alongside the library, delivered at the point you're writing the task rather than in a review afterward. And the judgment calls — is this method right for the question, is this effect within noise, does the data actually behave the way I assumed — are yours; no cache decides them.

## CLAUDE.md snippet for AI-agent projects

For projects driven by AI coding agents, the recommended setup is the [oryxflow Claude Code plugin](claude-plugin/index.md) — it ships these rules (and more: project scaffolding, task templates, conventions) as a skill that loads automatically, stays current with the library, and needs no per-project copy. If you can't use the plugin (a different agent, a locked-down environment), paste this snapshot of the rules into the project's `CLAUDE.md`:

```markdown
## oryxflow cache & provenance rules

1. Session start / after `/clear`: call `oryxflow.events.print_status()` — pending code
   warnings, last run per task family, recent failures — before assuming anything about
   cache state (`events.status()` returns the same as a dict for filtering; it prints
   nothing). No-Python fallback: `tail -30 .oryxflow/events.jsonl`.
2. Editing task or helper logic needs NO cache action — code invalidation is automatic
   (AST-hash of the task's own class + the repo-local symbols it references, transitively;
   comments/formatting never count, and editing an unrelated task in the same file never
   reruns its siblings — one monolithic tasks.py is fine) and propagates downstream. Do not
   hand-chain `reset()` calls for code changes.
   Expensive tasks (last run > `settings.code_version_auto_expensive_s`, default 600s)
   warn instead of silently recomputing — answer with reset / accept_code / pin.
   Exception: a task that declares `code_version` is PINNED — automatic tracking of its own
   logic is off, so bump its token **in the same edit**. Pins toggle freely: adding or
   removing one on unchanged code never recomputes (and never ripples downstream); an edit
   masked while pinned is caught the moment the pin comes off.
3. **Verify the rerun happened.** After any code edit, the next run must show the edited
   band in `result.ran` (or `oryxflow.events.runs()`) with a matching reason —
   `code change (auto: <file>::<symbol>)` or `code change (1 -> 2)`. `ran=0` after an edit means the
   hash didn't see the change (data file, installed package, dynamic call, notebook-defined
   task): `reset()` the affected task, or pin it with `code_version` if it recurs. `ran=0`
   on an untouched pipeline is the healthy signal.
4. Expensive recompute you judge output-equivalent (pure refactor): `flow.accept_code()` /
   `oryxflow.accept_code(anchor_task)` re-stamps the task and its whole upstream tree
   without rerunning — only if certain; when unsure, let it rerun. `preview()` first to see
   the pending band. A bare `flow.accept_code()` covers the whole pipeline — every task
   the flow can compute, multi-final pipelines included, from a fresh process; a list
   also works (`flow.accept_code([FinalA, FinalB])`). It prints what it re-stamped — "nothing
   accepted" means it didn't reach the target (use the instance/flow form, not the
   class/bare form). An `output predates current code` warning (outputs with no record
   yet, e.g. after an upgrade) is answered the same way: `flow.accept_code()` if the
   outputs are current, `reset()` if not. On WorkflowMulti use `flow.accept_code()`
   (all flows) — the module-level bulk form doesn't know the flows' parameters. Answer
   every staleness warning with one of its exits — bump, accept, or reset. Never leave one
   firing across runs.
5. After a run, read the returned result: `result.reasons` says why each task ran;
   `result.warnings` lists unacknowledged code changes (each distinct warning once, so its
   length is the pending count). Never hand-roll aggregation —
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
```

## Scaling up: hierarchies and independent experiments

The same three moves scale in two directions.

**Deeper hierarchies.** Aggregators compose: a per-state task feeds a per-country task feeds a per-sector task, each level a `requires()` fan-out combined with `inputLoadConcat()`. The whole tree is still one DAG, so preview, the run summary, and selective reset all reach every level.

**Independent experiments.** When you want to manage several runs separately — each with its own output and its own reset scope — drive the top with [WorkflowMulti](workflow.md) over a params grid instead of one more aggregator, then combine across flows with `outputLoadConcat`:

```python
flow = oryxflow.WorkflowMulti(Sector, params={'sector': ['Retail', 'Office']})
flow.run()
dfall = flow.outputLoadConcat(Sector)                # all sectors, one tagged frame
flow.reset_upstream(Sector, only=CountryFeatures)    # reset one family across every flow
```

A complete, runnable version of this multi-level dev loop — iterate on one `(sector, country)` first, then roll the change out to every flow *without re-fetching the expensive per-state source* — is in `docs/example-flow-multi.py`. The full reference for dict-`requires()`, `inputLoadConcat`, `outputLoadConcat`, and the `only=` reset filter is [Advanced: Dynamic Tasks](advtasksdyn.md).

!!! tip

    This is exactly what the [Claude Code plugin](claude-plugin/index.md) is built to manage. Describe the hierarchy in plain language and it writes the fan-out `requires()` and the `inputLoadConcat()` aggregators; when you iterate, it scopes the reset for you — resetting just the family you changed (`reset_upstream(..., only=...)`) so the expensive leaf tasks are preserved.
