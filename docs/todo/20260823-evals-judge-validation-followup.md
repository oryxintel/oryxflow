# Judge validation, finished properly

## Context

`oryxflow.evals` gained judge validation on 2026-08-22: `ev.judge_alignment(df, judge,
human)` reports a judge's TPR / TNR / Cohen's kappa against human labels, and
`ev.corrected_rate(observed, alignment)` applies the Rogan-Gladen estimator. Declaring
`ev.Metric(..., human='<column>')` prints both beside the metric.

That closed the largest gap between the harness and published eval practice — a judge
nobody checked is an opinion with a confidence interval printed around it — but it was
built in one pass and left work behind. One item below is a defect in the output it
produces today; the rest were chosen by re-reading three real evals rather than from the
literature, and that re-reading reordered them.

### The defect, as it currently prints

```
PAIRING (higher is better)
  prod      78%  (47/60)   95% CI [67%, 88%]
  preship   60%  (36/60)   95% CI [47%, 72%]
  judge vs 50 human label(s):  TPR 87%  TNR 91%  kappa 0.69  (agreement 88% -- read kappa, not this)
  prod corrected for judge error: 78% -> 89%
  preship corrected for judge error: 60% -> 65%
  D  prod - preship  = +18pp  [+2pp, +33pp]   outside noise

VERDICT  prod wins the primary metric (+18pp, outside noise).
```

Three things are wrong with that block:

1. **`89%` falls outside `[67%, 88%]`**, the only interval on the screen. That interval
   belongs to the *raw* rate. A reader will pair the two, because they are adjacent and
   nothing says not to.
2. **The delta and the verdict are uncorrected.** `+18pp` is the raw gap. The corrected
   gap is `89% - 65% = +24pp`. So the headline number the reader acts on is the one the
   judge distorted, while the corrected numbers sit three lines above it, unused.
3. **The correction has no uncertainty of its own.** It inherits sampling error from the
   rate *and* from the TPR/TNR estimates, and the second source is invisible. With 50
   labels, TPR 87% carries roughly +/-9pp, which moves the correction materially.

Point 2 is the one that changes decisions: an imperfect judge compresses every rate
toward its own error floor, so it *shrinks the effect being measured*. In the run above
the true rates were 85% and 55% (a 30pp gap), the judge reported an 18pp gap, and
correcting recovers 24pp. Reporting the raw delta as the verdict systematically
understates real differences — the exact failure the correction exists to remove.

### Checked against three real evals before prioritising

The three evals this harness was dogfooded against (a classifier, a write-rate eval
with controls, and an LLM-judged contract eval) were re-read to decide what is worth
building. What they show:

| | uses it | note |
|---|---|---|
| an LLM judge | **1 of 3** | and it is unvalidated -- no human labels anywhere in the three |
| pairwise A-vs-B judging | **0 of 3** | all three are pointwise |
| per-case failure evidence | **3 of 3** | ~71 lines hand-written across the ports (37 + 19 + 15) |
| a 2-D slice (phrasing x doc_state) | **1 of 3** | a `pivot_table`, hand-written |
| a per-ITEM ratio (dropped chips / total chips) | 1 of 3 | **already works** -- a callable-column `Metric` gets a clustered CI |

Two conclusions, both of which change this plan:

- **Per-case evidence is the most repeated unmet need in the sample set**, ahead of
  everything else here. Every eval ends by printing the rows that failed with the text
  that shows why -- "a rate says how often, only the closing line beside chip[0] says
  what went wrong" -- and every one of them hand-rolls it. It is promoted to P2.
- **Pairwise is used by none of them**, which confirms guidance-only. It stays last and
  ships no API.

The per-item ratio check is why this table exists: it looked like a gap, and was not.

### The unfinished pieces of judge validation

**No judge-development holdout.** The workflow the feature implies is: label a sample,
run the judge, look at where it disagrees, improve the judge prompt, repeat. Doing that
against one label set tunes the judge to those labels, and the TPR/TNR then measured on
the same labels are optimistic — and so is every rate corrected with them. Standard
practice is a dev/test split: iterate on dev, report on held-out test. The harness has a
`holdout` column already, but it means something different (a case whose text is in the
prompt, excluded from every arm), so this needs its own name.

**Nothing produces the labels.** `human='pairing_human'` names a column that must already
exist. Today the only way to fill it is to hand-edit a case file or a parquet. The
practice this feature is built on assumes ~100 labelled cases, and the literature is
explicit that the labelling should be done by the person who owns the product, not
outsourced. So the missing piece is a path from "I have a run" to "I have labels joined
back onto it", cheap enough that it actually happens.

**Pairwise comparison is unsupported.** Everything here is pointwise: each output is
judged on its own. Pairwise — "which of these two answers is better" — is a common shape
elsewhere, and it has a well-documented failure mode the harness cannot express: position
bias, where a judge prefers whichever answer came first, at reported magnitudes of
10-15pp. The mitigation is to run each comparison in both orders and treat a flipped
verdict as a tie. None of the three sample evals is pairwise, so this ships as guidance
and no API — see step 7.

### Design decisions

- **Correct the delta, do not just display a corrected rate.** The verdict is what people
  act on. Half-correcting — corrected rates above, raw delta below — is worse than not
  correcting at all, because it reads as if the correction was accounted for.
  Confirmed by the defect above.
- **The corrected interval must widen, never narrow.** It carries the rate's sampling
  error plus the alignment's. An interval that came out tighter than the raw one would be
  a bug; the plan pins that as a test.
- **Bootstrap the correction jointly** — resample cases and labels together, apply
  Rogan-Gladen to each resample — rather than propagating error analytically. The
  estimator is a ratio with a difference in the denominator; analytic propagation near
  `TPR + TNR = 1` is unstable, and the harness already bootstraps by case cluster
  everywhere else, so this is the same machinery.
- **A separate column for judge-development holdout**, not a reuse of `holdout`. The two
  answer different questions and a case can legitimately be in one and not the other.
- **Labelling is a command, not a library feature.** The library's job is to accept a
  labels column; producing it is workflow, which is what the plugin is for. This keeps
  `oryxflow.evals` free of anything resembling an annotation UI.
- **Pairwise gets the swap built in or not at all.** A pairwise helper that lets you
  compare A against B in one fixed order would ship the position-bias bug as a feature.
  If the swap is not built in, do not add pairwise.

## Implementation

### 1. `oryxflow/evals/stats.py` — an interval for the corrected rate

Add beside `corrected_rate`:

```python
CorrectedCI = namedtuple('CorrectedCI', 'value low high n n_labels method')


def corrected_rate_ci(df, metric, alignment_df=None, confidence=0.95,
                      n_resamples=2000, random_state=0):
    """Rogan-Gladen corrected rate, with an interval carrying BOTH sources of error.

    Resamples case clusters for the rate and label rows for TPR/TNR together, applies
    the correction to each resample, and takes the percentile interval. The result is
    always at least as wide as the uncorrected interval: a judge you measured on 50
    cases cannot make you more certain than the judge-free rate would.
    """
```

`alignment_df` defaults to `df` (labels live in the same frame). Reuse `_cluster_labels`
for the rate half. Fall back to `method='point'` with NaN bounds when the alignment is
not informative, so the caller renders the existing refusal rather than a fake interval.

Add `corrected_delta_ci(df, metric, arm_a, arm_b, ...)`: the same joint resample applied
to `corrected(a) - corrected(b)`, returning the existing `DeltaCI` shape so the verdict
renderer needs no new branch.

### 2. `oryxflow/evals/sweep.py` — make the corrected number the headline

In `_judge_lines`, render the corrected rate WITH its interval, and label the raw one:

```
  judge vs 50 human labels:  TPR 87%  TNR 91%  kappa 0.69  (agreement 88% -- read kappa)
  corrected for judge error (the rates above are what the JUDGE saw, not what happened):
    prod      78% -> 89%   95% CI [79%, 97%]
    preship   60% -> 65%   95% CI [52%, 79%]
```

In `_delta_line` and `_verdict_lines`: when the primary metric has a `human` column and
the alignment is informative, compute the delta from `corrected_delta_ci` and say which
it is — `D prod - preship = +24pp [+6pp, +41pp] (judge-corrected)`. When the alignment is
not informative, keep the raw delta and append the existing NOT CORRECTED line.

`best()` returns the corrected value in the same case, since it feeds report generation.

### 3. `oryxflow/evals/cases.py` — `judge_dev` as a reserved column

Add `judge_dev` to `RESERVED_METADATA` and `_is_reserved`. Unlike `holdout` it does not
remove the case from the run — it marks which labelled cases are for iterating the judge:

```python
alignment = ev.judge_alignment(df[~df['judge_dev'].fillna(False)], 'pairing', 'pairing_human')
```

`judge_alignment` grows a `dev` parameter (default False) that selects on the column when
present, and its `note` says which split it measured. When every labelled row is
`judge_dev`, the note says the alignment is measured on the same rows the judge was tuned
against and is therefore optimistic.

### 4. `oryxflow/evals/sweep.py` — `EvalResult.failures()`, the evidence block

The most repeated hand-written thing in the sample evals. All three variants are the
same shape — *the rows where this metric failed, showing these columns, grouped by arm*:

```python
def failures(self, metric=None, columns=(), limit=6, arm=None, print_it=True):
    """The rows that failed, with the text that shows why.

    A rate says how often; only the row says what went wrong. `metric` defaults to
    the primary one, and passing the guardrail instead gives its violations -- the
    controls that acted -- which is the same question asked of the other side.
    """
```

Selection is `metric.eligible(df)` minus `metric.subset(df)[column]`-true rows, so it
reuses the same eligibility rules as the rate and cannot drift from it: an errored or
unmeasured row is not a failure. Rows are grouped by arm, capped at `limit` per arm with
the count stated when truncated (never silently), and `columns` are printed one per line
under the case name. Defaults to every `*_preview` column when `columns` is empty, since
those exist precisely to be read.

Returns the selected frame whether or not it printed, so it is also the filter people
currently write by hand.

### 5. `oryxflow/evals/sweep.py` — two-dimensional slices

`slices=('phrasing', 'doc_state')` currently breaks the metric down by each column
separately. Accept a tuple to mean a cross-tabulation instead:

```python
slices = ('phrasing', ('phrasing', 'doc_state'))
```

The 2-D form renders as a small table of rates, and exists because it is what localises
a failure: *"fails on every phrasing when there is no outline, and on none when there
is"* is a finding; two separate 1-D breakdowns showing 60% and 60% is not. Suppress
cells with n below a floor (3) rather than printing a rate from one case.

### 6. Plugin: `commands/eval-label.md` — a new command

`/oryxflow:eval-label [n]`. Between `eval-run` and reading the verdict:

1. Read `evals/<name>/README.md` for the metric and what it asks.
2. Load the cached frame, take a **stratified** sample across arms and the metric's own
   verdicts (over-sampling disagreement-prone regions is fine; say so), default n=100.
3. Write `evals/<name>/labels.csv` — `case_name`, `arm`, the output columns needed to
   judge, an empty `<metric>_human` column, and an empty `judge_dev` column.
4. Print the labelling rubric from the README: the SAME question the judge is asked, in
   the same words, so the human and the judge answer one question and not two.
5. STOP. The human labels the file. This is the step that must not be automated: an agent
   filling that column is the judge grading its own homework.
6. On re-invocation with a filled file: join on `case_name` + `arm`, report alignment,
   and show every disagreement so the judge prompt can be improved against dev rows.

Mark it `disable-model-invocation: true` like the other four.

### 7. Plugin: pairwise, only with the swap

Add to `skills/oryxflow/evals.md` under judging: a pairwise comparison must be run in both
orders and a flipped verdict recorded as a tie, with the position-bias magnitude
(10-15pp) as the reason. Add `ev.Metric('preferred', 'wins_both_orders')` as the shape.

Do NOT add a library pairwise helper in this pass. Land the swap rule as guidance, see
whether a real eval needs it, and only then decide whether it earns API surface.

## Files modified

- `oryxflow/evals/stats.py` — `corrected_rate_ci`, `corrected_delta_ci`, `CorrectedCI`;
  `judge_alignment` grows `dev=`.
- `oryxflow/evals/sweep.py` — `_judge_lines` renders intervals; `_delta_line`,
  `_verdict_lines`, `best()` use the corrected delta when available; new
  `EvalResult.failures()`; `_slice_lines` accepts a tuple for a 2-D breakdown.
- `oryxflow/evals/cases.py` — `judge_dev` reserved and documented.
- `oryxflow/evals/__init__.py` — export the new names.
- `tests/test_evals_stats.py` — corrected interval never narrower than raw; recovers a
  known true rate; degenerate alignment returns no interval; dev/test split honoured.
- `tests/test_evals_sweep.py` — verdict quotes the corrected delta and says so; raw delta
  when the alignment is uninformative; corrected rate never printed without its interval;
  `failures()` excludes errored and unmeasured rows, states truncation, and accepts the
  guardrail; a 2-D slice suppresses thin cells.
- `docs/docs/llm-evals.md` — the judge section gains the interval and the dev/test split.
- `CHANGELOG.md` — under `[Unreleased]`.
- `../oryxflow-claude-plugin/commands/eval-label.md` — new.
- `../oryxflow-claude-plugin/skills/oryxflow/evals.md` — labelling loop, pairwise swap.
- `../oryxflow-claude-plugin/commands/eval-run.md` — point at `eval-label` when a metric
  declares `human=` and the column is empty.
- `../oryxflow-claude-plugin/docs/CHANGELOG.md` — under `[Unreleased]`.

## Verification

1. `python -m pytest tests/ -q` — baseline is **415 passing**; this adds roughly 12.
2. The defect above is gone: build the demo frame from `## Context`, print the verdict,
   and confirm (a) every corrected rate carries an interval, (b) the corrected rate lies
   inside its own interval, (c) the delta line says `judge-corrected`, (d) the corrected
   interval is wider than the raw one for the same arm.
3. A judge with `TPR + TNR <= 1` still prints `NOT CORRECTED` and no interval.
4. `judge_alignment(..., dev=False)` on a frame where every label is `judge_dev=True`
   reports the optimism note.
5. `python -m mkdocs build` clean; `llm-evals` page shows the new judge block.
6. Plugin: `claude --plugin-dir <plugin repo>`, `/reload-plugins`, then `/oryxflow:eval-label`
   on a scaffolded eval — it must write `labels.csv`, print the rubric, and STOP without
   filling a single label.
7. **The samples are the acceptance test.** Rewrite the three ported evals in `utest/` to
   use `failures()` and the 2-D slice, and confirm the ~71 lines of hand-written reporting
   go to roughly zero with identical output. If any of the three still needs its own
   printer, `failures()` has the wrong shape and should be reworked before it ships.

## Not in this pass

- **Error-analysis tooling** (open/axial coding support). `/oryxflow:eval-plan` now
  prescribes the loop in prose; whether it needs tooling should be answered by running it
  a few times, not guessed at.
- **A library pairwise API.** Guidance first — see step 5.
- **Intervals on TPR/TNR themselves.** The joint bootstrap already carries their
  uncertainty into the corrected interval, which is where it changes a decision. Printing
  a second pair of intervals beside the alignment line adds noise without adding a choice.
