---
title: LLM evals
description: Run an LLM eval as a cached parameter sweep — one cached cell per arm, so a re-run costs nothing and editing a prompt re-runs only what it touched. Confidence intervals, guardrails, and a verdict that refuses to name a winner inside the noise.
---

# LLM evals

You changed a prompt. Is it better?

`oryxflow.evals` answers that in six lines and one command. It runs your cases across every arm
you want to compare, keeps one cached result per arm, and prints a verdict that will not name a
winner when the gap is inside the run-to-run noise, and will not call one a *clean* win when it
broke a guardrail.

The scoring is [pydantic-evals](https://pydantic.dev/docs/ai/evals/) — cases, evaluators,
repeats, concurrency, retries. What oryxflow adds is the matrix and the memory: **a second run
of an unchanged arm costs nothing, and editing your function or your case file re-runs only the
cells that read it.** There is no `--reset` to remember. The model's outputs and their scores are
cached separately, so rewriting a scorer or a judge rubric re-scores what you already paid for
without calling the model again.

```
pip install "oryxflow[evals]>=26.10.6"
```

Read [What pydantic-evals already gives you](#what-pydantic-evals-already-gives-you) before writing
a scorer of your own: judges, tool-call checks, confusion matrices and per-case setup all ship
there.

Nothing in `oryxflow.evals` is loaded by `import oryxflow`, so if you never run evals it costs
you nothing. Import it on purpose:

```python
import oryxflow.evals as ev
```

!!! tip "In a hurry?"

    The [quickstart](llm-evals-quickstart.md) is a complete eval in one file — cases, two arms, a
    judge, a verdict — with each line explained. This page is the reasoning behind it.

## The six-line quick eval

No class, no scaffold, no output directory to configure. Write the async function you want to
compare — it takes one case's inputs plus whatever you are varying — and hand it to `ev.sweep`:

```python
import oryxflow.evals as ev
from pydantic import BaseModel


class Turn(BaseModel):
    question: str
    phrasing: str = 'plain'


async def run_turn(inputs, prompt_version):
    return await my_assistant(inputs.question, prompt=prompt_version)   # your code
```

```python
r = ev.sweep(run_turn,
             cases=ev.load_cases('cases.csv', inputs=Turn),
             prompt_version=['prod', 'preship'],
             repeats=3,
             metric=ev.Metric('accuracy', 'correct'),
             guardrail=ev.Metric('false positives', 'false_positive',
                                 higher_is_better=False, budget=0.05),
             slices=('phrasing',))
r.verdict()
```

Any keyword holding a **list** becomes an axis — `prompt_version=['prod', 'preship']` is two
arms; add `model_id=['small', 'large']` and you have four. A keyword holding a single value is
just a setting applied to every arm.

Before anything is billed you get the projected bill:

```
NeedsWeb · 22 cases × 2 arms × 3 reps = 132 calls
  cached 0 · new 132   (+1 preflight call per new arm)
  estimated cost: not estimated -- pass cost_per_call= for a crude calls x cost_per_call figure
```

Run it again, having changed nothing, and the second line is the whole point:

```
NeedsWeb · 22 cases × 2 arms × 3 reps = 132 calls
  cached 132 · new 0
```

Add a third arm and you pay for the third arm. Edit `run_turn` and its arms re-run; everything
else you have already evaluated stays on disk.

A few knobs worth knowing:

| You want | Pass |
| --- | --- |
| A cost figure in the bill | `cost_per_call=0.004` — your number; no vendor price table ships |
| To never be asked before spending | `confirm=False` |
| To always be asked, even from a notebook | `confirm=True` |
| To re-run these arms from scratch | `reset=True` |
| More runs per case, to tighten the noise band | `repeats=5` |
| More cases in flight at once | `concurrency=8` — speed only; it never changes the result |

And a few things to do with the result:

```python
r.df                       # the per-case table, tagged by arm — always your escape hatch
r.verdict()                # the block below, printed
r.report()                 # the same as markdown -> results/<date>-<name>.md
r.best()                   # (arm, value, interval, clean) — clean is False if a guardrail broke
```

### When you want a scaffold after all

Six lines is right when you want the answer **today**. For an eval you intend to *keep* — re-run
after every prompt change, hand to a teammate, cite in a review next quarter — the
[Claude Code plugin](claude-plugin/evals.md) writes one for you: four commands that settle what
you're measuring before any code exists, scaffold it into `evals/<name>/`, grow a real case set,
and run it. It is the same `ev.TaskEval` and `ev.cli` documented below, so nothing here becomes
irrelevant — you just don't type the boilerplate.

## Cases live in a file, not in Python

Twenty cases fit in Python. Five hundred do not — and by then the person who has the real
examples is a domain expert who does not edit Python, and your case set has stopped being
reviewable as a diff. So write them in a spreadsheet:

```
name,question,phrasing,state,synthetic,holdout
c01,where is the depot?,vague,empty,false,false
c02,how many are left?,plain,filled,false,false
c03,what changed today?,plain,filled,true,false
```

```python
cases = ev.load_cases('cases.csv', inputs=Turn)
```

Columns route themselves, with nothing to configure:

- `name` is the case name.
- Any column that is **a field of your `inputs` model** becomes that field, validated by
  pydantic. That validation is what makes a file safe: misspell `question` as `quesiton` and you
  get a `ValidationError` naming `question`, not a silent default.
- `expected`, or a set of `expected.<field>` columns, becomes the expected output.
- **Everything else becomes case metadata** — which becomes a column of `r.df`, so it is
  available to `slices=` and to your own analysis. Nothing is dropped.

`.yaml` and `.jsonl` files load through the same call, with the same routing.

### A long document in one cell

A value starting with `@` is replaced by that file's text, resolved relative to the case file:

```
name,question,context
c04,what changed?,@fixtures/handbook.md
```

That is what keeps a flat CSV usable with realistic inputs: a fixture document lives in one file
instead of being pasted down a column. A missing file is an error naming the case and the
column.

(pydantic-evals can also load a dataset straight from YAML or JSON with `Dataset.from_file`,
including the evaluators. Use that when you want the evaluators declared in the file too;
use `load_cases` when you want a spreadsheet, `@file` references, or `holdout` acted on.)

### Three column names mean something

- **`synthetic`** — a truthy value marks the case as machine-generated. Synthetic cases are
  reported *beside* the headline number and never inside it. A synthetic set written by the same
  mind that wrote the prompt flatters it. That is arithmetic, not a label: mark **every** case
  synthetic and the headline has nothing left to average, and the rate reads `n/a`.

    The flag tracks where the **content** came from, not whether a model touched it. An LLM that
    picks, trims or reformats real material — pulling ten threads out of a support log, cutting a
    document down to the section you need — hands you real cases: `synthetic=0`. An LLM that
    *writes* the material — inventing a support thread nobody sent — hands you synthetic ones:
    `synthetic=1`. Ask what the words are, not who typed them. Read it the other way and every
    harvested case gets marked `synthetic=1`, which is the empty headline above.

- **`holdout`** — a truthy value withholds the case from **every** arm, and the verdict states
  how many were withheld. Use it for any case whose text you have embedded in a prompt: scoring
  it would be scoring against your own answer key.

- **`arm`** — reserved for the sweep's own label. Every row is tagged with the arm it came
  from, so a column of your own called `arm` would be shadowed and `slices=('arm',)` would
  quietly report the arm label instead of your column. A sweep refuses that name up front,
  before it spends anything, and tells you to rename it. Call yours `surface`, `variant`,
  `write_arm` — anything else.

`expect_*` is reserved by convention for per-case expectations; it lands in metadata like any
other column.

## The metric, and the guardrail

A metric is one number the sweep is judged on. Most are a boolean column averaged into a rate —
accuracy, write rate, false-positive rate and pass rate are all that shape:

```python
ev.Metric('accuracy', 'correct')
```

The column can be anything in the per-case table: an evaluator's result, a score, a field of
your output model, or a metadata column from the case file.

To score a case against the `expected` column in your case file — the shape every classifier
eval has — hand the sweep an evaluator. Your function is given the inputs and nothing else, so
comparing against the expected answer is the evaluator's job:

```python
from pydantic_evals.evaluators import EqualsExpected

r = ev.sweep(classify, cases=cases, evaluators=(EqualsExpected(),),
             metric=ev.Metric('accuracy', 'EqualsExpected'),
             prompt_version=['v1', 'v2'])
```

```python
# only the rows where the assistant answered at all
ev.Metric('quality', 'good', where='answered')

# ...or only the rows where it did not
ev.Metric('silent quality', 'good', where='~answered')

# lower is better, and it must stay under 5%
ev.Metric('false positives', 'false_positive', higher_is_better=False, budget=0.05)
```

Pass a metric with a `budget` as `guardrail=` and it changes what the verdict is allowed to say.
A change that raises "did the assistant act" *by acting on everything* is a regression, not a
fix, and only a control set catches it — so an arm that wins the primary metric and breaks the
guardrail is reported as **not a clean win**, never as a win.

### When the thing you score is inside the output

`ev.Metric('label', 'column')` averages a **boolean** column into a rate — one case, one verdict.
That covers most evals, and it stops with `TypeError: Need to pass bool-like values` when the
column holds a fraction or a count instead.

You meet that the moment the thing being scored lives *inside* the output rather than being the
output: findings in a review, turns in a conversation, rows in an extraction. One case holds five
findings, and "how many of them were usable" is not a per-case yes or no.

Return the parts as columns, and pick the question you actually mean:

```python
async def run_case(inputs, arm):
    findings = await review(inputs.document, arm=arm)
    ok = [f for f in findings if f.quote in inputs.document]
    return {'n_findings': len(findings), 'n_locates': len(ok),
            'all_locate': len(ok) == len(findings)}
```

```python
# every case weighs the same: did this document come back clean?
ev.Metric('all quotes locate', 'all_locate')

# every finding weighs the same: the pooled rate across the whole set
ev.Metric('quotes that locate', lambda d: d['n_locates'].sum() / d['n_findings'].sum())
```

A callable is handed the frame the metric applies to — `where=` already applied, errored and
unmeasured rows already dropped — and returns the number. The interval underneath it is still
**clustered by case**, so five findings from one document never count as five independent
observations.

The two are different measurements, not two spellings of one. A single document with thirty bad
findings barely moves the first and dominates the second. Choose before you run, not after you
see which looks better.

### When the output is a list, not a score

Your extractor returns eight sections. Your notes-to-todos step returns five items. Nothing in
either output says *good*, there is no score to average, and no obvious column to put in a
metric. What do you measure?

Decompose it into binary checks, and score the free ones first. Do not invent a 1-5 quality
scale on the way: adjacent points on a scale mean different things to two raters, and different
things to the same judge an hour later, so separating them needs a far bigger case set than
separating yes from no.

**The deterministic checks are free — spend them first.** No judge, no reference answer, no
tokens, and on a structured output there are more of them than people expect.

A document split into ordered sections:

- every section's text is a verbatim span of the source
- no two sections overlap
- the sections come in source order
- the fraction of the document that landed in some section at all

Meeting notes turned into todo items:

- the item parses into the schema at all
- the assignee is one of the meeting's attendees
- the due date resolves to a real date
- no two items are the same item

Return the counts, and each of those becomes a column:

```python
async def run_case(inputs, arm):
    sections = await split(inputs.document, arm=arm)
    verbatim = [s for s in sections if s.text in inputs.document]
    return {'n_sections': len(sections),
            'n_verbatim': len(verbatim),
            'verbatim_rate': len(verbatim) / len(sections),
            'covered': covered_fraction(sections, inputs.document),
            'in_order': is_ordered(sections, inputs.document),
            'all_verbatim': len(verbatim) == len(sections)}
```

Now the metric almost everyone writes first, because `verbatim_rate` is the number they want:

```python
ev.Metric('sections verbatim', 'verbatim_rate')
```

```
TypeError: Need to pass bool-like values
```

That message names neither the metric nor the column, so: `verbatim_rate` holds a *fraction per
case*, and a metric column is averaged as a **boolean**. Two fixes, and they answer different
questions — pick before you run:

```python
# one verdict per case: did this document come back entirely clean?
ev.Metric('every section verbatim', 'all_verbatim')

# one verdict per section: pooled across every section in the set
ev.Metric('sections verbatim', lambda d: d['n_verbatim'].sum() / d['n_sections'].sum())
```

`covered` is a fraction as well, and gets the same treatment: pool it with
`lambda d: d['covered'].mean()`, or decide up front what counts as covered
(`'well_covered': covered > 0.95`) and average that boolean.

#### One giant section passes every check

Read that deterministic list back against an arm that returns the whole document as a single
section. Its text is a verbatim span of the source. It overlaps nothing. It is trivially in
order. It covers 100% of the document. It scores four out of four, beats every arm that did the
work, and has done none of it.

That is the clearest demonstration on this page of why a guardrail can never be a rearrangement
of the metric — "sections verbatim ≥ 95%" and "sections not verbatim ≤ 5%" are the same gate
twice, and the giant section walks through both. The guardrail here has to be **granularity**,
measured on the same cases in the opposite direction:

```python
guardrail = ev.Metric('wrong granularity', 'granularity_off',
                      higher_is_better=False, budget=0.20)
```

...where `granularity_off` is per case, and true when the split came back far coarser or far
finer than the document's own structure implies. Write the degenerate strategy down as a
sentence first — *"returning one section scores 100%"* — and read the proposed guardrail back
against that sentence. If the sentence still wins, the guardrail is not one.

#### Then match, then judge what is left

Where you have a reference — someone wrote down the items the output should contain — align each
predicted item to a reference item and count three things:

- **recall**: reference items that some predicted item matched
- **precision**: predicted items that matched a reference item
- **field accuracy, conditional on a match**: of the items that matched, how many got the
  assignee and the date right

Conditional matters. An invented item with the wrong assignee is one precision failure; scoring
its fields too counts the same mistake twice and makes both numbers worse than the system is.

```python
metric = ev.Metric('items recalled',
                   lambda d: d['n_matched'].sum() / d['n_reference'].sum())

guardrail = ev.Metric('items with no support in the source',
                      lambda d: d['n_unsupported'].sum() / d['n_items'].sum(),
                      higher_is_better=False, budget=0.05)
```

The guardrail is doing real work in that pair. **"Emit everything" wins recall outright** — a
prompt that lists every sentence of the notes as a todo item recalls 100% — and dies on the
fabrication rate. Recall alone is not a measure of anything.

Where you have no reference, a judge answers **one binary question per item**: *is this item
supported by the source?* Each verdict is a column, and the pooled rate is precision under
another name.

**Recall is unmeasurable without a reference, and no judge fixes that.** The judge sees the
source and the items you extracted. It can tell you an item is unsupported. It can never tell
you what was missed, because the missing item is not in front of it — nothing in the run
contains it. So a judge-only eval measures precision and quietly calls it quality. That sentence
is what decides whether hand-labelling a reference set is in scope, and it belongs in your
plan's data section rather than arriving as a surprise three weeks in.

### Judging two candidates head to head

With no reference answer and no absolute scale, the judged question that holds up is not "score
this out of five" but **"which of these two is better"**. Show the judge both outputs, ask for
one winner.

The catch is position bias: the same pair, presented the other way round, gets a different
answer often enough to invent a winner on its own. So make presentation order an arm and let the
sweep run both:

```python
async def compare(inputs, order):
    base, cand = baseline_output(inputs), candidate_output(inputs)
    first, second = (base, cand) if order == 'ab' else (cand, base)
    winner = await judge_pick(inputs.document, first, second)
    return {'prefers_candidate': winner == ('second' if order == 'ab' else 'first')}

r = ev.sweep(compare, cases=cases, order=['ab', 'ba'],
             metric=ev.Metric('candidate preferred', 'prefers_candidate'))
```

Two arms, two numbers, and both are worth having:

- The candidate winning at **both** orders is a real preference.
- The **gap between the orders is your judge's position bias** — measured, in points, rather
  than assumed. A candidate that wins by 30 points in one order and loses in the other has told
  you about your judge, not about your prompt.

Nothing special is happening here: `order` is an ordinary arm axis, so the `Δ` line between `ab`
and `ba` arrives with its interval like any other, and a bias inside the noise is one you do not
have to act on yet.

### What the per-case table already holds

Everything the run recorded is a column, so most "I need to compute X" turns out to be a
column you already have:

| column | where it comes from |
| --- | --- |
| your output fields | the model your case function returned |
| `expected`, `expected_*` | the case's expected output — so a confusion matrix is a crosstab, not a join |
| one per evaluator | assertions, scores and labels, by evaluator name |
| your metadata columns | every non-input column of your case file, ready to slice on |
| `input_tokens`, ... | anything the task recorded with `increment_eval_metric` |
| `task_duration_s`, `total_duration_s` | timing per case |
| `error`, `evaluator_errors` | the case raised / its evaluators raised |

Record what a call cost from inside your case function and it lands in the table, and the
verdict reports the total:

```python
from pydantic_evals import increment_eval_metric

async def run_case(inputs, arm):
    result = await my_assistant(inputs.request)
    increment_eval_metric('input_tokens', result.usage.input_tokens)
    increment_eval_metric('output_tokens', result.usage.output_tokens)
    return Output(...)
```

```
  measured usage: input tokens 9,612, output tokens 2,720
```

### Judging quality: don't write a judge if you don't need one

For a single yes/no question about the output — *"does the reply name a specific city",
"is the tone right", "did it refuse"* — pydantic-evals already ships the judge. Pass it a
rubric and a model, and the verdict becomes a column your metric reads:

```python
from pydantic_evals.evaluators import LLMJudge

r = ev.sweep(answer, cases=cases,
             evaluators=(LLMJudge(rubric='The reply names one specific city.',
                                  model=judge_model, include_input=True),),
             metric=ev.Metric('names a city', 'LLMJudge'),
             style=['terse', 'helpful'])
```

Use a **different model family** from the one under test — self-preference is real and
avoiding it costs nothing here.

Write your own `Evaluator` only when one verdict is not enough — when you need several
fields per case, or a verdict per item inside the output. Then return a dict and each key
becomes its own column, and return `{}` for a case there is nothing to judge: those rows
read as *not measured*, which is not a failure.

Put the judge in the evaluator, never inside the function under test. Scoring is the
evaluator's job, a judge that fails is then a scoring failure rather than a lost case, and
you avoid flattening judge fields into your output model by hand.

### Check the judge before you believe it

A judge nobody checked is an opinion with a confidence interval printed around it. Label a
sample of cases yourself, put those labels in a column, and name it:

```python
metric = ev.Metric('pairing', 'pairing_holds', human='pairing_human')
```

Now the verdict says how far the judge can be trusted, and corrects the rate for what it
gets wrong:

```
  judge vs 50 human label(s):  TPR 87%  TNR 91%  kappa 0.69  (agreement 88% -- read kappa, not this)
  prod corrected for judge error: 78% -> 89%
  preship corrected for judge error: 60% -> 65%
```

**Read kappa, not agreement.** Raw agreement inflates whenever one label dominates: if 90%
of outputs really do pass, a judge that says "pass" to everything scores 90% agreement and
has learned nothing. Kappa nets out the agreement chance would give you, so that judge
scores 0 — and the correction is refused outright, because a judge carrying no signal
cannot have its output corrected into an estimate.

The correction matters more than it looks. An imperfect judge pulls **every** rate toward
its own error floor, so it shrinks the gap you are trying to measure: a true 85% vs 55%
reads as 78% vs 60% through a judge that is 88% accurate. Correcting recovers most of the
difference.

Aim for **100 or so labelled cases**; below about 50 the rates are too noisy to act on,
and you will be told so.

### Where an arm reads its prompts, and the key that matches

An arm that *reads* one set of files but is *keyed* on another is a silent staleness bug:
a baseline arm reading a checked-out tree while keyed on a hash of the live files re-runs
when you edit a live prompt, and — worse — does not re-run when you point it at a
different ref. `ev.PromptArm` owns both, so they cannot drift:

```python
ARMS = {
    'live':     ev.PromptArm(globs=['prompts/*.jinja2'], root=ROOT, subdir='prompts'),
    'baseline': ev.PromptArm(ref='v2.1', paths=['prompts/'], root=ROOT, subdir='prompts'),
}

# where the arm reads:     ARMS[arm].dir()
# what it is cached on:    ARMS[arm].code_version()
```

The working-tree arm is keyed on the **content** of its globs; the ref arm on the resolved
**sha**, which never moves. Both hand back the same layout, so your loader never has to
know which arm it got.

### Write your case function `async`

Not because oryxflow needs it — the engine has no async in it at all — but because
`concurrency` is scheduled by the eval runner, and a blocking function holds the loop:

```python
async def run_case(inputs, prompt_version):
    return await my_assistant(inputs.request)     # concurrency works
```

A plain `def` runs and returns exactly the same numbers, so nothing tells you anything is
wrong. What it costs is the concurrency you asked for: eight half-second cases at
`concurrency=8` take **1.0s** async and **4.9s** sync. On a real sweep that is minutes
against the better part of an hour. If your SDK is sync-only, keep `concurrency=1` and
know the sweep is serial — you get a warning otherwise.

Rows that raised are excluded from every rate: a case that errored is not evidence either way,
and counting it as a miss would let an outage read as a quality regression. The count is stated
in the verdict.

**A row with no verdict is excluded for the same reason.** If your metric column is empty for a
row — an LLM judge that only grades some cases, or one you switched off — that row is *not
measured*, which is not the same as measured and failed. Counting it as a miss would deflate the
rate and pad the denominator at once, so a prompt that passed every case anyone actually judged
would report a third of that. The verdict states how many rows carried no verdict:

```
  8 of 16 eligible rows carry no pairing verdict and are OUT of the rate
  (unmeasured is not failed) -- the rate is over the 8 that were.
```

And when *nothing* was measured you get a refusal rather than a confident zero:

```
  NOT MEASURED: no row has a pairing verdict, so there is no rate here -- not a 0%.
  Check that whatever fills it actually ran.
```

Cases stop counting entirely when too many of them fail. Above 20% (`max_failure_rate`) the run
raises and **saves nothing** — a stored table of exceptions would be indistinguishable from a
measurement the next time someone opened it.

## Reading the verdict

```
NeedsWeb · 22 cases × 2 arms × 3 reps = 132 calls
  cached 0 · new 132

ACCURACY (higher is better)
  prod      86%  (57/66)   95% CI [68%, 95%]
  preship   55%  (36/66)   95% CI [36%, 77%]
  Δ  prod − preship  = +32pp  [+18pp, +55pp]   outside noise

FALSE POSITIVES (guardrail · budget 5%)
  prod       9%  (6/66)   95% CI [0%, 32%]   over budget
  preship    0%  (0/66)   95% CI [0%, 15%]

VERDICT  prod improves the primary metric (+32pp, outside noise) but breaks the false
         positives guardrail (9% > 5%). Not a clean win.

Weakest slices for prod:  phrasing=vague 62% (15/24)  phrasing=plain 100% (42/42)
```

**Read it in this order** — it prints in that order too, because the checks that can invalidate
everything below them come first.

1. **Any `!!` line above the tables.** A dead metric, or a saturated eval. If one fired, stop
   reading and go look at what feeds the metric.
2. **The coverage rate**, when the primary metric has a `coverage=` companion. A quality rate
   over a shrinking subset improves as the subset shrinks.
3. **The rate itself, with its `n`** — `86% (57/66)`, never `86%` on its own.
4. **The `Δ` line and its interval** — the gap between the two arms, and whether it is *outside
   noise*. That word is the decision.

Then the slices, when you want to know where the next batch of cases comes from.

**Any `!!` line, first.** If a metric sits at exactly 0% or exactly 100% in *every* arm, the
verdict says so above every table:

```
!! PAIRING: identically 0% in all arms -- this usually measures the harness, not the model. Check the metric's inputs before reading anything else.
```

Nothing below that line is worth reading until you have checked what feeds the metric. It is the
cheapest bug detector on this page: it catches a broken metric column in minutes, rather than
after a whole sweep has been believed. (A guardrail that is identical and comfortably inside its
budget in every arm is *not* flagged — that is the control doing its job, and warning about it
would only teach you to skip the warning.)

A single arm gets the other half of the same check:

```
!! ACCURACY is perfect on all 22 cases. A saturated eval has stopped discriminating -- it cannot show a regression or an improvement from here. Add harder cases rather than reading this as a result.
```

**Coverage, before any quality rate.** Give a quality metric a `coverage=` companion and the
coverage number is rendered **first**, above it, with a reminder that a quality rate is never
read alone:

```python
ev.Metric('quality', 'good', where='answered',
          coverage=ev.Metric('answered', 'answered'))
```

And when a metric's denominator differs materially between arms — the same trap arriving without
a coverage metric to catch it — you are told:

```
  denominator moved (n=20 vs n=10) -- this rate is not directly comparable across arms; read the coverage metric first.
```

**The `n` beside every rate.** `(57/66)` is the numerator and the denominator, always. A rate
over a filtered subset gets better as the subset shrinks — a prompt that answers half as often
can post a spectacular quality score.

**"outside noise" / "inside noise".** The `Δ` line is the gap between the two arms, followed by
the range that gap could plausibly be. When that range includes zero, the verdict names no
winner at all:

```
VERDICT  prod vs preship = +8pp [+0pp, +33pp], inside noise at 3 reps -- raise repeats
         before calling this real.
```

This is the line that saves you from shipping a coin flip. Real harnesses have measured the
*same* prompt at 69% and then at 64% an hour later; at three runs per case a single flip is
worth 33 points. The lever for a narrower band is `repeats=`.

**Wide intervals on a perfect score.** When every case agrees, `100% (66/66)` still comes back
with a wide interval and a note saying the numbers left nothing to vary. That is not a defect:
22 cases cannot demonstrate 100%, and reporting `[100%, 100%]` would claim a certainty you did
not buy.

**Weakest slices.** Pass `slices=('phrasing', 'state')` — any metadata column from your case
file — and the winning arm is broken down by it, worst first. That is where your next batch of
cases comes from.

### Against a baseline

When one arm is the thing you are comparing *against* -- what ships today -- name it. An arm
called `baseline` is picked up automatically; otherwise pass `baseline='prod'` to `sweep()` or set
`baseline = 'prod'` on the class. Every other arm is then reported as a difference from it:

```
ACCURACY (higher is better)
  baseline   72%  (47/66)   95% CI [58%, 83%]
  live       81%  (53/66)   95% CI [69%, 90%]
  Δ  live − baseline  = +9pp  [-2pp, +20pp]   inside noise

VERDICT  live vs baseline on accuracy: +9pp [-2pp, +20pp], inside noise at 3 reps.
```

No winner is named, on purpose. Real results are rarely one number moving: an intent prompt that
lifted one label from 0% to 73% while another fell from 86% to 61% is *mixed*, and the useful
output is a written judgement -- what moved, what is inside the noise, what you would ship and
why -- not a pass/fail. Treat the bar in your plan as a reference point for that judgement.

### Read the outputs, not only the rate

A rate says how often. It never says what the outputs look like, and that is how a scorer that
rewards the wrong thing survives. Write them out:

```python
r = ev.sweep(...)
r.side_by_side()        # results/<date>-<name>-side-by-side.md
```

For each case where the arms **disagree** on a column the verdict reads -- plus any case that
failed -- it writes the inputs and every arm's output under its own heading, with its scores.
`all=True` includes every case; later repeats appear only where their outcome differs. It reads
the stored outputs, so it calls nothing. The layout is a Jinja template: pass `template=` a path
or a string to change it. Commit the file beside the verdict -- both are small, and both cost money
to produce.

## Never truncate a field your metric reads

This is the first of two rules about **your own code**, and it is worth more than the rest
of this page.

A real harness capped its output model's `message` field at 2000 characters, so that a CSV row
would not be a wall of text. Its metric compared the last line of that field against a generated
list. Long replies were sliced mid-sentence, the metric read the last line of the truncation,
and the run reported failures the model had never committed. The cap had turned the harness into
the thing being measured.

**Emit the full value, and add a separate short column for display.**

```python
class Reply(BaseModel):
    message: str                  # full, always — this is what gets scored
    correct: bool
```

The eval table already does this on the way out: a long text column arrives twice, as `message`
in full and as `message_preview` shortened for reading. Skim the `_preview` column; never let a
shortened value be the one a metric sees.

## Measure the field the way production reads it

The other way a harness quietly becomes the thing being measured, and it is harder to spot than
truncation because nothing about it looks wrong.

A style reviewer returns a quoted passage with every finding, and the eval scored how often that
quote could be located in the document. The shipping client locates the quote too — and it
normalizes whitespace and case before it looks. The eval compared raw strings. Every finding
whose quote differed by a double space scored as a failure the product does not have. Push it
the other way — the eval normalizing more than production does — and the sweep reports a pass
rate the user never gets. Both directions produce a fiction, and neither raises anything.

**When production applies a tolerance, a threshold or a normalisation before it acts on a field,
the metric applies the identical one.** Not an equivalent one: the same function, imported from
the same place.

```python
from myapp.review import locate_quote           # the function the client itself calls

async def run_case(inputs, arm):
    findings = await review(inputs.document, arm=arm)
    located = [f for f in findings if locate_quote(f.quote, inputs.document)]
    return {'n_findings': len(findings), 'n_located': len(located)}
```

When it cannot be imported — it lives in a client, another service, another language — write
down **where that definition lives** before you restate it, and put the pointer in a comment
beside the copy. A restated rule drifts; an undocumented restated rule has already drifted and
nobody can tell.

The same applies to every threshold your product acts on. If it acts at confidence ≥ 0.7, the
metric scores at 0.7 — not at "reasonably confident", and not at the 0.5 that seemed natural
while writing the eval.

## Keeping the baseline runnable

Comparing "before" against "after" only means something if "before" is really what shipped.

**For a baseline, check it out of git.** `ev.git_tree` materializes a path from any ref into a
directory, keeping its layout:

```python
ev.git_tree('v2.1', 'prompts', dest='baseline/v2.1')   # -> baseline/v2.1/prompts/
```

It is byte-exact, it cannot decay as the live files move, and — the reason it exists — it
restores files that were **deleted**. It refuses to produce an empty directory: a baseline that
silently materialized empty would read as *"the old prompt already scored well"*, which is the
worst answer a comparison can hand you. Pass `expect_absent=['prompts/rules.md']` to assert that
a file really is gone at that ref, so a wrong ref fails instead of measuring nothing.

Files keep their layout under `dest`, and are written once and reused. Leave `dest` out and they
land in a gitignored directory named after the resolved commit. Point your own loader at them —
how your templates load is yours to know, and nothing is patched on your behalf:

```python
from pathlib import Path


async def run_turn(inputs, prompt_dir):
    template = (Path(prompt_dir) / 'system.md').read_text()
    return await my_assistant(inputs.question, template=template)


r = ev.sweep(run_turn, cases=cases,
             prompt_dir=['baseline/v2.1/prompts', 'prompts'],   # v2.1 vs live
             metric=ev.Metric('accuracy', 'correct'))
```

Because a resolved commit never changes, the baseline arm's cached result stays valid for good;
the live arm is the only one that ever needs to re-run. If you write the class form, have that
arm's `code_version()` return `ev.git_sha('v2.1')` rather than the ref text — `HEAD~1` names
different content every time you commit, and a cache key that moves under a stable-looking name
is how stale numbers get read as fresh ones.

**For a probe — a rewrite you have not shipped anywhere — use `ev.Variant`.** There is no ref to
check out, so an exact replacement in the rendered text is all there is:

```python
tighter = ev.Variant(old='Be helpful and thorough.',
                     new='Answer in at most three sentences.')
text = tighter.apply(template)
```

It raises when `old` is not present, rather than quietly returning the text unchanged and then
reporting the *unrewritten* prompt's numbers as the variant's.

**Use `git_tree` for baselines and `Variant` only for probes.** A replacement cannot restore a
deleted section, and cannot put one back where it was — which is exactly what a baseline needs
and exactly what a probe does not have.

One more thing to say out loud, because it is the difference between numbers you can trust and
numbers you cannot. `ev.sweep` re-runs an arm when your **function** or your **case file**
changes — the function's own source, not the helpers it calls. A prompt your function *reads*,
or a module it imports, is neither of those, so name it with `watch=`:

```python
r = ev.sweep(run_turn, cases=cases, watch='prompts/*.md',
             prompt_version=['prod', 'preship'],
             metric=ev.Metric('accuracy', 'correct'))
```

Now editing a prompt re-runs the arms that read it, and nothing else. Leave `watch=` off and the
prompt sits outside the cache key entirely — you edit it, re-run, and get last week's numbers
back with no sign that anything is wrong. A pattern that matches no file raises, so a typo'd path
cannot quietly turn into "nothing ever changed".

`watch` takes whatever your arms actually depend on, so the module holding your agent code
belongs there too:

```python
watch=lambda: oryxflow.hash_files('prompts/*.jinja2', 'agent.py', root=HERE)
```

You are not flying blind if you forget one: oryxflow fingerprints the code a task imports, and
tells you when something moved that your cache key did not cover —

```
StalenessWarning: task FollowUps: agent.py changed since cached run;
code_version() still computes fn:e22d2369 -- reusing cached output.
```

That is a warning, not a re-run. `watch=` is what turns it into a re-run.

Patterns are relative to the directory you run from. If the script might be launched from
anywhere, anchor them:

```python
HERE = pathlib.Path(__file__).parent
r = ev.sweep(run_turn, cases=cases,
             watch=lambda: oryxflow.hash_files('prompts/*.md', root=HERE), ...)
```

## When one function is not enough

Reach for the class form when you want a command line, an upstream dependency, several saved
outputs, or a cache that covers the files your function reads:

```python
import oryxflow
import oryxflow.evals as ev
from pydantic_evals import Dataset


class TurnEval(ev.TaskEval):
    """Does the assistant answer this turn correctly?"""

    dataset = Dataset(name='turns',
                      cases=list(ev.load_cases('cases.csv', inputs=Turn)),
                      evaluators=[AnsweredCorrectly()])

    metric = ev.Metric('accuracy', 'AnsweredCorrectly')
    guardrail = ev.Metric('false positives', 'false_positive',
                          higher_is_better=False, budget=0.05)
    slices = ('phrasing',)

    prompt_version = oryxflow.Parameter(default='prod')
    model_id = oryxflow.Parameter(default='small')

    def code_version(self):
        return oryxflow.hash_files('prompts/*.md')     # edit a prompt -> this arm re-runs

    async def case(self, inputs):
        return await my_assistant(inputs.question, prompt=self.prompt_version,
                                  model=self.model_id)


if __name__ == '__main__':
    ev.cli(TurnEval)
```

**Let `case()` raise.** Catching the exception inside it and returning an error field makes the
run record a *successful* case: the failure rate reads as zero, every evaluator scores an error
object as if it were an answer, and filtering them out later becomes your manual job. Raising is
what keeps a broken case out of the rates and lets the automatic retries do their work.

`ev.cli` gives that class a command line whose flags are **derived from its parameters**, so
adding a parameter adds a flag and the two cannot drift apart. Each one repeats, because
repeating a flag is how you declare an axis:

```
python turn_eval.py --prompt-version prod --prompt-version preship --repeats 3
```

Built in on top of your own: `--repeats`, `--concurrency`, `--reset`, `--rescore`, `--check`,
`--csv`, `--yes`.

### Two stages: the model calls, then the scoring

Each arm is cached as two steps, so the expensive one is never repeated for the cheap one:

| stage | stored as | re-runs when |
| --- | --- | --- |
| the model calls | `<Eval>Outputs`, one JSON per arm | `code_version()` changes (what the arm reads), the code `case()` runs changes, or the case set changes |
| the scoring | `<Eval>`, the per-case table | the outputs changed, or `scorer_version()` changes -- by default the evaluators' code and configuration |

So `code_version()` on your class names what the **arm** reads -- prompt files, a git ref, a model
id -- and you never have to fold your scorers into it. Edit an evaluator, change a judge's rubric
or swap its model, and only the scoring re-runs, over the stored outputs:

```
TurnEval · 22 cases × 2 arms × 3 reps = 132 calls
  cached 132 · new 0
  re-scoring 2 arms from stored outputs: no model calls
```

`--reset` discards both stages and calls the model again. `--rescore` re-runs only the scorers --
for when a judge failed transiently and you want those cases judged again without paying for the
outputs twice. Override `scorer_version()` when a scorer reads something that is not its own code
or configuration, such as a rubric file.

The stored outputs are plain JSON, readable without oryxflow: one record per case and repeat, with
the output, its timing, and whatever the case recorded with `increment_eval_metric`.

**Return what your scorers need, including the tool calls.** Outputs are stored; the trace of the
run is not. Evaluators that read the trace -- pydantic-evals' `HasMatchingSpan`, `ToolCorrectness`,
`TrajectoryMatch`, `ArgumentCorrectness`, `MaxToolCalls`, `MaxModelRequests`, or any evaluator with
`needs_trace = True` -- therefore score in the model-call stage, while the trace still exists, and
re-run only with it. If you want to change a tool-call check later for free, have `case()` put the
tool calls it made in its output and score that instead.

## Where credentials come from

**oryxflow does not manage them.** It never reads, stores or configures an API key. Your
`case()` function makes the call, using whatever your provider's SDK already picks up — an
environment variable, a config file, your shell. There is nothing to configure here, and nothing
new that can leak.

What you do get is a one-call answer to *"is this actually wired up?"*, because the alternative
is expensive. Run from the wrong working directory and every case raises, the sweep completes,
and an empty result gets stored as though it were a measurement:

```
python turn_eval.py --check
TurnEval: preflight OK.
```

`--check` runs your `preflight()` — by default one real call, on the first case — and exits. A
failure prints the traceback and exits non-zero. During a sweep the same probe runs once per new
arm before anything else is billed, so a credential problem costs you one call instead of a
matrix. Override `preflight()` to probe something else, or set `preflight = None` to skip it.

## What pydantic-evals already gives you

oryxflow does the matrix, the cache, the intervals and the verdict. Scoring is pydantic-evals,
and most of what an eval needs per case already ships there -- check this list before writing it
yourself:

| you need | use |
| --- | --- |
| a yes/no judgement of fuzzy quality | `LLMJudge(rubric=..., model=...)` -- a different model family from the one under test |
| a scored rubric with explicit steps | `GEval(criteria=..., evaluation_steps=...)` |
| exact match against the expected answer | `EqualsExpected()`; `Equals`, `Contains`, `IsInstance` for fixed values |
| did it call the right tools, with the right arguments | `ToolCorrectness`, `TrajectoryMatch`, `ArgumentCorrectness`, `HasMatchingSpan` |
| a budget on tool calls or model requests | `MaxToolCalls`, `MaxModelRequests` |
| a confusion matrix or precision/recall over a label | `ConfusionMatrixEvaluator`, `PrecisionRecallEvaluator` in the dataset's `report_evaluators` |
| per-case setup and teardown (stubbing a search API, seeding a record) | `CaseLifecycle` |
| a first draft of cases | `generate_dataset` -- then mark them `synthetic` |

See the [pydantic-evals docs](https://pydantic.dev/docs/ai/evals/) for each. Three shapes that need
no new API at all:

- **Model as an axis.** A model id is just another parameter -- `model_id=['small', 'large']`
  -- and belongs in `code_version()`, pinned to a snapshot, so a provider moving the alias
  invalidates the cell instead of being served from it.
- **A conversation, replayed.** One case is one episode: `case()` runs every turn, feeding each
  turn the state the previous one returned, and returns per-turn columns (`turn1_ok`,
  `turn2_ok`, ...) plus an episode-level one. Arms are then free to diverge mid-conversation, which
  is the point.
- **"Zero false negatives" as the rule for a flag.** A closed label set is scored by comparison,
  not by a judge. Return the predicted flag, score it against `expected`, and read the false
  negatives off the confusion matrix -- the numbers to quote are the per-class rates, not one
  accuracy.

## Using a different eval runner

The per-case table is the only thing the metrics, the intervals, the verdict and the report ever
read. Nothing downstream of a run touches pydantic-evals.

So if you want to run your cases some other way, there are exactly two methods to override on
your `TaskEval` subclass:

- **`_evaluate()`** — scores the stored outputs and returns a report. Replace it to use a
  different runner.
- **`_to_frame(report)`** — turns that report into the per-case table. Replace it to accept a
  different report shape.

Everything else keeps working unchanged, because everything else was only ever reading the
table. The same property is why you can build an `ev.EvalResult` from a DataFrame you made
yourself and get the full verdict out of it.

## Read next

- **[LLM eval quickstart](llm-evals-quickstart.md)** — a complete eval in one file, line by line.
- **[Classes and functions](llm-evals-api.md)** — everything `oryxflow.evals` exports, grouped by
  when you meet it.
- **[Prompts as files](llm-evals-prompts.md)** — one file per prompt, git as the version
  store, and how to get a prompt out of a Python string constant.
- **[What an eval plan must contain](llm-evals-checklist.md)** — the six sections a plan needs
  before anyone can run it, and how many cases it takes to resolve the difference you care about.
- **[Scaffold an LLM eval](claude-plugin/evals.md)** — the Claude Code plugin's four eval
  commands, and the files they write.
- **[LLM evals are a parameter sweep](../blog/posts/llm-evals-are-a-parameter-sweep.md)** — why
  an eval matrix is a Cartesian product, and what follows from that.
- **[Cheap, durable LLM evals](../blog/posts/cheap-durable-llm-evals.md)** — the arithmetic that
  makes per-cell caching worth having.
- **[Your eval platform deletes your results in 14 days](../blog/posts/llm-eval-results-retention.md)**
  — keeping scored rows you can still read next year.
- **[Writing and managing tasks](tasks.md)** — the task model `ev.TaskEval` is built on.
- **[Running workflows](run.md)** — resets, invalidation, and what re-runs when.
