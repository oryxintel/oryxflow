---
title: What an eval plan must contain
description: The six sections an LLM eval plan needs before anyone can run it — goal, models, prompts, success measure and hypothesis test, data, credentials — and the checks that each number traces to a column.
---

# What an eval plan must contain

A plan can settle exactly what to measure and still be impossible to run. The gap is always one
of two shapes: **a number with no column behind it**, or **an input with no file behind it**.
Both survive review, because both read as decisions rather than omissions.

Six sections close them. Write all six and a fresh session — a teammate, an agent, you next
quarter — can execute the plan without inventing anything.

| Section | The question it answers | Without it |
| --- | --- | --- |
| [1. The goal](#1-the-goal) | What decision does this number inform? | An eval that measures something adjacent to the problem |
| [2. The models](#2-the-models) | What is judged, and what judges it? | A matrix billed before anyone checks the models fit |
| [3. The prompts](#3-the-prompts) | What is being compared with what? | A baseline that quietly is not the shipped one |
| [4. The measure](#4-the-measure) | What counts as better, and how would you know? | A verdict of *inside noise*, every time |
| [5. The data](#5-the-data) | Which cases, from where? | A plan that stops on its first line |
| [6. Credentials](#6-credentials) | How does the run authenticate? | A whole matrix billed against a bad key |

The rule underneath all six:

!!! tip "The rule"

    Every number in the plan traces to a **column in the per-case table**, and every input to that
    table either **exists on disk** or has a **named source and owner**.

## 1. The goal

The section that costs nothing and saves the most. Write it first, because everything below is
downstream of it — and because an eval written straight from a ticket measures the ticket.

Five things, in this order:

- **The decision this number informs.** Who acts on it, and what they do differently at a good
  result versus a bad one. If no action changes either way, stop — you want a spot-check, not an
  eval.
- **Business background.** What the feature does, who uses it, and what a bad output actually
  costs: a wasted minute, a lost customer, a wrong number in a filing. That cost is what sets how
  much noise you can tolerate in section 4.
- **What ships today.** The current behaviour in one paragraph, and the base prompt it comes from
  by path.
- **The failure being chased**, quoted from a real report rather than paraphrased. Paraphrase
  loses the detail that turns out to be the metric.
- **What "better" would unlock.** The change you would make if the number moved.

Then one honest line: is the failure in the report the *frequent* failure, or just the one
somebody noticed? Reading 20-50 real outputs and counting the failure modes is what separates
those two, and the common outcome is that the reported failure ranks third. If no real outputs
exist yet, say so — that is a known gap, not a blocker, but the plan should carry it rather than
imply a distribution nobody measured.

## 2. The models

Name both, by import path and model id — not by family:

```python
from myapp.llm import get_model

MODEL = get_model('<the model production uses>')   # under test
JUDGE = get_model('<a different family>')          # scoring, when scoring is not deterministic
```

Then justify each in one line, and invite disagreement:

- **Under test: is this what production runs?** Evaluating a bigger model than the one you ship
  answers a different question. If production is pinned to a cheap model for cost or latency, the
  eval is pinned to it too — or the plan says explicitly that the model choice is *also* an arm.
- **Judge: a different family from the model under test.** Self-preference is real, and avoiding
  it is free. A judge scoring its own family's output flatters it.
- **Is a judge needed at all?** When the output is one of a fixed set of labels, scoring is a
  comparison. A judge there buys cost, variance and a second model's opinion on a string equality
  test.
- **Both ids are pinned.** A model id that silently moves to a new snapshot makes two runs
  incomparable while looking identical. Pin the snapshot, and fold it into the arm's
  `code_version()` so a model change invalidates the cell instead of being served from it.

**Ask for a review of this section specifically.** Whoever owns the prompt should say whether
these two models suit the goal in section 1 before anything is billed — model fit is the one
choice that is cheap now and expensive after a full matrix. Record their answer, including a
disagreement you decided to overrule and why.

## 3. The prompts

What is being compared with what. Both by **path**, never paraphrased into the plan:

| Arm | Reads | Cached on |
| --- | --- | --- |
| `baseline` | `prompts/reply.md` at `<ref>` | the resolved sha of `<ref>` |
| `live` | `prompts/reply.md` in the working tree | the hash of the files it reads |

Three things this section has to settle:

- **The baseline is a git ref, checked out.** `ev.git_tree(ref, paths)` materialises it byte-exact
  and restores files the change **deleted** — which a string replacement cannot do at all. Stamp
  the arm with `ev.git_sha(ref)`, never the ref string: `HEAD~1` names different content after
  every commit, and a cache key that moves under a stable-looking name serves stale numbers as
  fresh ones.
- **The eval imports production, never a copy.** A second copy of the prompt inside the eval
  directory drifts from the shipped one the first time either is edited, and after that the eval
  scores the copy. If something has to change to make the entry point callable, change it in
  production.
- **A rewrite that has not shipped is a third arm, not the baseline.** There is no ref to check
  out, so it is `ev.Variant(old, new)` — which raises when its anchor text is gone, rather than
  quietly becoming a no-op and reporting the unchanged prompt's numbers.

Name the entry point by import path too: `app.reply:draft_reply`, not "the reply generator".

## 4. The measure

The section where plans most often stop being executable, because it is where numbers get named
without anything producing them.

### The success measure

One number, and where it comes from:

```python
metric = ev.Metric('quality', 'Good', where='acted',
                   coverage=ev.Metric('action rate', 'acted', where='~control'))
```

Prefer **binary** over a 1-5 scale. Adjacent points on a scale have no consistent meaning between
raters or between runs, and separating them needs a much larger sample. If gradations matter,
decompose into several binary checks.

If the measure is a rate over a filtered subset, it needs a **coverage** companion beside it: a
quality rate improves as its subset shrinks, so a prompt can score well by staying silent on a
third of the cases. Coverage prints first for that reason.

### The baseline to beat

Two numbers, not one: **what the current prompt scores**, and **the threshold at which you would
act**. Write both down before the run. A threshold chosen after seeing the result is not a
threshold.

### The hypothesis test

State it plainly, because the library's verdict is exactly this test:

- **H₀:** the two arms have the same rate.
- **The test:** a paired bootstrap of the difference, resampling **case names** so the repeats of
  one case are never counted as independent observations.
- **The decision rule:** if the interval spans zero, no winner is named — the verdict says
  *inside noise* and means it.

```text
  Δ  candidate − baseline  = +25pp  [-25pp, +75pp]   inside noise
```

Then the question the plan must answer: **what is the smallest difference worth acting on**, and
**can this case count resolve it?** Those are not independent, and getting them wrong is why so
many evals return *inside noise* and feel like a waste.

Roughly, at a 70% baseline and one repeat — simulated with the same `delta_ci` the verdict uses:

| Cases | Detects +5pp | +10pp | +20pp |
| --- | --- | --- | --- |
| 20 | ~10% | ~15% | ~40% |
| 50 | ~10% | ~20% | ~70% |
| 100 | ~15% | ~35% | ~95% |
| 200 | ~20% | ~65% | ~100% |
| 400 | ~35% | ~90% | ~100% |

Read it as a floor, not a promise — real case sets are messier. What it settles is the shape:
**twenty cases can only resolve a large difference.** A 15-25 case set is the right size to get
an eval built and wired, and the wrong size to adjudicate a 10-point claim. If the difference you
care about is small, the plan needs to say where the extra cases come from before anyone runs
anything.

### The guardrail

The thing a prompt can win the headline on by breaking. Write the degenerate strategy as a
sentence first:

> Without a guardrail, **acting on everything** scores 100%.

Then read the proposed guardrail back against that sentence. The failure to look for is a
guardrail that is an **algebraic restatement of the metric**: "quotes that locate ≥ 85%" beside
"quotes that do not locate ≤ 15%" is the same gate twice, satisfied by exactly the arms that
satisfy the metric, and the degenerate strategy walks straight through it.

A guardrail is the **opposite** error, measured on control cases:

```python
guardrail = ev.Metric('false action rate', 'acted', where='control',
                      higher_is_better=False, budget=0.05)
```

### The mechanics — where each number lives

Four checks that turn the section above into something that runs.

**State the table's unit and list its columns.** One line, and it pre-empts more execution-time
confusion than anything else in the plan:

> One row per case, per arm, per repeat. The case function returns `n_findings`, `n_quoted`,
> `n_quote_locates`, `n_fixes`, `n_fixes_dropin`.

Everything downstream — metrics, slices, intervals, the verdict, the report — reads that table and
nothing else, so a gate number no listed column can produce is a gap, visible the moment the
columns sit beside the gates.

**Each number is a boolean column, or a callable.** `ev.Metric('label', 'column')` averages a
**boolean** column into a rate; hand it a fraction or a count and the run stops with
`TypeError: Need to pass bool-like values`. That bites whenever the thing being scored lives
*inside* the output — findings in a review, turns in a conversation, rows in an extraction:

```python
# every case weighs the same: did this document come back clean?
ev.Metric('all quotes locate', 'all_quotes_locate')

# every finding weighs the same: pooled across the set, still clustered by case
ev.Metric('quotes that locate', lambda d: d['n_quote_locates'].sum() / d['n_quoted'].sum())
```

They are different measurements. Say which one the gate number means.

**Budget the slots.** The verdict renders three numbers — coverage, metric, guardrail — plus
`slices=` breakdowns. A gate table with five rows must say which three take the slots and where
the other two print: as a slice, as a second result over the same frame
(`ev.EvalResult(r.df, metric=...).verdict()`), or report-only. A number nobody printed should be
a choice, not an oversight.

**Human labels need a per-case column.** `human='quality_human'` reads a column of the case
table, so a judge can only be checked where one case carries one label. Around 100 labelled cases
is the working target; below about 50 the agreement rates are too noisy to act on. An unvalidated
judge does not merely add noise — it pulls every rate toward its own error floor and **shrinks**
the difference you are trying to measure.

## 5. The data

Cases are most of the work and all of the credibility. The plan needs a **source**, not a
description of a good set: *"documents across a range of genres"* is a rule, and someone still
has to produce them.

### Sampled from real material

Say where from, how many, and how they were chosen:

> 60 cases sampled from support transcripts, Jan-Jun, one per conversation, stratified by
> product area. Sampled from resolved tickets only.

Then name the bias that introduces — the last line above quietly excludes every conversation that
went badly, which is the population the eval most needs.

### Or generated, with the recipe written down

Synthetic cases are not banned, they are **marked**, and the mark changes the arithmetic: the
headline averages over rows where `synthetic` is falsy, and invented cases print beside it.

```text
  synthetic n=4  60% (2/4) -- reported beside the headline, never in it.
```

A rule that marks *every* case synthetic therefore leaves the headline with nothing to average,
and the rate reads `n/a`. If the set has to start out generated, the plan says so, says what the
headline is computed over until real cases land, and records it as a known gap.

Write the recipe so someone else could reproduce the set: which model generates them, the
generation prompt, how many, what varies between them, and what stops them all being the same
case in different words.

### Every set needs these

- **Controls** — cases where doing nothing is correct. The guardrail reads them, and without them
  a prompt that acts on everything scores 100% with no row anywhere disagreeing. Two or three
  cannot move a rate; aim for about a fifth of the set.
- **Fixtures on disk.** A case value starting with `@` is replaced by that file's text, so a plan
  naming `@fixtures/handbook.md` has promised a file. List them, and say which exist today.
- **Holdout, if any case text is embedded in a prompt.** Scoring it would be scoring against your
  own answer key. Mark it `holdout` and it is withheld from every arm, with the count reported.
- **Slice columns.** The metadata worth breaking the winner down by — genre, length, difficulty.
  A flat number hides that one kind of case got worse, and the worst slice is where the next batch
  of cases comes from.

## 6. Credentials

How the run authenticates, in one line: which environment variable or secret store, and who has
access. oryxflow never reads or stores a key — your case function makes the call and your
provider's SDK picks up what it already picks up — so this is about the person running it having
what they need, not about configuring the library.

**Never put a key, token or internal hostname in the plan file.** Name where it lives.

Then the one-call check that it is wired at all, before anything else:

```text
python run_eval.py --check
PromptEval: preflight OK.
```

A credential problem then costs one call instead of a matrix. Say what the first real run should
print, too — one line of the expected shape. A wiring failure produces plausible-looking numbers,
and having written down the right shape in advance is the only defence.

## The final pass

Before the plan is handed over, two questions:

1. Can a fresh session, with no memory of the planning conversation, produce **every input**?
2. Does **every number** name a column, and does something print it?

Anything answered "the executor will work it out" is the seam where an eval stalls. Anything you
cannot answer belongs under **Known gaps** with what would close it — an eval with no known gaps
has not been thought about.

## Read next

- **[LLM eval quickstart](llm-evals-quickstart.md)** — a complete eval in one file.
- **[LLM evals](llm-evals.md)** — the guide: cases, metrics, guardrails, baselines, intervals,
  and reading the verdict.
- **[Classes and functions](llm-evals-api.md)** — everything `oryxflow.evals` exports.
- **[Scaffold an LLM eval](claude-plugin/evals.md)** — the plugin writes these six sections for
  you, then scaffolds an eval from them.
