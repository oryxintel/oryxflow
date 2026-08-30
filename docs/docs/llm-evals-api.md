---
title: Classes and functions
description: Everything oryxflow.evals exports, grouped by when you meet it — the six names you use every time, then arms and baselines, judge validation, statistics, and errors.
---

# Classes and functions

Everything `oryxflow.evals` exports, grouped by **when you meet it** rather than alphabetically.
Six names cover an ordinary eval; the rest are there when a specific question comes up.

```python
import oryxflow.evals as ev
```

Nothing here is imported by `import oryxflow`. It needs the extra: `pip install "oryxflow[evals]"`.

## The six you use every time

| Name | What it is |
| --- | --- |
| **`sweep(target, *, cases=None, dataset=None, metric=None, guardrail=None, slices=(), repeats=1, concurrency=4, reset=False, confirm=None, name=None, cost_per_call=None, watch=None, evaluators=(), **arms)`** | Run an eval across a grid of arms, one cached cell per arm. `target` is a plain async function taking `(inputs, **arm_params)` — or a `TaskEval` subclass. Any keyword holding a **list** becomes an axis. Returns an `EvalResult`. |
| **`load_cases(path, inputs=None, expected=None, name_col='name')`** | Load cases from `.csv` / `.yaml` / `.jsonl`. Columns route by name: `name`, then any field of your `inputs` model, then `expected`; everything else becomes metadata you can filter and slice on. Returns a `CaseList`. |
| **`Metric(label, column, where=None, higher_is_better=True, budget=None, coverage=None, human=None)`** | One number the arms are judged on. `column` is a boolean column averaged into a rate — or a callable taking the frame. See [below](#what-metric-quietly-does-for-you). |
| **`EvalResult`** | What a sweep returns. `.df` is the per-case frame, `.verdict()` prints the comparison, `.report(path)` writes it as markdown, `.best()` returns `(arm, value, interval, clean)`. |
| **`TaskEval`** | Subclass it when the eval is something you keep: one `Parameter` per arm axis, a `dataset`, a `metric`, a `guardrail`, and a `code_version()` that decides when a cell recomputes. See [the guide](llm-evals.md). |
| **`cli(task, argv=None)`** | A command line for one `TaskEval`, with flags **derived from its Parameters** — so adding an arm axis adds its flag and the two cannot drift. Built-ins on top: `--repeats --concurrency --reset --check --csv --yes`. |

### What `Metric` quietly does for you

Four behaviours you'd otherwise have to remember, and which are the difference between a rate and
a *believable* rate:

- **Errored rows are excluded** from every rate. A case that raised is not evidence either way,
  and counting it as a miss would let an outage read as a quality regression.
- **Unmeasured rows are excluded too.** A null in the metric column means *not measured*, which
  is not the same as measured-and-failed — an LLM judge that grades only some rows, or was
  switched off, leaves nulls behind. A metric with no measured row at all reports `n/a`, never
  `0%`.
- **`where=`** filters rows: a column name, or `~column` for its negation.
- **`coverage=`** names a companion metric answering "was there any output at all", and is
  rendered **first** — because a quality rate over a shrinking subset improves as the subset
  shrinks.

`budget=` (with `higher_is_better=False`) is the guardrail value that must not be exceeded; a
winner that breaks it is reported as not a clean win. `human=` names a column of human labels —
see [judge validation](#judge-validation).

## Arms and baselines

For the common case — "is the new prompt better than the one we shipped?" — the baseline arm has
to read the *old* files and be cached on the *old* content. These four make that automatic.

| Name | What it is |
| --- | --- |
| **`git_tree(ref, paths, dest=None, expect_absent=None, repo=None)`** | Materialize files from a git ref into a directory, for a baseline arm. Byte-exact by construction, and — the reason it exists — it restores **deleted** files, which a string-replacement variant cannot do at all. |
| **`git_sha(ref, repo=None)`** | Resolve a ref to its full commit SHA. An arm built from a ref must report *this* as its `code_version()`, never the ref string: `HEAD~1` names different content after every commit, and a cache key that moves under a stable-looking name reads stale numbers as fresh ones. |
| **`PromptArm(ref=None, paths=(), globs=(), root=None, subdir='', expect_absent=(), dest=None)`** | Where one arm reads its prompt files **and** the cache key that matches them, in one object: `.dir()` and `.code_version()`. Written by hand these are two places that drift silently. |
| **`Variant(old, new)`** | An exact replacement applied to already-rendered text, for a **probe** arm — a rewrite that hasn't shipped, so there is no ref to check out. Raises `VariantError` when `old` is absent, because a variant whose anchor was edited away silently becomes a no-op and reports the unchanged prompt's numbers as the variant's. |

## Judge validation

A judge nobody checked is an opinion with a confidence interval printed around it. Hand-label a
sample, and these tell you whether to believe the rest.

| Name | What it is |
| --- | --- |
| **`judge_alignment(df, judge, human)`** | How well the judge agrees with human labels, on the rows that have both. Returns a `JudgeAlignment`. |
| **`JudgeAlignment`** | `(tpr, tnr, kappa, agreement, n, n_pos, n_neg, informative, note)`. Read **kappa**, not agreement: on a lopsided sample a judge that says "pass" every time posts high agreement and zero skill. |
| **`corrected_rate(observed, alignment)`** | The Rogan-Gladen correction — an observed rate adjusted for a known-imperfect judge: `(observed + tnr - 1) / (tpr + tnr - 1)`. The standard estimator for a prevalence measured with an imperfect test, which is exactly what an LLM judge is. |

You rarely call these directly: set `human=` on a `Metric` and the verdict reports all of it.

## Statistics

The intervals under every rate. Exposed because a number without one is not a measurement.

| Name | What it is |
| --- | --- |
| **`rate_ci(df, metric, confidence=0.95, n_resamples=2000, random_state=0)`** | Bootstrap interval for a rate, **clustered by case**. Resamples case names, not rows: with `repeats=3` the three runs of one case are correlated, and treating them as independent reports an interval roughly √3 too narrow. Returns a `RateCI`. |
| **`delta_ci(df, metric, arm_a, arm_b, arm_col=None, ...)`** | Interval for `(arm_a − arm_b)`, resampling the **same** case names for both arms so the pairing is preserved — materially tighter than differencing two independent intervals, and both arms ran the same cases. Returns a `DeltaCI`, whose `spans_zero` is the "inside noise" verdict. |
| **`cluster_labels(df)`** | The case identity of each row. Exposed because the clustering, not the bootstrap, is what makes these intervals right — group by this for per-case aggregates. |
| **`RateCI`** | `(value, low, high, n, n_clusters, numerator, method)` |
| **`DeltaCI`** | `(value, low, high, n_a, n_b, n_clusters, method, spans_zero)` |

## Errors

Each one exists to turn a silent wrong number into a loud stop.

| Name | Raised when |
| --- | --- |
| **`EvalFailureRateError`** | More than `max_failure_rate` of cases raised. The run saves **nothing** — a parquet of exceptions is indistinguishable from a measurement the next time someone opens it. |
| **`BaselineError`** | A baseline could not be built from the ref you named. |
| **`VariantError`** | A `Variant`'s anchor text was not found. A `ValueError`, because the arguments no longer describe the text they're applied to. |

## The rest

| Name | What it is |
| --- | --- |
| **`CaseList`** | What `load_cases` returns: a `list` of cases plus `.excluded`, the count of `holdout` rows dropped. A case whose text is embedded in the prompt under test is scoring against its own answer key, and a silent exclusion is as misleading as none at all. |
| **`RESERVED_METADATA`** | Metadata names the harness owns: `synthetic`, `holdout`, `arm`, `expect_*`. Using one for your own column raises before anything is billed, rather than being overwritten mid-run. |
| **`build_app(task)`** | The Typer app behind `cli`, separately available so you can mount it under a larger application or drive it with `typer.testing.CliRunner`. |

## The seam

Everything downstream of a run — `Metric`, the slices, the intervals, the verdict, the report —
reads only the **per-case DataFrame** and never touches a pydantic-evals object. Two methods on
`TaskEval` are the exit:

- **`_evaluate()`** — runs the cases and returns a report. Replace it to use a different runner.
- **`_to_frame(report)`** — turns that report into the per-case table. Replace it to accept a
  different report shape.

That's also why you can build an `ev.EvalResult` from a DataFrame you made yourself and get the
full verdict out of it.

## Read next

- **[LLM eval quickstart](llm-evals-quickstart.md)** — a complete eval in one file.
- **[LLM evals](llm-evals.md)** — the full guide, with the reasoning behind each of these.
- **[What an eval plan must contain](llm-evals-checklist.md)** — goal, models, prompts, measure,
  data, credentials: the six sections a plan needs before anyone can run it.
- **[API Reference](reference.md)** — the generated reference for the rest of the library.
