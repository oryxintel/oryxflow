# oryxflow.evals: release readiness - split outputs from scoring, side-by-side, ship it

> Plugin half: `oryxflow-claude-plugin/docs/plans/20261006-evals-project-layout.md` (the `evals/`
> project layout, probe tier, docs-first rule, second skill). This file is the library half and
> goes FIRST: nothing in the plugin can run until `oryxflow[evals]` is on PyPI.

## Context

`oryxflow.evals` is committed but unreleased (`[Unreleased]` in `CHANGELOG.md`; PyPI's newest is
26.7.21, which has no `evals` module). The docs section (`LLM evals` in `mkdocs.yml`, six pages)
is built but returns 404 on docs.oryxflow.dev; only the three blog posts - which show the older
hand-written `EvalRun` pattern - are live. So every `pip install "oryxflow[evals]"` in the docs and
the plugin currently installs a version without it.

Evidence behind the changes below: a downstream consumer project (an LLM writing product) with
five hand-built evals and ~25 throwaway `tmp/probe_*.py` scripts, read end to end. The probes are
what agents write when a durable eval looks too expensive. They re-implement, per script: prompt
surgery to build arms, path/credential bootstrapping, a hand-rolled pass/fail exit, printouts or
`probe_out_A.md` / `probe_out_B.md` for a human to read side by side, and a `rescore.py` to re-score
without re-calling the model. Several of those exist here already and went unused (`git_tree`
baselines, cached cells, the interval verdict); the rest is below.

Also surveyed: pydantic-evals 2.54.0 (2026-10-03; docs moved to `https://pydantic.dev/docs/ai/evals/`).
Since 1.70 it added agentic evaluators (`ToolCorrectness`, `TrajectoryMatch`, `ArgumentCorrectness`,
`MaxToolCalls`, `MaxModelRequests`), `GEval`, `CaseLifecycle`, online evaluation and ASCII-safe
report rendering. It still has no caching, arms, intervals, git baselines, guardrails, judge
validation, CSV cases or multi-turn support - so what this package adds is genuine and barely
overlaps. Rule for this work: **reuse pydantic-evals wherever it has the thing; build only what
nobody has.** Other libraries checked (inspect_ai, promptfoo, deepeval, ragas, langsmith,
braintrust/autoevals): none does paired clustered intervals, kappa / Rogan-Gladen, or result
caching; their human-review UIs are SaaS or Node.

## Decisions (signed off 2026-10-06)

1. **pydantic-evals >= 2.x is a hard floor.** The 1.70 line lacks the tool-call evaluators,
   `CaseLifecycle` and Windows-safe output.
2. **Split a cell into an outputs task and a scoring task (A7).** Today one `TaskEval` cell runs
   `dataset.evaluate(self.case, ...)` (`task.py` `_evaluate`), so the model calls and the
   evaluators share one cache entry and one `code_version()`. Change a scorer and you either re-bill
   every call or serve stale scores. Split it the standard oryxflow way - the expensive,
   un-replayable step apart from the cheap re-runnable one:

   ```
   EvalOutputs(TaskJson)        model calls; one JSON per cell:
                                [{case_name, rep, output, metrics, attributes,
                                  task_duration_s, error}, ...]
                                output = model_dump(mode='json') or the raw str
                                code_version = prompts / model / agent module
     EvalScores(TaskPqPandas)   requires EvalOutputs; runs the evaluators on the stored
                                outputs; saves today's per-case frame
                                scorer_version = the module defining the evaluators
   ```

   - JSON, not parquet columns or pickle: any output shape persists with no per-project schema,
     it is human-readable and diffable, and the side-by-side renderer reads it directly.
   - Rehydrate a typed output with `Output.model_validate(record)` when `case()` declares one, so
     `ctx.output.message` evaluators keep working; otherwise pass the dict / str. This cannot drift:
     the `Output` model lives in the agent module, which is in the outputs task's code_version.
   - Rebuild `EvaluatorContext` from the case list (inputs, expected, metadata) plus the JSON
     (output, metrics, attributes, durations). Check first whether pydantic-evals 2.x
     `run_evaluators()` (online module, re-scores stored contexts) does this for us.
   - Spans are not persisted, so span-based evaluators cannot re-score from cache. Guidance:
     `case()` records the tool calls it made into the output; span evaluators remain usable but
     score in the outputs stage only.
   - Failures persist with `error` set; `max_failure_rate` applies at the outputs stage (a run that
     mostly raised still saves nothing). The scoring stage gets the same treatment for judge
     errors, and its judge calls count in the projected bill.
   - The user still writes ONE `TaskEval` (`case()`, evaluators, `code_version()`, optional
     `scorer_version()`); the library builds the two tasks. `ev.sweep` gets the same split.
   - Do it before the first release: changing the cache layout after release breaks everyone's
     cached cells.
3. **Name the baseline arm; report against it; no hard gates (A5, revised).** Add `baseline=` to
   `TaskEval` / `ev.sweep`, defaulting to the arm named `baseline`. The verdict shows every arm's
   paired delta against it with the interval and the existing `inside noise` wording. No
   `noninferior=` gate and no pass/fail outcome: real results are mixed (one arm up on one label,
   down on another) and need a written judgement, which the plugin's `eval-run` steers the agent to
   write. The guardrail `budget` stays a MARK in the report, not a gate.
4. **Exit codes reflect the RUN, not the verdict.** Non-zero only for setup / credential errors and
   `EvalFailureRateError`; a completed run exits 0 whatever it found.
5. **Side-by-side output file (A6).** `EvalResult.side_by_side(path)`, rendered with Jinja from the
   cached outputs JSON. Default template shipped in the package; a project overrides it with its own
   template file. Layout: per case, inputs, then each arm's output under its own heading with its
   scores inline (markdown tables break on long multi-line text). Default selection: cases where the
   arms DISAGREE on the metric or guardrail, plus failures; `all=True` for everything. First repeat
   shown; other repeats expanded only where their outcome differs. `jinja2` joins the `[evals]`
   extra. Results are committed by default - small files that cost money to produce.
6. **Launch-directory guard.** `ev.cli` warns when the working directory is not the directory of the
   entry script. The plugin layout makes `evals/` the one launch directory, so a run from the repo
   root (or from a sub-package directory a credential loader needs) is caught instead of building a
   second cache or "succeeding" with no credentials.

## Work items

1. **Bug: `TaskEval` cache key omits the case set.** Only `sweep` digests the cases
   (`sweep.py` `_case_digest`); a class-form eval serves stale cells after `cases.csv` changes. Add
   the case digest to the outputs task's identity; test that editing a case recomputes.
2. **Outputs/scores split** (decision 2), including `ev.sweep`, the projected bill across both
   stages, and tests: scorer edit -> only scores recompute, zero new model calls; prompt edit ->
   both recompute; typed output round-trips.
3. **`_assign_reps`** - derive `rep` from pydantic-evals' `source_case_name` / `"[i/N]"` suffix
   instead of the claim that pydantic-evals carries no repeat index.
4. **`baseline=`** (decision 3) and verdict wording; `EvalResult.side_by_side()` (decision 5).
5. **Launch guard** (decision 6) and exit-code semantics (decision 4).
6. **Dependency floor**: `pydantic-evals>=2` in `setup.py` `extras_require['evals']`, plus `jinja2`.
   Run the full test suite against 2.54 before raising it; record any API changes.
7. **Docs fixes**: quickstart calls `oryxflow.set_dir('evals/data')` while saying no output dir is
   needed; quickstart shows the confirm prompt as `Run? [y/n/c]` (the code prints
   `Run N new calls for X? (y/n/c=check one call first)`); install lines get a minimum version.
   Add a "what pydantic-evals already gives you" section (LLMJudge / GEval, tool-call evaluators,
   ConfusionMatrixEvaluator / PrecisionRecallEvaluator via `report_evaluators`, `CaseLifecycle` for
   per-case stubs, `generate_dataset`) and the patterns that need no new API: model as an arm
   parameter folded into `code_version()`; multi-turn replay as one `case()` running the episode
   and returning per-turn columns; a "zero false negatives" check as a confusion-matrix column.
8. **Release**: GitHub Release -> CI publishes to PyPI; deploy the docs so the `LLM evals` section
   and `llms.txt` entries are live. Then update the three existing blog posts
   (`docs/todo/20261006-evals-blog-post-examples.md`).

## Status (2026-10-06)

Done: items 1-7 (cache-key bug, outputs/scores split, `_assign_reps`, `baseline=`,
`side_by_side()`, launch guard, dependency floor, docs). Test suite green on pydantic-evals 2.18
(dev env, pandas 2) and 2.54 (fresh venv, pandas 3 -- which surfaced and fixed a preview-column
bug). Open: item 8, the release.

Deviations from the decisions above, decided during implementation:

- **No failure-rate gate on the scoring stage.** Decision 2 said judge errors would get the
  `max_failure_rate` treatment. Since re-scoring is now free, caching a partly failed score is
  cheap to undo, so the scoring stage keeps the existing behavior (evaluator errors counted and
  flagged above every rate, row saved) and `--rescore` re-runs it. The outputs stage keeps the
  gate: a run that mostly raised still saves nothing.
- **Exit codes needed no change.** The CLI already exits 0 for a completed run and non-zero only
  for a preflight failure or an `EvalFailureRateError`.
- **`scorer_version()` and a user `code_version()` coexist via `__init_subclass__`**: a subclass's
  `code_version` is moved to the outputs stage (it names what the arm reads), so neither the
  template nor the docs change meaning.

## Deferred (still open in `20260830-evals-gaps-from-the-first-real-eval.md`)

`ev.pooled` / `ev.explode`, `TaskEval.gates`, `EvalResult.also`, evaluator preflight, arm-aware
`watch=`, the check_dataset / check_report scaffold files. Revisit after the release; nothing above
depends on them. `TaskEval.gates` in particular should be re-read against decision 3 (no hard
gates) before anyone builds it.

## Verification

- Test suite green on pydantic-evals 2.54.
- A sweep run twice: second run makes zero calls. Edit a scorer: zero model calls, scores change.
  Edit a case: that case recomputes.
- `side_by_side()` on a two-arm run renders only disagreeing cases by default.
- Fresh venv: `pip install "oryxflow[evals]>=<release>"` from PyPI, quickstart runs as written.
- docs.oryxflow.dev/docs/llm-evals/index.md returns 200.
