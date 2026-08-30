# `oryxflow.evals`: a cached LLM-eval harness over pydantic-evals

## Context

Three blog posts already argue oryxflow's position on LLM evals — an eval matrix is a Cartesian
product over parameters, so cache one output per cell and pay only for the cell you changed
(`docs/blog/posts/llm-evals-are-a-parameter-sweep.md`, `llm-eval-results-retention.md`,
`cheap-durable-llm-evals.md`). The argument holds. Nothing in the library supports it.

Today a user follows those posts by hand-writing the same ~150 lines every time: a `sys.path`
preamble, `set_dir`, a `TaskPqPandas` subclass with `persists = ['cases']`, an
`asyncio.run(dataset.evaluate(...))`, a loop flattening `report.cases` into rows, an `argparse`
CLI mapping `--models` onto a `model_id` parameter, a `runIterConcat` call, and a bespoke
`_print_summary`. Evidence from real harnesses built this way:

- **The prompt is invisible to the cache.** Every such harness disables `code_version_auto` and
  bumps a hand-written `code_version`, with a README warning to pass `--reset` after editing a
  template. Forget it and stale numbers read as fresh ones. (Fixed by the companion plan,
  `20260822-engine-computed-code-version.md`, which this plan depends on.)
- **Credential failure reads as success.** Run from the wrong working directory, every case
  raises, the sweep completes, and an empty result is cached as if it were a measurement.
- **No noise floor.** One harness recorded the *same* prompt measuring 69% and then 64% an hour
  apart, and separately noted that at n=3 a single flip is a 33-point swing. Both facts live in
  prose in a README; neither reaches the output a reader acts on.
- **Guardrails are manual.** A change that raises "did the assistant act" by acting on everything
  is a regression, not a fix, and only a control set catches it. Nothing enforces that a declared
  winner did not break one.
- **The CLI drifts from the task.** `--models` mapping to `model_id` is hand-maintained, so
  adding a parameter silently fails to add a flag.

**And the harness loses to no harness at all.** Asked for a *quick* eval, a competent coding agent
inspected an existing oryxflow-based eval in the same repository and chose to bypass it — *"no
oryxflow caching — quick run, plain asyncio."* It optimized time-to-first-run, which the class-plus-
scaffold shape loses on, and thereby signed up to re-bill every case on every iteration of exactly
the workload that re-runs most. Any design whose cheapest entry point is heavier than
`asyncio.gather` plus a dict will keep losing that comparison, and losing it at the moment caching
is worth the most.

A second harness in the same repository dropped the caching layer too, and this one wrote down its
reasoning: *"a cache here would mostly be a way to read stale numbers after a prompt edit — the
exact trap that eval documents needing `--reset` for."* That judgement is **correct as stated**. An
un-invalidatable cache is worse than no cache, because no cache is merely expensive while a stale
one is wrong. It is also the whole argument for ordering the companion plan first: fix invalidation
and the objection disappears, ship caching without fixing it and the objection is right.

The outcome this plan targets: an engineer who has just changed a prompt gets a defensible answer
to *"is it better?"* in six lines and one command, pays only for cells that changed, and cannot be
handed a winner that broke a guardrail or that sits inside the noise.

### Design decisions

**1. pydantic-evals is the model, not an option.** `Case`, `Dataset`, `Evaluator`,
`report_evaluators`, `EvaluationReport` are the vocabulary; `oryxflow[evals]` depends on it
unconditionally. We do not rebuild scoring. Specifically we delegate, and must not reimplement:
the concurrent runner (`max_concurrency`), retries (`retry_task=RetryConfig(...)`), repeats
(`repeat=`) and their aggregation (`case_groups()` / `ReportCaseAggregate`, grouped by
`source_case_name`), the success/failure split (`report.cases` vs `report.failures`, the latter
carrying `error_message` / `error_stacktrace`), report-level analyses (`ConfusionMatrix`,
`PrecisionRecallCurve`, `ScalarResult`), `LLMJudge`, and OpenTelemetry/Logfire tracing.

**2. Extension is deferred but designed for, and the seam is the DataFrame.** Everything
downstream of the run — `Metric`, slices, the bootstrap, the verdict, `report()` — reads only a
per-case DataFrame and never touches a pydantic-evals object. Two overridable methods on
`TaskEval` are the exit: `_evaluate()` (returns a report; swap the runner) and `_to_frame(report)`
(returns the rows; swap the report shape). Keep them as separate methods with docstrings saying
so even though only one implementation ships. The property this buys is testable today: every
metric/verdict/report test is written against a hand-built DataFrame with no pydantic-evals object
involved.

**3. The function-first form is the headline; the class is the graduation.** `ev.sweep()` accepts
a plain async function and constructs the task internally. This exists because the class-first
shape demonstrably loses to hand-rolled asyncio for a first eval. The class form remains for tasks
needing `persists`, upstream dependencies, or a custom `code_version`.

**4. Cases live in files, not Python.** Hardcoded `Case(...)` constructors do not survive contact
with scale: real harnesses start with ~20 cases in Python and abandon it for a CSV loader by ~500.
The reasons are structural, not stylistic — a domain expert who has the real examples cannot edit
Python, a case set buried in constructors is not reviewable as a diff, and appending synthetic
cases becomes code generation. CSV leads (it is how real user instructions arrive and it opens in
a spreadsheet); YAML and JSONL are supported by the same loader.

**5. Rejected: a cross-product DSL in the case file.** Expressing 22 cases as `axes:` plus
expansion rules invents a config language and hides the case list behind rules the reader must
mentally execute. An explicit N-row file is auditable in review. Expansion is a *generation* step
that belongs to tooling (the plugin's `eval-cases` command), and the file stays the record of what
actually ran.

**6. Confidence intervals, via scipy, clustered by case.** `scipy.stats.bootstrap` does the work;
we resample **case names** rather than rows, because repeats of one case are correlated and
treating 132 calls over 22 cases as 132 independent samples overstates confidence by roughly the
square root of the repeat count. This is not an invention: Inspect AI (UK AISI) ships `stderr()`
with a `cluster` parameter for exactly this, and the literature is explicit that a normal
approximation is wrong at these sample sizes (arXiv 2503.01747). pydantic-evals has no inference —
its `diff_atol` / `diff_rtol` are rendering tolerances that decide what is coloured red.

**7. Rejected: continuous-integration framing.** No `--assert-no-regression`, no exit-code gating,
no "your evals are now tests". This is an on-demand tool an engineer reaches for when they change
a prompt. A user who wants a build gate can assert on `r.df` themselves.

**8. No eval spends money without showing the bill.** Every entry point prints the projected call
count, the cached/new split, and an estimated cost before running, and the CLI confirms.

**9. Failures abort rather than cache.** Above a threshold (default 20% of cases in
`report.failures`), `run()` raises instead of saving. Below it, failures are excluded from every
rate and reported separately. A parquet of exceptions must never become a cache entry, because the
next reader cannot tell it from a measurement.

**10. Real and synthetic never blend.** `synthetic` is a reserved metadata column. The headline
metric is computed on real cases only; synthetic is reported beside it. A synthetic set written by
the same mind that wrote the prompt flatters it. `holdout` is likewise reserved: any case whose
text is embedded in a prompt is excluded from **every** arm, with the count and reason stated, or
the comparison is scoring against its own answer key.

**11. Typer for the CLI, with flags derived from the task's Parameters.** Hand-maintained argparse
drifts from the task the moment a parameter is added. Typer is Click underneath, type-hint driven,
and Rich arrives transitively via pydantic-evals, so output styling costs no new dependency.

**12. Truncation is a rendering concern and must never touch scoring.** An observed harness capped
its output model's `message` field at 2000 characters — a reflex, so a CSV row would not be a wall
of text. Its metric compared *the last line of that field against the first item of a generated
list* — so the field was an input to scoring, not merely a display column. Long replies
were sliced mid-sentence, the harness took the last line of its own truncation, and the judge
correctly reported a failure that the model had not committed. **The object an evaluator sees must
be the object production produced.** Our architecture is already safer, because scoring happens
inside pydantic-evals on `case.output` while `_to_frame()` runs after it — but only if the user's
own output model does not truncate. So `_to_frame()` emits capped columns under a `_preview` suffix
beside the full value, never in place of it, and the docs state the rule in one line.

**13. A metric identically dead across every arm is flagged automatically.** The truncation bug was
caught because pairing scored 0% in *both* arms — *"a metric that's identically dead in the before
and after arm is usually measuring the harness."* That heuristic is cheap and mechanical, so the
verdict runs it: any metric at exactly 0% or exactly 100% in **every** arm gets a line saying this
usually indicates the harness rather than the model, and to check the metric's inputs before
reading anything else. It costs nothing and it catches the class of bug that silently invalidates
a whole sweep.

**14. Rates report their denominator, and denominator drift is flagged.** A rate computed over a
filtered subset is gameable by shrinking the subset: in the observed run the old prompt scored well
on output *quality* partly by producing no output at all, with 33% of turns silent. Quality moved from
31% to 100% while yield moved 1.62 to 2.42 per turn — and it is the second number that makes the
first mean anything. So `verdict()` always prints `n` per arm per metric, and flags when a metric's
`n` differs materially between arms, because a rate comparison across different denominators is not
a comparison. The docs state the reading order plainly: **a coverage metric first, and never a
quality rate alone.**

**15. A baseline arm is materialized from a git ref, not reconstructed by string replacement.**
This supersedes an earlier draft of this plan that made anchored `(old, new)` diffs the baseline
mechanism. The observed harness found the limit: two of four template changes were *deletions*, and
a replacement-based variant cannot put a deleted section back in its original position. Checking the
whole tree out at a ref is byte-exact by construction and cannot decay as the live files move. The
two mechanisms are therefore distinct and both ship: **git ref for a baseline** (something that
existed — faithful, immutable, so its cache key is stable forever) and **anchored diff for a probe**
(a rewrite you have not shipped, which must raise when its anchor text is gone). Either way it fails
loudly: a baseline that silently materialized empty would read as *"the old prompt already scored
well"*.

**16. Judge layers are opt-in, judged pre-gate, and from a different model family.** Three rules
from the observed harness, all cheap and all load-bearing. Deterministic layers run always and cost
nothing; the LLM judge is opt-in (`--judge-model none` disables it) and its calls are a separate
line in the bill, because a sweep's judge calls can outnumber its generator calls. The judge grades
the **raw** output rather than the post-sanitizer output — the question is whether the prompt
complied, not whether a production backstop cleaned up after it, and how often the backstop fired
is its own deterministic metric. And the judge model should differ in family from the model under
test: self-preference is a real, measured effect and avoiding it is free. One more, worth a line in
the docs because it is invisible when wrong: **every field of a judge's output schema must be
required.** An optional flag is one the model can quietly decline to fill, and an unfilled flag
reads downstream as a pass.

## Execution

### Branch and ordering

This is the **second** of three related plans:

1. `docs/todo/20260822-engine-computed-code-version.md` — **hard prerequisite.** Without computed
   `code_version`, editing a prompt does not invalidate its arm, and this harness inherits the
   read-stale-numbers trap it exists to remove. Do not start before it verifies.
2. `docs/todo/20260822-evals-llm-eval-harness.md` — this file.
3. `../oryxflow-claude-plugin/docs/plans/20260822-eval-commands.md` — the plugin commands that
   drive this API.

Same topic branch as plan 1 (`20260822-evals`); if starting fresh:

```bash
cd <oryxflow repo root>
git checkout 20260822-evals || git checkout -b 20260822-evals
```

Do not commit or push until verification passes and the user asks. This plan file goes in the same
commit as its code, with an `## Implementation notes (divergences from the plan as built)` section
appended here if anything changed.

### Shared contracts — quote these into every subagent prompt

Parallel agents integrate only if they agree on three things. All three are defined in this file
and must be pasted into the prompts, not summarized:

- **The row schema** — the table in step 5 (`_to_frame`). Every downstream consumer reads only
  these columns. It is the seam (design decision 2), so it is also what makes metric/verdict/report
  testable with no pydantic-evals object present.
- **The `Metric` dataclass** — the definition in step 3, field for field.
- **The truncation rule** — design decision 12 and the `_to_frame` note: never shorten in place;
  emit `<field>` full and `<field>_preview` capped.

### Subagent orchestration

Fan out with the Agent tool, file-disjoint per wave. Every prompt carries: this plan's absolute
path, the step numbers owned, the exclusive file list, the three shared contracts above, and
*"do not create or edit any file outside your list; if you believe you need to, stop and report."*
Each agent writes its own tests and runs them before reporting.

**Wave 0 — you, not an agent** (everything else imports through it): step 1 (`setup.py` extra and
`packages`) plus a minimal `oryxflow/evals/__init__.py` containing only the import guard. Re-exports
are added later, in wave 4.

**Wave 1 — four agents in parallel. Leaf modules, no interdependencies:**

| Agent | Step | Exclusive files |
|---|---|---|
| A · cases | 2 | `oryxflow/evals/cases.py`, `tests/test_evals_cases.py` |
| B · metric | 3 | `oryxflow/evals/metric.py`, `tests/test_evals_metric.py` |
| C · stats | 4 | `oryxflow/evals/stats.py`, `tests/test_evals_stats.py` |
| D · baseline | 4b | `oryxflow/evals/baseline.py`, `tests/test_evals_baseline.py` |

Agent C's test 10 (clustering) and agent D's test 28 (a file deleted in the working tree) are the
two that prove their modules are correct rather than merely present — call them out in the prompts.

**Wave 2 — one agent:** step 5, `oryxflow/evals/task.py` + `tests/test_evals_task.py` +
`tests/test_evals_frame_truncation.py`. Depends on `metric`. It owns the row schema, so it must
implement it exactly as quoted.

**Wave 3 — two agents in parallel:**

| Agent | Step | Exclusive files |
|---|---|---|
| E · sweep | 6 | `oryxflow/evals/sweep.py`, `tests/test_evals_sweep.py` |
| F · cli | 7 | `oryxflow/evals/cli.py`, `tests/test_evals_cli.py` |

**Wave 4 — one agent:** steps 8 and 9 — `__init__.py` re-exports and the seam note, the docs page,
`mkdocs.yml` nav + `llmstxt` sections, the blog link lines, `CHANGELOG.md`.

**Wave 5 — you:** run the full suite, confirm the four-file baseline still passes **without the
`evals` extra installed** (a user who does not do evals must be unaffected), then run the manual
end-to-end. Tests 18, 19 and 20 — zero new cells on re-run, only the new arm on a third arm, only
the affected arm after a function edit — are the caching claims this whole plan rests on; verify
their output yourself rather than trusting a report.

## Implementation

New package `oryxflow/evals/`, added to `packages=[...]` in `setup.py`. Depends on the companion
plan `20260822-engine-computed-code-version.md` for prompt invalidation; implement that first.

### 1. `setup.py` — the extra

```python
    extras_require={
        ...,
        'evals': ['pydantic-evals', 'scipy', 'typer']},
    packages=['oryxflow','oryxflow.targets','oryxflow.tasks','oryxflow.evals'],
```

Nothing in `oryxflow/evals/` may be imported from `oryxflow/__init__.py` at module scope — a user
without the extra must be unaffected. Import is explicit: `import oryxflow.evals as ev`. The
package's `__init__` raises a clear `ImportError` naming `pip install oryxflow[evals]` if
`pydantic_evals` is missing.

### 2. `oryxflow/evals/cases.py` — `load_cases`

```python
def load_cases(path, inputs=None, expected=None, name_col='name'):
    """Load eval cases from a .csv / .yaml / .jsonl file into pydantic-evals ``Case`` objects.

    Column routing, by name, with no configuration:
      * ``name``                      -> the case name
      * a field of the ``inputs`` model -> that input field (validated by pydantic)
      * ``expected`` / ``expected.*``  -> the expected output
      * everything else                -> case metadata, which becomes a DataFrame
                                          column and is therefore available as a report slice

    A value of the form ``@some/path.md`` is replaced by that file's text, resolved
    relative to the case file. This is what keeps a flat CSV usable with realistic
    inputs -- a long fixture document lives in one file instead of being repeated
    down a column.
    """
```

Implementation notes:

- Dispatch on suffix: `.csv` via `csv.DictReader` (stdlib, not pandas — the loader must work
  before any DataFrame exists), `.yaml` via `yaml.safe_load`, `.jsonl` line-by-line via `json`.
- `inputs=` is a pydantic model; construct it with the matching columns so a typo'd column from a
  spreadsheet is a `ValidationError` naming the field, not a silent default. This is the main
  reason moving cases out of Python is safe.
- Booleans from CSV: accept `true/false/1/0/yes/no`, case-insensitive, when the target field or
  metadata value is unambiguous. Metadata values keep CSV strings otherwise.
- `@path` resolution happens before model construction. A missing file raises `FileNotFoundError`
  naming the case and the column.
- Reserved metadata names to document (not enforce): `synthetic`, `holdout`, `expect_*`.
- `holdout` truthy excludes the case from the returned list, and `load_cases` records the excluded
  count on the returned list's `.excluded` attribute for the report to state.

### 3. `oryxflow/evals/metric.py` — `Metric`

A record, deliberately not a DSL:

```python
@dataclass
class Metric:
    """One number a sweep is judged on.

    ``column`` is a boolean column averaged into a rate -- accuracy, write rate,
    false-positive rate, pass rate are all this shape. Pass a callable taking the
    frame instead when it is not.

    ``where`` filters rows: a column name, or ``~column`` for its negation.
    ``budget`` (guardrails) is the value that must not be exceeded in the
    ``higher_is_better=False`` direction; a winner that breaks it is reported as
    not a clean win.
    """
    label: str
    column: str | Callable
    where: str | None = None
    higher_is_better: bool = True
    budget: float | None = None
```

Plus `compute(df) -> (value, n, numerator)` and `subset(df)`.

### 4. `oryxflow/evals/stats.py` — the interval

```python
def rate_ci(df, metric, confidence=0.95, n_resamples=2000):
    """Bootstrap confidence interval for a rate, clustered by case.

    Resamples CASE NAMES with replacement, not rows: with ``repeat=3`` the three
    runs of one case are correlated, and treating them as independent samples
    reports an interval roughly sqrt(3) too narrow. Delegates to
    ``scipy.stats.bootstrap`` (BCa); this function only builds the clusters.
    """

def delta_ci(df, metric, arm_a, arm_b, ...):
    """Interval for (arm_a - arm_b), resampling the SAME case names for both arms
    so the pairing is preserved -- a paired comparison is materially tighter than
    differencing two independent intervals, and both arms ran the same cases."""
```

`scipy` does the resampling and the interval; do not hand-roll either. Clamp reported rate
intervals to [0, 1]. When every case agrees (a rate of exactly 0 or 1) BCa degenerates — fall back
to a Wilson interval and note it, rather than reporting `[1.0, 1.0]` from 22 cases.

### 4b. `oryxflow/evals/baseline.py` — the two ways to build a comparison arm

Two mechanisms, deliberately distinct (design decision 15). Both fail loudly, because a comparison
arm that silently becomes a no-op reports the old version as scoring well.

```python
def git_tree(ref, paths, dest=None):
    """Materialize files from a git ref into a directory, for a BASELINE arm.

    Use this for a prompt/template tree as it was before a change: it is
    byte-exact by construction, cannot decay as the live files move, and -- the
    reason it exists -- it restores DELETED files, which a string-replacement
    variant cannot do at all.

    ``ref`` is anything git resolves ('abc123^', 'v2.1', 'main~3'). Because a
    resolved ref is immutable, an arm built from one has a cache key that is
    stable forever.

    Raises RuntimeError when the ref yields no files under ``paths`` -- a
    silently empty baseline reads as "the old version already scored well",
    which is the worst possible failure for a comparison.

    ``expect_absent=`` names files that must NOT exist at this ref; their
    presence means the ref is wrong and the arm is measuring nothing.
    """
```

Implementation: `git ls-tree --name-only <ref> <path>` to enumerate, `git show <ref>:<path>` per
file, written under `dest` (default a stable directory beside the eval, gitignored) so it is
materialized once and reused. Return the directory path. The caller points its own loader at it —
we do not know how a user's templates are loaded, and must not guess.

```python
@dataclass
class Variant:
    """An exact (old -> new) replacement applied to already-rendered text, for a
    PROBE arm -- a rewrite that has not shipped, so there is no ref to check out.

    Raises when ``old`` is not present: a variant whose anchor text has been
    edited away silently becomes a no-op, and then reports the unchanged prompt's
    numbers as the variant's.

    NOT for baselines. Use ``git_tree`` there: a replacement cannot restore a
    deleted section, and cannot put one back in its original position.
    """
    old: str
    new: str
```

`code_version()` for a `git_tree` arm should return the resolved commit SHA (`git rev-parse <ref>`),
not the ref string — `HEAD~1` names different content over time, and a cache key that moves under a
stable-looking name is the same failure class the companion plan exists to fix.

### 5. `oryxflow/evals/task.py` — `TaskEval`

```python
class TaskEval(oryxflow.tasks.TaskPqPandas):
    """One cell of an eval matrix: one arm's cases, scored, cached as parquet.

    Declare the dataset, the parameters that define an arm, and one ``case()``
    coroutine. Everything else -- concurrency, retries, repeats, the success/
    failure split -- is pydantic-evals'.

    SEAM: everything downstream of a run reads only the per-case DataFrame, never
    a pydantic-evals object. To use a different runner or report shape later,
    override ``_evaluate()`` and ``_to_frame()``; nothing else needs to change.
    """
    persists = ['cases']

    dataset = None          # pydantic_evals.Dataset
    metric = None           # ev.Metric
    guardrail = None        # ev.Metric | None
    slices = ()             # metadata columns to break the metric down by

    repeats = oryxflow.IntParameter(default=1)
    concurrency = oryxflow.IntParameter(default=4, significant=False)
    max_failure_rate = 0.2
```

`concurrency` is `significant=False` deliberately: it changes how fast the sweep runs, never what
it returns, so it must not fragment the cache.

`run()`:

1. `self.preflight()` — default runs `case()` once on the first case, so a credential or wiring
   failure costs one call and aborts before the sweep. Overridable; `preflight = None` disables.
2. `report = self._evaluate()`.
3. If `len(report.failures) / total > self.max_failure_rate`, raise `EvalFailureRateError` naming
   the rate and the first stacktrace. Do not save.
4. `self.save({'cases': self._to_frame(report)})`.

`_evaluate()`:

```python
        return asyncio.run(self.dataset.evaluate(
            self.case, repeat=int(self.repeats), max_concurrency=int(self.concurrency),
            progress=False, retry_task=self.retry_task))
```

`retry_task` defaults to a `RetryConfig` with a few attempts on transient errors — a 429 is not a
verdict, and letting the exception propagate to `report.failures` is what makes retries the
library's job rather than the user's. **Document explicitly** that `case()` should let exceptions
raise: catching them inside and returning an error field makes pydantic-evals record a *successful*
case, empties `report.failures`, and forces manual filtering downstream. This is a real pattern in
hand-written harnesses and it fights the library.

`_to_frame(report)` produces the row schema every downstream consumer depends on. Document it as
the contract:

| Column | Source |
|---|---|
| `case_name` | `case.name` |
| `source_case_name`, `rep` | `case.source_case_name` (repeat grouping) |
| one column per metadata key | `case.metadata` |
| one column per assertion | `case.assertions[name].value` |
| one column per score | `case.scores` |
| `task_duration_s` | `case.task_duration` |
| `error` | `''` for `report.cases`; `error_message` for `report.failures` |
| output fields | flattened from `case.output` when it is a pydantic model / dict |

Both `report.cases` and `report.failures` become rows, distinguished by `error`. Failures carry
their metadata so a per-slice failure count is possible.

**Truncation rule (design decision 12), enforced here.** `_to_frame()` never shortens a value in
place. A long text column is emitted twice: `<field>` with the full value (parquet handles long
strings; this is what any later re-analysis reads) and `<field>_preview` capped for display, used
by `verdict()` and by `--csv`. The docstring states the reason in one line — *a cap applied where a
scorer can see it turns the harness into the thing being measured* — and the docs page repeats it
as a rule for the user's own output model, which is where the observed failure actually lived.

### 6. `oryxflow/evals/sweep.py` — `sweep` and `EvalResult`

```python
def sweep(target, *, cases=None, dataset=None, metric=None, guardrail=None,
          slices=(), repeats=1, concurrency=4, reset=False, confirm=None,
          name=None, **arms):
    """Run an eval across a grid of arms, cached one cell per arm.

    ``target`` is either a ``TaskEval`` subclass, or a plain async function taking
    ``(inputs, **arm_params)`` -- the function form builds the task for you, so a
    first eval needs no class, no scaffold and no set_dir, and is still cached:

        r = ev.sweep(run_turn, cases=ev.load_cases('cases.csv', inputs=Turn),
                     prompt_version=['prod', 'preship'], repeats=3,
                     metric=ev.Metric('yield', 'wrote'))
        r.verdict()

    Keyword arguments holding a list become the sweep axes. Prints the projected
    call count, the cached/new split and an estimated cost before running.
    """
```

Function form: synthesize a `TaskEval` subclass whose `case()` calls `target`, whose Parameters are
the arm keys, and whose `code_version()` returns the source hash of `target` (via
`codehash`) so editing the function invalidates its cells. Name it after the function so the cache
directory is legible.

Under the hood it builds the params grid and delegates to the existing
`oryxflow.runIterConcat(task, params, reset=reset)` — the concat that tags each flow's rows with
its parameters is already the behavior we want; do not reimplement it.

The pre-run banner needs the cached/new split, which comes from `WorkflowMulti.complete()` per
flow before running — not from a re-run. Cost estimation is deliberately crude and labelled as
such: `calls x per_call_cost` where `per_call_cost` is `None` unless the user supplies it. Do not
ship a vendor price table; it would be wrong within a month.

`EvalResult`:

- `.df` — the concatenated per-case frame, tagged by arm. Always available, always the escape hatch.
- `.verdict()` — prints the block below.
- `.report(path=None)` — writes markdown (default `results/<date>-<name>.md`), returns the path.
- `.best()` — `(arm, value, interval, clean)` where `clean` is False if a guardrail budget broke.

Verdict block, rendered with Rich:

```
needs_web_v3 · 22 cases × 2 arms × 3 reps = 132 calls
  cached 66 · new 66

ACCURACY (higher is better)
  prod      100%  (66/66)   95% CI [94%, 100%]
  preship    64%  (42/66)   95% CI [51%,  75%]
  Δ  prod − preship  = +36pp  [+24pp, +47pp]   outside noise

FALSE POSITIVES (guardrail · budget 5%)
  prod        8%  (1/12)   over budget
  preship     0%  (0/12)

VERDICT  prod wins the primary metric (+36pp, outside noise) but breaks the
         false-positive guardrail (8% > 5%). Not a clean win.

Weakest slices for prod:  phrasing=vague 67% (4/6) · state=empty 50% (1/2)
```

Rules for the verdict line, which are the whole point of having one:

- If the delta interval spans zero: *"inside noise at N reps — raise repeats before calling this real."* No winner is named.
- If a guardrail budget broke: never the phrase "wins" unqualified; always "not a clean win".
- Real-only headline; a `synthetic n=K` line beside it when synthetic cases are present.
- Excluded holdout count stated when non-zero.
- Failure count stated when non-zero, with the note that failures are excluded from rates.
- **Dead-metric flag (decision 13).** Any metric sitting at exactly 0% or exactly 100% in *every*
  arm prints: *"identically N% in all arms — this usually measures the harness, not the model.
  Check the metric's inputs before reading anything else."* Print it above the tables, because if
  it fires, nothing below it is worth reading.
- **Denominator line (decision 14).** Every rate prints its `n`. When a metric's `n` differs
  between arms by more than 20%, print: *"denominator moved (n=A vs n=B) — this rate is not
  directly comparable across arms; read the coverage metric first."* A rate over a filtered subset
  is gameable by shrinking the subset, and this is the only automatic defence against it.

A metric may name a `coverage=` companion Metric. When present it is rendered **first**, above the
quality metrics, with a one-line note that a quality rate is read after it and never alone.

### 7. `oryxflow/evals/cli.py` — `cli(task)`

```python
def cli(task, argv=None):
    """A Typer app for one TaskEval, with flags DERIVED from its Parameters.

    Each declared Parameter becomes a repeatable option (``--model-id`` for
    ``model_id``), so adding a parameter adds a flag and the two cannot drift.
    Built-ins: --repeats --concurrency --reset --check --csv --yes.
    """
```

`--check` runs `preflight()` only and exits — the cheap "do my credentials work" probe.
`--yes` skips the cost confirmation. Without it, the projected call count and cost are printed and
confirmation is required when new (uncached) cells exist.

### 8. `oryxflow/evals/__init__.py`

Re-export `TaskEval`, `Metric`, `sweep`, `EvalResult`, `load_cases`, `cli`, `rate_ci`, `delta_ci`,
`git_tree`, `Variant`, `EvalFailureRateError`. The module docstring carries the **seam note** from design decision 2: the
per-case DataFrame is the interface, `_evaluate()` and `_to_frame()` are the override points, and
everything downstream is testable without pydantic-evals.

### 9. Docs

New page `docs/docs/llm-evals.md`, in `nav` and in the `llmstxt` `sections` in `mkdocs.yml`
(a page missing from `sections` is invisible to `llms.txt` and `llms-full.txt`).

Written for an AI engineer, in benefits: what you type and what you get back. Sections: the six-line
quick eval; cases in a CSV; metric and guardrail; reading the verdict, including what "inside noise"
means and why repeats matter; keeping the baseline runnable; where credentials come from (we do not
manage them — `--check` tells you whether they work); and a short "using a different eval runner"
note pointing at the two override methods. Do not explain the bootstrap's internals on this page.

The three existing blog posts show a hand-written `EvalRun(TaskPqPandas)`. Leave them as published
argument, and add one line to each linking the new page as the shipped implementation.

`CHANGELOG.md` under `## [Unreleased]`.

## Files modified

| File | Change |
|---|---|
| `setup.py` | `evals` extra; `oryxflow.evals` in `packages` |
| `oryxflow/evals/__init__.py` | new — re-exports, import guard, seam note |
| `oryxflow/evals/cases.py` | new — `load_cases` |
| `oryxflow/evals/metric.py` | new — `Metric` |
| `oryxflow/evals/stats.py` | new — `rate_ci`, `delta_ci` over `scipy.stats.bootstrap` |
| `oryxflow/evals/baseline.py` | new — `git_tree` (baseline arms), `Variant` (probe arms) |
| `oryxflow/evals/task.py` | new — `TaskEval`, `EvalFailureRateError` |
| `oryxflow/evals/sweep.py` | new — `sweep`, `EvalResult` |
| `oryxflow/evals/cli.py` | new — `cli()` |
| `docs/docs/llm-evals.md` | new user page |
| `mkdocs.yml` | nav entry + `llmstxt` section entry |
| `docs/blog/posts/*llm-eval*.md` | one link line each to the new page |
| `CHANGELOG.md` | Unreleased entry |
| `tests/test_evals_*.py` | new (see Verification) |

## Verification

**Baseline to hold: 114 passing** on the four-file command (the 86 in `CLAUDE.md` is stale), plus the new files. The existing four-file command must stay
green and must not require the extra:

```bash
python -m pytest tests/test_main.py tests/test_workflow.py \
    tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
```

New tests, all offline — **no test may make a network call**; the task under test is a stub
coroutine returning canned outputs.

`tests/test_evals_cases.py`
1. CSV round-trip: columns route to inputs / expected / metadata as documented.
2. A column that is not a field of the `inputs` model lands in metadata, not silently dropped.
3. A typo'd input column raises `ValidationError` naming the field.
4. `@fixtures/doc.md` loads the file's text; a missing file raises naming case and column.
5. `holdout` truthy cases are excluded and counted.
6. YAML and JSONL produce a list equal to the CSV's for an equivalent fixture.

`tests/test_evals_metric.py` and `tests/test_evals_stats.py` — **built on hand-constructed
DataFrames, importing no pydantic-evals object.** This is the seam test as much as a unit test.
7. `Metric.compute` with `where='col'` and `where='~col'`.
8. A callable `column`.
9. `rate_ci` on a frame where every case agrees returns a clamped interval, not `[1.0, 1.0]`.
10. `rate_ci` clustered: for the same 22 cases at `repeats=1` vs `repeats=3` with identical
    per-case outcomes, the interval must **not** shrink materially — the property that catches an
    un-clustered implementation, which would narrow it by roughly sqrt(3).
11. `delta_ci` on two arms differing by a constant contains that constant.

`tests/test_evals_task.py`
12. `TaskEval` over a stub coroutine and a 4-case dataset saves a frame with the documented schema.
13. Failures above `max_failure_rate` raise and **write no output** — assert the target does not
    exist afterwards.
14. Failures below the threshold appear as rows with `error` set and are excluded from the metric.
15. `preflight` raising aborts before any case runs (assert the stub's call count is 1).
16. `concurrency` is insignificant: two instances differing only in it share a `task_id`.

`tests/test_evals_sweep.py`
17. Function form: `sweep(async_fn, cases=..., prompt_version=['a','b'])` runs without a class and
    returns a frame tagged with `prompt_version`.
18. Second identical `sweep` call runs zero new cells (assert the stub's call count is unchanged) —
    the caching claim, tested.
19. Adding a third arm runs only the third (call count grows by one arm's worth) — the
    marginal-cost claim, tested.
20. Editing the target function invalidates only its cells (depends on the companion plan).
21. `verdict()` names no winner when the delta interval spans zero.
22. `verdict()` says "not a clean win" when a guardrail budget is exceeded, even though the primary
    metric improved.
23. `report()` writes a markdown file containing the arm table and the verdict line.
24. **Dead-metric flag**: a frame where a metric is 0% in every arm produces the "identically 0% in
    all arms — usually the harness" line, printed above the tables. Same for 100%. A metric that
    differs between arms does **not** produce it.
25. **Denominator drift**: two arms whose metric subsets differ by >20% produce the "denominator
    moved" line; equal denominators do not.
26. **Coverage ordering**: a metric declared with `coverage=` renders the coverage metric first.

`tests/test_evals_frame_truncation.py`
27. An output field longer than the preview cap appears in the frame **twice** — `field` full-length
    and `field_preview` capped — and the full column is byte-identical to the model's output. This
    is the regression test for the class of bug where a display cap reaches a scorer.

`tests/test_evals_baseline.py` (uses a scratch git repo built in a tmpdir; no network)
28. `git_tree` materializes files from a ref, including a file **deleted** in the working tree —
    the property a string replacement cannot provide and the reason this function exists.
29. `git_tree` raises when the ref yields no files under `paths`.
30. `git_tree(expect_absent=[...])` raises when a named file is present at that ref.
31. `Variant` raises when `old` is absent from the rendered text, and applies cleanly when present.
32. An arm's `code_version()` built from `git_tree` returns a resolved SHA, so the same ref
    expression resolving to a new commit produces a different cache key.

`tests/test_evals_cli.py`
24. Flags are derived: a `TaskEval` with `model_id` and `prompt_version` exposes `--model-id` and
    `--prompt-version`; adding a Parameter adds a flag with no CLI edit.
25. `--check` runs preflight only and exits without running the sweep.

End-to-end, by hand, once, against a real provider — the acceptance test for the motivating
scenario, and the only step that spends money:

```bash
# six lines in a scratch file, no scaffold: two prompt arms over ~20 cases at repeats=3
python quick_eval.py            # prints call count + cost, runs, prints the verdict
python quick_eval.py            # second run: 0 calls, verdict identical from cache
# edit the prompt template, change nothing else
python quick_eval.py            # only the affected arm re-runs
```

## Implementation notes (divergences from the plan as built)

**Final state: 360 tests passing** (`python -m pytest tests/ -q`); the four-file baseline holds at
114. `import oryxflow` was verified to work with `pydantic_evals`, `scipy`, `typer`, `tenacity` and
`pydantic_ai` all blocked at the import hook, and `import oryxflow.evals` raises the actionable
install message — a user who does not run evals is unaffected.

### Wrong in the plan, corrected in the build

**`RetryConfig` is not pydantic-evals'.** It lives in `pydantic_ai.retries` (package
`pydantic-ai-slim`), which pydantic-evals imports only under `TYPE_CHECKING`, and that module
raises `ImportError` without **tenacity** — an *optional* group of pydantic-ai-slim. So a clean
`pip install oryxflow[evals]` would have failed at import on the plan's default
`retry_task=RetryConfig(...)`. Two changes: `tenacity` joins the extra, and `task.py` imports
`RetryConfig` defensively and falls back to `retry_task = None`. Verified by blocking `tenacity`
in `builtins.__import__` and re-importing.

**`WorkflowMulti` has no `complete()`.** The plan sources the cached/new split from it; the method
does not exist (`CLAUDE.md`'s method list is also wrong on this). `get_flow(name)` returns a
`Workflow`, which does have `complete()`, so `sweep._split_cached()` iterates the per-flow objects.

**`git ls-tree` needs `-r`.** The plan wrote `git ls-tree --name-only <ref> <path>`, which for a
directory path returns only the top tree entry and materializes **nothing** — silently producing
the empty baseline this module exists to prevent, in the module's own prescribed command. The build
uses `git ls-tree -r -z --name-only <ref> -- <paths>` (`-z` avoids git's quoting of non-ASCII
names), reads blobs as **bytes**, and keys everything off the resolved SHA so a `HEAD~1` that moves
mid-run cannot mix two commits into one tree.

**`Dataset(...)` requires `name=`** in the installed pydantic-evals; the function form defaults it
to the target's `__name__`.

**There is no `rep` field on `ReportCase`.** `rep` is derived by numbering occurrences within each
`source_case_name` group, in report order (`report.cases` then `report.failures`). Successes come
before failures within a group: `rep` addresses a repeat group, it is not a run index.

### Added beyond the plan

**`watch=` on `sweep()` — the plan's central promise had a hole in its headline path.** The
synthesized `code_version()` folded in the target's source and the case identity, but **not files
the target *reads*.** Editing a prompt `.md` between two `ev.sweep` calls ran **zero** new cells —
precisely the read-stale-numbers trap the companion plan exists to remove, sitting in the quick
path that is *most* likely to be used right after a prompt edit. Documenting it and pointing at the
class form was not enough: the quick path is the one an engineer reaches for when iterating on
wording. `watch=` takes glob patterns (or a callable) and folds `hash_files` into the digest;
patterns resolve against the cwd, so a script that may be launched from anywhere should pass a
callable with `root=`. It is rejected on the class form, which declares its own `code_version()`.
Four tests cover it, including one that pins the no-`watch` behavior so the default stays
deliberate rather than becoming accidental.

**`cost_per_call=` on `sweep()`** — decision 8 requires a cost line, and the plan's `per_call_cost`
had nowhere to arrive: `**arms` would have swallowed it as a sweep axis.

**`ev.cli` is a callable module.** `cli.py` holds a function of the same name, and a package cannot
expose both under one attribute — binding the function hides the submodule (and breaks
`import oryxflow.evals.cli`), while leaving the module breaks the documented `ev.cli(Task)`. The
`__getattr__` returns a `ModuleType` subclass with `__call__`, so both mean what they say. Typer is
imported lazily on first touch, so `import oryxflow.evals` does not pay for it.

**The bill arithmetic omits `preflight`.** It runs once per *new* arm, so the true count is
`cases x arms x reps + new_arms`. The banner states this rather than quietly under-quoting.

**`runIterConcat`'s default tagging is insufficient for a multi-axis sweep** — it tags each flow
with that flow's params, but `delta_ci` needs a single column to compare on. A `concat_fn` (an
existing kwarg) adds an `arm` column holding the flow name. `WorkflowMulti`'s built-in grid
generators also name flows `0, 1, 2...`, so the `{flow: params}` dict is built explicitly.

**`Metric.subset()` drops rows carrying an `error`** before applying `where`. A case that raised is
not evidence either way, and counting it as a miss would let an outage read as a quality
regression.

**`load_cases` coerces booleans asymmetrically, on purpose.** `true/false/yes/no` convert in any
metadata column; `1/0` convert only in columns boolean by convention (`holdout`, `synthetic`,
`expect_*`). Elsewhere `1` is as likely a count, and a stray bool in an averaged column would
corrupt `Metric.compute`, which does `astype('boolean')`.

**The clustering test needed a positive control.** Verification test 10 says the interval "must not
shrink materially" between `repeats=1` and `repeats=3`. Clustering makes the two bootstraps
*identical* by construction (`3k/3n == k/n`), measured `w3/w1 = 1.000000` — so the assertion alone
is vacuous and a broken implementation could pass it. A control was added: the same 66 outcomes as
66 *independent* cases narrows to `0.52x` the clustered width, against the theoretical
`1/sqrt(3) = 0.577`.

**`stats` signals its fallback rather than hiding it.** `RateCI`/`DeltaCI` carry a `method` field —
`'bca'`, `'wilson'` (BCa degenerated on a rate), `'newcombe'` (the same in a delta), `'empty'` — and
`verdict()` prints a caveat when it is not `'bca'`, instead of passing a Wilson interval off as a
bootstrap. `random_state` defaults to `0`, not `None`, so a cached frame always reports the same
interval.

**`BoolParameter` renders as a two-valued choice, not a flag.** `--strict true --strict false` is a
two-arm sweep, which an on/off switch cannot express. Also: typer 0.27 **vendors** click, so a
real-`click` `BadParameter` escapes typer's handler as a bare exit-1 with no message — a
`ChoiceParameter` surfaces through a synthesized `Enum` instead.

**The template's placeholder rows cannot all be synthetic.** With `synthetic=1` on all three, the
real-only headline came back `n/a (0/0)` and `VERDICT no arm produced a usable rate`, so every
`eval-init` smoke run would have looked like a wiring failure. It ships two real, one synthetic.

**`docs/docs/llm-evals.md` is not doctested.** `TESTED_PAGES` in `scripts/build_docs.py` is an
explicit three-entry dict, so a new page opts out by default. Left out deliberately: the examples
need the `evals` extra and a live provider, and `docs/docs/ai-ready.md` scopes its "examples an
agent reads first are executed by the test suite" claim to exactly those three pages. Every example
on the page was nonetheless executed by hand, and the verdict blocks in it are real captured
output.

## Post-implementation: what dogfooding three real evals changed

The harness was then used to re-implement three evals from a downstream consumer
project — a classifier accuracy eval, a write-rate eval with a control set, and an
LLM-judged contract eval — and to run them against live providers. Four defects and
four papercuts surfaced, none of which the test suite had caught, because every one
of them needed a real dataset or a real judge to appear. They are fixed; the
numbered items below are the design record for *why*.

1. **`sweep` overwrote a case metadata column named `arm`.** `_tagger` assigned the
   flow name to `df['arm']` unconditionally. A dataset comparing an `outline` arm
   with a `guidelines` arm — the natural use of that word, and the whole localiser
   of the eval in question ("if guidelines writes where outline does not, the
   problem is the outline prompt, not the surface mechanism") — lost that column
   silently, and `slices=('arm',)` reported the flow name back with no error. The
   clash is derivable from the cases before anything is spent, so `_check_metadata`
   now raises there, and `arm` joins `RESERVED_METADATA`. `_preserve()` keeps a
   colliding column as `meta_<name>` as a second line of defence.

2. **`Metric` scored an unmeasured row as a failed one.** `astype('boolean').
   fillna(False)` treated a null verdict as a miss. This mattered most where a
   judge grades only part of the frame: rows with no chips, or a surface the judge
   skips, both deflated the numerator and padded the denominator. `Metric` now
   splits `eligible()` (where + errors dropped) from `subset()` (also measured),
   reports `unmeasured()`, and renders `n/a` + `NOT MEASURED` rather than a
   confident `0%` when nothing was graded.

   This is not cosmetic. Re-reading one already-cached run, the headline moved from
   `preship 25% (6/24), D +67pp [+29pp, +88pp]` to `preship 43% (6/14), D +46pp
   [+3pp, +81pp]`, with the unmeasured line and the denominator-drift line both
   firing. The old figure counted ten zero-chip turns as pairing failures; they are
   a *coverage* failure, which the coverage metric was already reporting separately.
   The verdict survived, the effect size did not.

3. **The function form could not score against `expected_output`.** `_make_case`
   passes `target(inputs, **arms)` and nothing else, so the commonest eval shape of
   all — a classifier graded on its expected label — had to build a `Dataset` by
   hand and pass `cases=` and `dataset=` together. `sweep(..., evaluators=...)` now
   forwards to the generated dataset.

4. **`code_version()` in the function form hashes only the target's own source**,
   not the helpers it calls. Left as designed — folding in the whole module would
   re-invalidate on unrelated edits, which is the over-invalidation `code_version`
   exists to avoid — but it is now stated in `sweep`'s docstring, the user docs and
   the plugin skill, together with the fact that it is *not* silent: the engine
   raises a `StalenessWarning` naming the changed file. `watch=` is what converts
   that warning into a re-run, and the docs now show the agent module in the
   pattern list, not only prompt templates.

Papercuts fixed at the same time: a single-value axis no longer appears in the arm
name (it is identical in every arm and set the column width of every table, while
distinguishing nothing — but when *no* axis varies the full name is kept, since a
lone cell called `default` says less); `verdict()` no longer reprints the header
`sweep()` already emitted as the bill; and "Weakest slices" lists only slices
actually below the arm's own rate, since a 100% slice under that heading reads as a
warning about something that is fine.

Two things the exercise confirmed rather than changed. `preflight` caught a broken
`git_tree` on the first real run and cost one call instead of twenty-eight, leaving
the already-finished arm cached. And `watch=` removed the `--reset`-after-editing-a-
prompt warning that both original evals carried in their READMEs — one of them had
refused to cache at all for exactly that reason, calling a cache "mostly a way to
read stale numbers after a prompt edit".

### Second pass: rebuilding an eval from the template rather than porting one

The three evals above were ported — they followed the originals' structure — so
the exercise had tested the library API but never the product an engineer meets
(the template plus the four commands). Rebuilding the largest of them from
`resources/template-eval/` produced something 40% smaller (514 -> 316 lines) and
found four more things, all of which the ports structurally could not:

5. **`Evaluator.evaluate` is the abstract method even when async.** `evaluate_async`
   raises `TypeError` at class creation. The template's `OutputOk` docstring sends
   the reader to an LLM judge and shows only a sync example, so writing one hits
   that immediately. Template now shows the async shape and says a judge belongs in
   the Evaluator rather than inside the case function — which also drops the output
   model from 17 hand-flattened fields to 7.

6. **A sync `case()` silently serializes the sweep.** `concurrency` is scheduled as
   coroutines by pydantic-evals, so a blocking case holds the loop and the cases run
   one at a time while the setting still reads as honored: 8 half-second cases at
   `concurrency=8` take 1.0s async and 4.9s sync, with identical numbers and no
   error. Nothing in oryxflow is async — `core.py` contains no `async def` — so this
   is inherited, and it was documented nowhere. Now a `RuntimeWarning` naming the
   task and the ignored concurrency, plus docs, template and skill.

7. **The dead-metric flag fired on a clean guardrail.** A guardrail identically at
   0% in every arm is the outcome it exists to confirm, and it was being reported as
   "this usually measures the harness". Flagging success trains the reader to skip
   the line that matters when it is real. Now skipped when a guardrail is identical
   and *inside* budget; still flagged when identically over.

8. **`--check` was a symptom, and its documentation was wrong.** The plugin command
   described it as "project the bill, run nothing"; it runs `preflight()` — one
   real, billed call — and prints no bill. Corrected. The design point underneath:
   the safe operation should not require a flag the first-time user has never heard
   of, so the cost confirmation now offers it inline — `Run 56 new calls? (y/n/c=check
   one call first)`, where `c` spends one call, reports whether the wiring works, and
   asks again. Deliberately *offered* rather than run automatically before the bill:
   the obvious fix is to preflight first, but that makes the tool's first billed call
   without consent, which is worse than the ordering it fixes. Declining now exits 0
   with one line instead of a traceback.

The port-versus-rebuild difference also reframes item 4 above: "the function form
hashes only the target's own source" is a property of the path the ports took, not
of the library. The template uses the class form, whose `code_version()` is explicit
per arm and returns `git_sha()` for a baseline arm — an immutable key rather than a
hash of files that can move. Items 1 and 2 are unaffected: both reproduce in the
rebuilt eval, and the unmeasured-metric bug bites harder there, since returning `{}`
from an evaluator is the idiomatic way to say "nothing to judge".

### Third pass: what a fresh eval still costs, and why

Asked what was still large about the rebuilt eval, the honest count is that its 313
lines are **168 lines of code** (72 blank, 36 docstring, 31 import, 6 comment), of
which ~120 is that eval's own subject matter. A complete eval with two arms, an LLM
judge, intervals and caching is **20 lines** (`utest/minimal/`), so the length is
the domain's, not the harness's. Two further changes came out of it, and two more
defects came out of chasing the first one.

9. **Do not hand-write a judge for one question.** pydantic-evals ships
   `LLMJudge(rubric=, model=, include_input=)`, which answers a single yes/no about
   the output and lands as a column the metric reads. The template's `OutputOk`
   docstring pointed the reader at an LLM judge and never mentioned it, so the
   obvious move was to write 35 lines of scaffolding. A custom `Evaluator` is for a
   multi-field verdict or a verdict per item inside the output; docs, template and
   skill now draw that line. (Consistent with preferring a library over a bespoke
   subsystem: no oryxflow judge helper was added.)

10. **`ev.PromptArm`** pairs the directory an arm READS with the cache key it is
    stored under. It saves three lines and that is not why it exists: by hand they
    are two declarations that must agree, and drift is silent -- a baseline arm
    reading a checked-out tree while keyed on a hash of the live files re-runs on a
    live prompt edit and does *not* re-run when repointed at another ref. A
    working-tree arm is keyed on its globs' content, a ref arm on the resolved sha;
    `subdir=` makes both hand back the same layout so the loader is arm-agnostic;
    an arm with no key is refused at construction.

11. **An evaluator that RAISED read as one with nothing to score.**
    `ReportCase.evaluator_failures` was consumed by nothing, so a judge crashing on
    every case left its column absent and the metric reported `n/a` with no
    indication of why -- the same output as a judge that legitimately declined.
    Found by pointing `LLMJudge` at a provider with no key configured. The frame now
    carries `evaluator_errors` / `evaluator_error` and the verdict prints the count
    and the first message ABOVE the rate. `case.labels` was being discarded for the
    same reason (only `assertions` and `scores` were read) and is now a column too.

12. **The `arm` collision guard invented a `meta_arm` from its own write.** An axis
    literally named `arm` sets that column, and `_preserve` then treated the
    sweep's own assignment as a pre-existing user column. Fixed by snapshotting the
    frame's columns before tagging. Surfaced only because the minimal example
    happened to name its axis `arm` -- neither the ports nor the rebuild did.

### Fourth pass: re-reading pydantic-evals, and what the frame was throwing away

The claim that ~110 of the rebuilt eval's lines were "irreducible domain content"
did not survive re-reading the dependency. `_to_frame()` was consuming four of the
sixteen fields `ReportCase` carries and two of the eight on `EvaluationReport`, so
work the report had already done was being discarded and then redone by hand in
the eval.

13. **Six dropped fields, now columns.** `case.metrics` (what
    `increment_eval_metric` recorded -- token counts, retries, anything a task
    measures about its own call), `case.attributes` (`set_eval_attribute`),
    `case.expected_output` (flattened to `expected` / `expected_*`), `case.labels`,
    `case.total_duration`, and `report.report_evaluator_failures`. The
    `expected_*` recovery is the behavioural one: a confusion matrix or a per-label
    breakdown is a crosstab of two frame columns, where before it needed the answer
    key joined back on by case name -- which this plan's own notes had recorded as
    an accepted limitation rather than a defect. `utest/intent_sources` lost that
    join.

14. **Measured usage instead of an estimate.** With `case.metrics` on the frame the
    verdict can print `measured usage: input tokens 9,612, output tokens 2,720`.
    `sweep`'s pre-run bill was documented as "deliberately crude" on the grounds
    that a vendor price table would be wrong within a month -- true, but the reason
    there was no real figure AFTER a run was that the numbers pydantic-evals
    collected were being thrown away. Read from the conventional
    `input_tokens` / `output_tokens` / `total_tokens` / `cost` names rather than
    inferred from any numeric column, since guessing eventually sums something that
    is not a token count.

15. **`LLMJudge` and `Dataset.from_file` exist and were undocumented.** Neither is
    a gap in oryxflow -- both are pydantic-evals -- but the template pointed the
    reader at "an LLM judge" without saying the library ships one, which is how a
    35-line hand-written judge becomes the obvious move for a single yes/no
    question. `load_cases` is NOT redundant against `Dataset.from_file` (CSV,
    `@fixture` references, `holdout` acted on, column routing against an inputs
    model) but the relationship is now stated so a reader can choose.

The general lesson, recorded because it is the one that keeps recurring: every time
this harness looked like it needed more code, the actual finding was that it was
consuming less of its dependency than it should. Items 9, 13, 14 and 15 are all
that shape. A frame built from a report should start by asking what the report
carries, not by listing what the current caller happens to want.

### Fifth pass: checking the design against published eval practice

Everything above was measured against pydantic-evals' API. Reading the practitioner
literature (Husain & Shankar's evals FAQ, and the LLM-as-judge validation work)
surfaced one large omission and three smaller ones.

16. **Nothing validated the judge.** This is the most-emphasized practice in the
    field and the harness had no notion of it: a judge column went straight into a
    rate with a confidence interval around it, which lends borrowed authority to an
    unexamined opinion. Added `ev.judge_alignment(df, judge, human)` -> TPR / TNR /
    Cohen's kappa against a human-labelled sample, and `ev.corrected_rate(observed,
    alignment)` applying the Rogan-Gladen estimator (the standard correction for a
    proportion measured with an imperfect test -- exactly what a judge is).
    `ev.Metric(..., human='<column>')` wires it into the verdict.

    Three details are load-bearing and are enforced rather than documented:
    **kappa is printed with an explicit instruction to read it instead of raw
    agreement**, because agreement inflates under class imbalance (on a 90%-pass
    set, a judge that passes everything scores 90% agreement and kappa 0);
    the correction is **refused** when `TPR + TNR <= 1`, where the denominator
    collapses and the result is amplified noise; and fewer than ~50 labelled rows
    says so.

    The reason this is not cosmetic: an imperfect judge pulls every rate toward its
    own error floor, so it SHRINKS the difference a sweep exists to detect. A true
    85% vs 55% reads as 78% vs 60% through an 88%-accurate judge. When a
    judge-scored delta lands "inside noise", validating the judge is a better move
    than raising repeats -- which is the opposite of the advice the verdict gives,
    and is now said in the skill.

17. **A guardrail was checked against its point estimate only.** The published
    guidance is to act on the interval bound. `0% (0/12)` is entirely consistent
    with a true rate above a 5% budget, so a guardrail whose point estimate clears
    but whose interval does not now says it is "not demonstrated, only not yet
    violated".

18. **A lone arm at 100% was reported as a win.** A case set that never fails
    cannot show a regression or an improvement; the field rule of thumb is that a
    ~70% pass rate is a more useful eval than a 100% one. Flagged as saturated.
    (Multi-arm saturation was already caught by the dead-metric check; this closes
    the single-arm hole, which is exactly the shape a first smoke run has.)

19. **Error analysis did not precede the metric.** `/oryxflow:eval-plan` derived
    the metric from a filed ticket -- which reports the failure somebody NOTICED,
    not the failure distribution. Added a step before the four questions: read
    20-50 real outputs, open-code the problems, axial-code into a counted taxonomy,
    stop at saturation, derive the metric from the most frequent mode, and record
    explicitly when no real outputs were available. Also: prefer binary over 1-5
    scales (adjacent scale points have no stable meaning and need far larger
    samples), and plan judge validation up front rather than after.


### Follow-on plans

Two plans continue this work, in order:

1. `20260823-evals-judge-validation-followup.md` — finishes judge validation (the corrected
   rate currently prints outside the only interval on screen, and the verdict's delta stays
   uncorrected), adds a judge dev/test split, `EvalResult.failures()` for the per-case
   evidence all three sample evals hand-write, 2-D slices, and a plugin `eval-label`
   command so the labels column has a producer.
2. `20260823-evals-ux-review.md` — a walk of the whole surface as a new user meets it. Its
   conclusion: the output side is careful and the input side is not. A misspelled metric
   column bills the whole run and then prints `n/a` with no error, because the helpful
   `KeyError` is caught and discarded; a metric on a float column fails in pandas' words.
   Sequenced second because two of its items touch code the first plan edits.
