# `oryxflow.evals` UX review, and the fixes it implies

**Sequenced after `20260823-evals-judge-validation-followup.md`.** That plan finishes
judge validation and adds `EvalResult.failures()`; this one is a walk of the whole
surface as a new user meets it, and the smaller fixes that walk turned up. Do it second:
two items here touch code that plan is editing, and both are noted where they collide.

## Context

`oryxflow.evals` was built in one pass and then hardened over a long session of
dogfooding — roughly two dozen changes, most of them guards added in response to a real
eval going wrong. Nothing has since looked at the surface *as a whole*, from the position
of someone who has never seen it. That is what this is.

The verdict of the walk: **the output is in good shape and the input is not.** Once a run
completes, the harness is careful, hedged and hard to misread. Getting to that point, the
same care is missing: the three most likely first-mistakes all produce either silence or a
message from pandas that names nothing the user wrote.

### What the surface looks like now

24 public names. Six of them are the everyday API and the other eighteen are machinery a
normal user never types:

| tier | names | who touches it |
|---|---|---|
| everyday | `sweep`, `Metric`, `load_cases`, `TaskEval`, `cli`, `EvalResult` | everyone |
| comparison arms | `PromptArm`, `git_tree`, `git_sha`, `Variant` | anyone comparing against a baseline |
| judging the judge | `judge_alignment`, `corrected_rate`, `JudgeAlignment` | anyone with an LLM judge |
| statistics | `rate_ci`, `delta_ci`, `cluster_labels`, `RateCI`, `DeltaCI` | anyone re-analysing `.df` |
| errors + misc | `BaselineError`, `VariantError`, `EvalFailureRateError`, `CaseList`, `RESERVED_METADATA`, `build_app` | raised at you, not called |

That is not too many names, but nothing in the docs says which tier is which, so the
reference page reads as 24 equally-weighted things. The library already does this well
for the task-body API (three tiers, low to high: `input()` / `inputLoad()` /
`inputLoadConcat()`); the eval API just has not been given the same treatment.

### The happy path is short and honest

A complete eval — cases from a file, two arms, an LLM judge from a different model
family, intervals, one cached cell per arm — is 23 lines, and the output leads with what
it refuses to claim:

```
VERDICT  terse vs helpful = +0pp [-56pp, +56pp], inside noise at 1 rep -- raise repeats
         before calling this real.
```

Nine separate guards can fire in one block: failed cases excluded and counted, measured
token usage, coverage printed above quality with the reason, unmeasured rows held out of
the rate, judge alignment with kappa flagged as the number to read, a corrected rate, a
guardrail the interval does not demonstrate, weakest slices, and the refusal itself. That
half of the system is the good half. **Do not churn it.**

### Finding 1 — a typo in a metric costs money and says nothing

The single worst thing in the surface. Misspell the metric column and the run completes,
bills every call, and prints:

```
X (higher is better)
  a   n/a  (?/0)

VERDICT  no arm produced a usable rate.
```

No error. No mention of the column. `Metric.compute` *does* raise a good `KeyError` —
`"metric 'x': column 'wrote' is not in the frame (have [...])"`, which names the column
and lists the real ones — and `EvalResult._values` catches it and substitutes `NaN`:

```python
try:
    out[arm] = metric.compute(self._arm_rows(arm, real=real))
except KeyError:
    out[arm] = (NAN, 0, None)
```

So the most useful message in the module is written, raised, and thrown away. The same
applies to a misspelled `where=`. Both are ordinary first-run mistakes — a metric names a
column that an evaluator or output model was supposed to produce, and any rename breaks
it silently.

This interacts badly with a guard added earlier in the same session: a wholly unmeasured
metric now prints `NOT MEASURED ... check that whatever fills it actually ran`. That
message is right for a judge that was switched off and actively misleading for a typo,
and there is currently no way to tell the two apart.

### Finding 2 — a metric on a non-boolean column fails in pandas' words

```
TypeError: Need to pass bool-like values
```

That is the whole message. It does not name the metric, the column, the frame, or what a
metric expects. A **float** column is the likely way in, because pydantic-evals
*scores* are floats: `ev.Metric('quality', 'SomeScoreEvaluator')` is a natural thing to
write and produces exactly this. The fix is not only a better message — a metric over a
score wants a threshold, and there is currently no way to say one.

### Finding 3 — `corrected_rate` is the only stat that returns a bare number

Every other statistic returns a named tuple carrying its method and its bounds
(`RateCI`, `DeltaCI`, `JudgeAlignment`). `corrected_rate` returns a float, so a caller
cannot ask how it was produced or whether it was clipped. The follow-up plan adds
`corrected_rate_ci`, which makes the inconsistency worse rather than better if the bare
version stays as the obvious one to reach for.

### Finding 4 — two ways to say the same thing, neither marked primary

`confirm=False` in code and `--yes` on the command line; `--check` as a flag and `c` at
the confirmation prompt. Both pairs are defensible — one is for scripts, one for a
terminal — but the docs introduce them in different places without saying which is the
normal one, so a reader meets the same decision twice and has no basis for it.

### Finding 5 — the plugin's four commands are a pipeline nobody states

`/oryxflow:eval-plan` -> `eval-init` -> `eval-cases` -> `eval-run` is a designed sequence,
and each command's own file explains its own step well. Nothing shows the sequence in one
place at the moment a user needs it — which is *before* they run the first one. The skill
lists them, but the skill is loaded on demand and a user typing `/oryxflow:eval-` sees
four descriptions with no ordering.

The gap inside that pipeline is already known and planned:
`ev.Metric(human='<column>')` accepts a labels column that no command produces, so the
judge-validation feature has no on-ramp. That is `eval-label` in the preceding plan; it is
restated here only because it is the pipeline's one broken link, not to duplicate the work.

### Design decisions

- **Fix the input side; leave the output side alone.** Every finding above is on the way
  in. The verdict is the product of a lot of careful hedging and is not to be churned.
- **A typo must raise, not degrade.** Silence is only correct when the *user's* data is
  absent (an unjudged row); it is never correct when the user's *declaration* is wrong.
  These need to be distinguishable, which means the swallow has to become selective
  rather than merely louder.
- **Raise before spending wherever the mistake is knowable up front.** A metric that
  names a column the frame will never contain cannot always be caught before a run --
  the frame does not exist yet -- but a metric naming a column no evaluator, output field
  or case-metadata key could produce often can be. Where it cannot, fail on the first
  cell rather than after the last.
- **A threshold on a score is a `Metric` argument, not a lambda.** `Metric('quality',
  'Score', threshold=0.7)` reads as what it is; `lambda d: (d.Score > 0.7).mean()` loses
  the numerator, the CI clustering and the error/unmeasured filtering that the string
  form gets for free.
- **Tier the reference, do not shrink the API.** All 24 names earn their place; the
  problem is presentation, and the library already has a house style for it.

## Implementation

### 1. `oryxflow/evals/sweep.py` — stop swallowing the message

Replace the blanket `except KeyError` in `EvalResult._values` with one that distinguishes
a missing column from an empty one. `Metric.compute` already raises `KeyError` naming the
column; let it out.

```python
def _values(self, metric, real=True):
    out = {}
    for arm in self.arms:
        try:
            out[arm] = metric.compute(self._arm_rows(arm, real=real))
        except KeyError:
            # A column the frame does not have is a DECLARATION error -- the metric
            # names something nothing produces -- and must not read as "no data".
            raise
    return out
```

If a genuine per-arm-missing-column case turns up (an arm whose evaluator did not run at
all), handle it by checking membership explicitly and reporting it as unmeasured, with
the arm named. Do not restore the blanket catch.

**Collision:** the preceding plan edits `_values`' callers for the corrected delta. Land
that plan first, then this.

### 2. `oryxflow/evals/sweep.py` — check the metric before the run

In `sweep()`, beside `_check_metadata`, add `_check_metric_columns(dataset, metric,
guardrail, task_cls)`:

- Collect the names the frame is *guaranteed* to carry: `case_name`, `source_case_name`,
  `rep`, `error`, `task_duration_s`, `total_duration_s`, `evaluator_errors`, every case
  metadata key, `expected` / `expected_*` when cases carry expected output, and every
  evaluator's `get_default_evaluation_name()` where the dataset exposes it.
- If a metric's `column` or `where` is a string outside that set **and** the target's
  return annotation is a pydantic model whose fields also do not contain it, raise before
  spending, listing what is available.
- Where the output shape cannot be determined (no annotation, a dict return), say nothing
  — a false positive here would block a legitimate eval, which is worse than the silence
  it replaces.

### 3. `oryxflow/evals/metric.py` — a real message, and a threshold

Wrap the boolean coercion in `compute`:

```python
try:
    series = sub[self.column].astype('boolean').fillna(False)
except (TypeError, ValueError):
    raise TypeError(
        "metric '{}': column '{}' holds {} values, which cannot be averaged into a "
        "rate. A metric is a BOOLEAN column averaged into a rate -- pass "
        "threshold= to turn a score into one, or a callable for anything else."
        .format(self.label, self.column, sub[self.column].dtype))
```

Add `threshold: Optional[float] = None`. When set, the column is compared
(`series > threshold`, or `< threshold` when `higher_is_better=False`) before averaging,
and the metric's rendered label says so (`quality (score > 0.7)`), so a reader of the
verdict knows a threshold was applied and what it was. Reject `threshold` together with a
callable column.

### 4. `oryxflow/evals/stats.py` — make `corrected_rate` consistent

Have `corrected_rate` return the same named tuple `corrected_rate_ci` returns (from the
preceding plan), with NaN bounds and `method='point'` when no interval was requested.
Keep a float-returning path only if a test proves something needs it.

**Collision:** this is the same tuple that plan introduces. Do them together or not at all.

### 5. `docs/docs/llm-evals.md` — tier the reference

Add the five-tier table from `## Context` above the API reference section, in the same
shape as the task-body tiers elsewhere in the docs. Name `sweep` / `Metric` /
`load_cases` as the three anyone needs, and mark the statistics names as "for re-analysing
`.df`; the verdict calls these for you".

Also settle Finding 4 in prose: `confirm=`/`--yes` and `--check`/`c` each get one sentence
saying which is the normal one (code: `confirm`; terminal: the prompt) and why the other
exists.

### 6. Plugin: state the pipeline where it is needed

- `commands/eval-plan.md`, `eval-init.md`, `eval-cases.md`, `eval-run.md`: one line at the
  top of each — `Step N of 4: plan -> init -> cases -> run` — so any entry point shows the
  whole sequence and where the reader is in it.
- `skills/oryxflow/SKILL.md`: the eval trigger currently points at the skill; have it name
  the first command explicitly, since a user who has not run one does not know that
  `eval-plan` comes before `eval-init`.

## Files modified

- `oryxflow/evals/sweep.py` — `_values` re-raises; new `_check_metric_columns` called
  from `sweep()` before the bill.
- `oryxflow/evals/metric.py` — typed-column error message; `threshold=`.
- `oryxflow/evals/stats.py` — `corrected_rate` returns the named tuple.
- `tests/test_evals_metric.py` — threshold on a score column; a string column raises a
  message naming the metric and the column; threshold with a callable is refused.
- `tests/test_evals_sweep.py` — a misspelled metric column raises rather than printing
  `n/a`; a misspelled `where` likewise; `_check_metric_columns` refuses before any call is
  made and stays quiet when the output shape is unknowable.
- `docs/docs/llm-evals.md` — tier table; the `confirm`/`--yes` and `--check`/`c` sentences.
- `CHANGELOG.md` — under `[Unreleased]`.
- `../oryxflow-claude-plugin/commands/eval-*.md` — step-N-of-4 line.
- `../oryxflow-claude-plugin/skills/oryxflow/SKILL.md` — name the entry command.
- `../oryxflow-claude-plugin/docs/CHANGELOG.md` — under `[Unreleased]`.

## Verification

1. `python -m pytest tests/ -q` — baseline **416 passing** plus this plan's additions
   (~10) and the preceding plan's (~12).
2. The three findings, by hand, in a scratch directory:
   - a metric naming a column nothing produces raises **before** any call is billed, and
     the message lists what is available;
   - a metric on a float column raises a message naming the metric and the column, and
     the same metric with `threshold=0.7` runs and renders the threshold in its label;
   - a metric on a column that exists but is empty for every row still prints
     `NOT MEASURED`, unchanged — the two cases must not have collapsed into one.
3. `python -m mkdocs build` clean; the tier table is on the `llm-evals` page and in
   `site/llms-full.txt`.
4. Re-run the ported evals under `utest/` — all three must produce byte-identical
   verdicts, since nothing here changes a number.

## Not in this pass

- **Shrinking the public API.** All 24 names are used; the problem is that the docs
  present them flat. Presentation first, and only revisit if the tiering shows something
  genuinely unused.
- **A metric DSL.** `threshold=` covers the common score case. Anything more expressive is
  what the callable form is for, and a query language would be a bespoke subsystem
  competing with pandas.
- **Interactive labelling UI.** `eval-label` in the preceding plan writes a CSV and stops;
  that is the right amount of tooling until someone has used it a few times.
