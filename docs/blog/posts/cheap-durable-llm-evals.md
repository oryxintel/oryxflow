---
date: 2026-07-25
slug: cheap-durable-llm-evals
categories:
  - Guides
description: An eval matrix is a Cartesian product of cases, scorers, models, prompts and reps — and metered eval platforms bill every cell, every time, then delete the results in 14 to 30 days. Score with pydantic-evals, cache the matrix with oryxflow, and only pay for the cell you changed.
faq:
  - q: "Why does Braintrust get expensive as my evals grow?"
    a: "Because an eval matrix is a Cartesian product and scores are metered per unit. Cases times scorers times models times prompt variants times repetitions multiplies fast: 500 cases with 6 scorers across 4 models, 3 prompts and 3 reps is 108,000 scores per sweep. Run that weekly and Pro's included 50,000 scores per month is gone in the first sweep — at $1.50 per additional 1,000 the monthly bill lands near $822. Nothing pathological caused it; every dimension grew for a good reason and they multiply."
  - q: "How do I stop paying to re-run eval cells that did not change?"
    a: "Cache each cell of the matrix separately, keyed by its parameters. Change one prompt variant of three and only the cells using that variant should recompute — the other two thirds load from disk. oryxflow does this with a task parameterized by model and prompt_version, so a sweep costs you the calls that genuinely changed and nothing else, and the platform bill for the cached cells is zero because they are parquet files in your own data directory."
  - q: "What is the cheapest way to run LLM evals?"
    a: "Split scoring from persistence. Use pydantic-evals for cases, evaluators and reports — it is open source and free — and an ordinary caching workflow engine for the matrix and storage, so re-running a sweep only calls the model for cells whose parameters or code actually changed. Keep an observability platform for live traces and the debugging UI, where the money is genuinely well spent, and stop metering the part that is just a Cartesian product over a labeled dataset."
  - q: "How do I keep per-case eval results after platform retention expires?"
    a: "Save the scored rows yourself as parquet under a version-controlled data directory and put it under Git LFS. Per-case scored rows for a few hundred cases are kilobytes, so they version alongside the code that produced them and stay readable indefinitely. Leave the bulky raw traces in the observability platform where the trace UI lives — that split keeps your repository small and your results permanent."
---

# Cheap, durable LLM evals: pydantic-evals + oryxflow

*An eval matrix is a Cartesian product. Metered platforms bill every cell, every time — then delete the answers.*

<!-- more -->

## The first calculation looks fine

You're evaluating an intent classifier — does this query need a web search or not. 200 labeled cases. You wire up [Braintrust](https://www.braintrust.dev/pricing), run it, and it's free: Starter gives you 1 GB of processed data and 10,000 scores per month, and you used 200.

So you do it properly. A real eval isn't one scorer, it's five: correct label, per-class assertion, output format valid, latency under threshold, and an LLM judge on the borderline reasoning. Now each config costs 200 × 5 = **1,000 scores**.

Then you ask the question you actually care about — is the lite model good enough? Four models. And one run per case is noise, so three reps each:

> 200 cases × 5 scorers × 4 models × 3 reps = **12,000 scores**

That's one afternoon. Your monthly free allowance was 10,000.

The overage is trivial — $2.50 per 1,000 additional scores on Starter, so 2,000 over costs you five dollars. This is the part that feels fine. It stays feeling fine right up until the workload that made you like the tool is the workload that bills you.

## Then the matrix multiplies

Six weeks in, everything has grown the way it's supposed to. Your dataset grew because you seeded it from production traces. Your scorer count grew because you learned what breaks. You're sweeping prompt variants too, because that's the whole point of having evals.

> 500 cases × 6 scorers × 4 models × 3 prompts × 3 reps = **108,000 scores per full sweep**

You run that weekly. 432,000 scores a month. Pro at $249/month includes 50,000 scores, with overage at $1.50 per 1,000:

| | |
|---|---|
| Pro base | $249 |
| 382,000 scores over | $573 |
| **Monthly** | **$822** |

Nothing pathological happened. Nobody made a mistake. Every one of those five dimensions grew for a good reason, and they multiply.

## And there are three meters, not one

Scores are the meter that scales with the *shape* of eval work, but the others ride along. Braintrust bills processed data at $4/GB on Starter and $3/GB on Pro — and agent traces carrying tool calls and retrieved context run hundreds of KB each, one per rep of every cell. If your LLM judge runs through their proxy, token credits meter too.

Seats are free and unlimited, which is genuinely nice and completely beside the point. Your cost driver isn't headcount. It's the Cartesian product.

## The part that should bother you

Here's the arithmetic that actually motivates this post. You changed **one** prompt variant. The other two are byte-identical. All four models are unchanged. All 500 cases are unchanged.

You re-ran and re-paid for all 108,000.

Two thirds of that sweep was recomputation of cells that could not possibly have moved. You paid the platform to meter it, and you paid the model provider to generate it, and the answers were already sitting on your disk from Tuesday.

And then it's deleted — 30-day retention on Pro, 14 on Starter. (Pro does offer extended storage at $0.50/GB/mo, and Enterprise offers custom retention and export, so this is a dial rather than a wall — but it's a dial you pay to turn, on data that is measured in kilobytes.) You spent $822 to learn something, and next month you'll spend it again to remember it.

## Every one of those dimensions is a parameter

Cases, scorers, models, prompt variants, reps. That's a parameter sweep, and data science has had good tooling for parameter sweeps for a decade: declare the grid, cache one output per parameter set, and changing one value reruns exactly the cells that value touches.

Change one prompt variant under that model and 36,000 score-equivalents recompute instead of 108,000 — and the platform bill for them is zero, because they're parquet files in `data/`. You still pay your model provider for the third that genuinely changed. That's the only cost that was ever real.

The rest of this post wires that up in about sixty lines. The division of labor:

- **[pydantic-evals](https://pydantic.dev/docs/ai/evals/)** — scoring. Cases, evaluators, reports. Open source, free.
- **[oryxflow](https://github.com/oryxintel/oryxflow)** — the matrix and persistence. One cached output per parameter set.
- **Logfire** (or your platform of choice) — traces and the debugging UI, which is where observability spend is genuinely well placed.

Nothing overlaps.

## Step 1: the dataset and the scorers

Our running example is the intent classifier: two labels, `needs_web` and `no_web`.

One methodological note first, because it's the thing most eval write-ups get wrong. **This is classification, not a job for LLM-as-judge.** The output is one of two fixed strings; exact match is the correct scorer. Reach for `LLMJudge` when the output is free text with no single right answer — not here, where a judge would add cost, latency and sampling noise to a comparison that was already deterministic.

Second: aggregate accuracy lies. Your errors aren't symmetric. Searching when you didn't need to costs milliseconds. *Not* searching when you needed to means the agent answers a question about this morning from stale training data. If `needs_web` is 30% of traffic and you miss a tenth of it, accuracy reads 97% and recall on the class that matters is 90%.

pydantic-evals covers this without a custom scorer: `ConfusionMatrixEvaluator` and `PrecisionRecallEvaluator` are *report-level* evaluators — they run once over the whole experiment — and you pass them via `report_evaluators`.

```python
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EqualsExpected, ConfusionMatrixEvaluator

CASES = [
    Case(name='weather_now',    inputs='what is the weather in Lisbon right now',
         expected_output='needs_web'),
    Case(name='reverse_list',   inputs='how do I reverse a list in python',
         expected_output='no_web'),
    Case(name='latest_release', inputs='what shipped in the newest postgres release',
         expected_output='needs_web'),
    Case(name='explain_tcp',    inputs='explain the TCP handshake',
         expected_output='no_web'),
]

dataset = Dataset(
    name='needs_web_v3',
    cases=CASES,
    evaluators=[EqualsExpected()],
    report_evaluators=[
        ConfusionMatrixEvaluator(
            predicted_from='output',
            expected_from='expected_output',
            title='needs_web vs no_web',
        ),
    ],
)
```

That's the scoring layer, complete. It knows nothing about caching, matrices, or where results live — which is exactly why it composes.

## Step 2: one cell of the matrix, cached

An oryxflow task is a class with parameters, a `run()`, and a `save()`. The engine keys the cached output on the parameter values, so each `(model, prompt_version)` pair gets its own stored result automatically — no filenames to invent, no collisions.

```python
import pandas as pd
import oryxflow

oryxflow.set_dir('data/')

class EvalRun(oryxflow.tasks.TaskPqPandas):
    model           = oryxflow.Parameter(default='claude-sonnet-5-20260501')
    prompt_version  = oryxflow.Parameter(default='v3')
    dataset_version = oryxflow.Parameter(default='needs_web_v3')

    def run(self):
        classify = build_classifier(self.model, self.prompt_version)
        report = dataset.evaluate_sync(
            classify, name=f'{self.model}/{self.prompt_version}')

        rows = pd.DataFrame([{
            'case': c.name,
            'query': c.inputs,
            'expected': c.expected_output,
            'predicted': c.output,
        } for c in report.cases])
        rows['correct'] = rows['expected'] == rows['predicted']

        self.save(rows)                       # one row per case, parquet
        self.saveMeta({
            'accuracy': float(rows['correct'].mean()),
            'recall_needs_web': float(
                rows.loc[rows['expected'] == 'needs_web', 'correct'].mean()),
            'n_failures': len(report.failures),
        })
        report.print()                        # confusion matrix, in your terminal
```

Note what gets persisted: **per-case rows**, not a summary. A stored accuracy number can't answer a question you didn't think to ask in July. Stored per-case rows can — you can slice by class, by case, by query type, months later, without re-running anything.

## Step 3: the matrix

A `requires()` that returns a dict fans out over the grid. `inputLoadConcat()` stacks the dependencies' outputs into one DataFrame, tagging each with the parameters of the task it came from — so `model` and `prompt_version` arrive as columns for free:

```python
MODELS = ['claude-haiku-4-5', 'claude-sonnet-5-20260501', 'claude-opus-5']
PROMPTS = ['v2', 'v3']

class EvalMatrix(oryxflow.tasks.TaskPqPandas):
    def requires(self):
        return {f'{m}|{p}': EvalRun(model=m, prompt_version=p)
                for m in MODELS for p in PROMPTS}

    def run(self):
        self.save(self.inputLoadConcat())
```

Run it and ask your questions:

```python
flow = oryxflow.Workflow(EvalMatrix)
flow.run()
df = flow.outputLoad()

df.groupby(['model', 'prompt_version'])['correct'].mean()          # the headline
df[df.expected == 'needs_web'].groupby('model')['correct'].mean()  # what matters
```

Now edit `PROMPTS` to add `'v4'`. Three new cells run. The six existing ones load from disk in milliseconds and cost nothing — not in API calls, not in metered scores. That's the entire argument of this post, and it fits in one line of diff.

## Step 4: provenance, which you get without asking

Most eval setups rot for a reason unrelated to retention: a score of 0.91 with no record of which prompt, which model string, which dataset revision is worthless in three months. You'll find the number and be unable to defend it.

oryxflow writes an event stream as it runs — plain JSONL you can `jq`. Each execution records parameters, code version, a fingerprint of the task's source, upstream output hashes, the git SHA, duration, and the *reason* it ran:

```python
oryxflow.events.runs(task_family='EvalRun', last=2)   # diff params, code, hashes
```

The reason field is the underrated one. Edit your scoring logic and affected tasks recompute automatically, with a reason naming the changed symbol — `code change (auto: evals/run.py::EvalRun)`. You can't accidentally compare a score computed under the old scorer against one computed under the new one, because the old entry is no longer a valid cache hit.

## Step 5: share it

Put `data/` under Git LFS — `/oryxflow:init-gitlfs` with the Claude Code plugin, or `git lfs track "data/**"` by hand. Results now version alongside the code that produced them, and a teammate who clones gets the numbers without re-running anything.

Two things people get wrong:

- **Split blessed baselines from scratch sweeps with `env=`.** `oryxflow.Workflow(EvalMatrix, env='baseline')` writes under `data/env=baseline/`; Tuesday's throwaway grid goes to `env='dev'` and never enters LFS. Skip this and LFS bloats with abandoned experiments.
- **Commit the scored rows, not the traces.** Per-case parquet is kilobytes; raw spans are megabytes and belong in Logfire, where the UI for reading them lives. GitHub's free LFS quota is 1 GB with bandwidth metered separately.

## What this doesn't solve

Four caveats, each with the fix.

**1. The model endpoint is a cache blind spot.** oryxflow hashes your code and inputs. It cannot see a provider updating weights behind a stable alias, so pointing at `claude-sonnet-5` means your cache will serve July's numbers as current in December, confidently.

*Fix:* pin the snapshot in the parameter — `claude-sonnet-5-20260501`, not the alias. The identity of what you measured becomes part of the cache key, and a new snapshot is a new value that runs fresh while the old numbers stay readable. To deliberately re-measure the *same* endpoint (checking for drift), invalidate just that family:

```python
flow.reset_upstream(EvalMatrix, only=EvalRun)   # every cell reruns, nothing else does
```

**2. You're caching a stochastic process.** oryxflow guarantees a result came from the code and inputs it recorded. It does not guarantee a rerun reproduces it. A cached run is a faithful record of *one sampling*.

*Fix:* set `temperature=0` where supported and make it a parameter so the record shows it. Then measure the residual variance rather than assuming it away — which is caveat 4.

**3. Latency needs care.** pydantic-evals runs cases concurrently by default. Time that and you're measuring your own queueing.

*Fix:* a separate task with `max_concurrency=1`, discard the first call, report p50/p95 rather than the mean:

```python
class LatencyRun(oryxflow.tasks.TaskPqPandas):
    model = oryxflow.Parameter(default='claude-sonnet-5-20260501')
    n_run = oryxflow.IntParameter(default=1)          # rep index

    def run(self):
        timings = []
        classify = timed(build_classifier(self.model, 'v3'), timings)
        dataset.evaluate_sync(classify, max_concurrency=1, progress=False)
        s = pd.Series(timings[1:])                    # drop warm-up
        self.save(pd.DataFrame({'latency_s': s}))
        self.saveMeta({'p50': float(s.quantile(.5)), 'p95': float(s.quantile(.95))})
```

**4. Reps.** Decide whether the rep index is a parameter or lives inside pydantic-evals:

- **Rep as a parameter** (`n_run` above) — each rep caches separately, so going from three reps to five runs exactly two new evaluations. Bigger DAG.
- **`repeat=` inside `evaluate_sync`** — one cached unit covers all reps. Smaller DAG, but changing the count re-runs everything.

When reps are expensive, take the parameter and fan out with `WorkflowMulti`:

```python
flow = oryxflow.WorkflowMulti(LatencyRun, {f'rep{i}': {'n_run': i} for i in range(1, 4)})
flow.run()
df = flow.outputLoadConcat()              # every rep, tagged, in one DataFrame

df.groupby('model')['latency_s'].quantile([.5, .95])
```

Need five reps instead of three? Extend the dict. Reps 1–3 load from cache; only 4 and 5 hit the API.

## Takeaway

The bill wasn't caused by anything you did wrong. It was caused by a Cartesian product meeting a per-unit meter, and by an architecture where the thing you re-pay for is *recomputation of cells that could not have changed*.

Score with pydantic-evals, because it's a well-designed open-source scoring library. Debug in Logfire, because a good trace UI is worth paying for. But own the matrix — cache each cell by its parameters, keep the scored rows in your repo, and let a changed prompt variant cost you the third of the grid that actually moved.

```bash
pip install oryxflow pydantic-evals
```

## Frequently asked questions

### Why does Braintrust get expensive as my evals grow?

Because an eval matrix is a Cartesian product and scores are metered per unit. Cases times scorers times models times prompt variants times repetitions multiplies fast: 500 cases with 6 scorers across 4 models, 3 prompts and 3 reps is 108,000 scores per sweep. Run that weekly and Pro's included 50,000 scores per month is gone in the first sweep — at $1.50 per additional 1,000 the monthly bill lands near $822. Nothing pathological caused it; every dimension grew for a good reason and they multiply.

### How do I stop paying to re-run eval cells that did not change?

Cache each cell of the matrix separately, keyed by its parameters. Change one prompt variant of three and only the cells using that variant should recompute — the other two thirds load from disk. oryxflow does this with a task parameterized by `model` and `prompt_version`, so a sweep costs you the calls that genuinely changed and nothing else, and the platform bill for the cached cells is zero because they are parquet files in your own data directory.

### What is the cheapest way to run LLM evals?

Split scoring from persistence. Use pydantic-evals for cases, evaluators and reports — it is open source and free — and an ordinary caching workflow engine for the matrix and storage, so re-running a sweep only calls the model for cells whose parameters or code actually changed. Keep an observability platform for live traces and the debugging UI, where the money is genuinely well spent, and stop metering the part that is just a Cartesian product over a labeled dataset.

### How do I keep per-case eval results after platform retention expires?

Save the scored rows yourself as parquet under a version-controlled data directory and put it under Git LFS. Per-case scored rows for a few hundred cases are kilobytes, so they version alongside the code that produced them and stay readable indefinitely. Leave the bulky raw traces in the observability platform where the trace UI lives — that split keeps your repository small and your results permanent.

Then keep going:

- Sibling post: [Your eval platform deletes your results in 14 days](llm-eval-results-retention.md) — the retention argument in full.
- Sibling post: [LLM evals are a parameter sweep](llm-evals-are-a-parameter-sweep.md) — why the tooling already exists.
- [Parameter sweeps without rerunning upstream steps](parameter-sweeps-without-rerunning.md)
- [Managing complex workflows](../../docs/managing-workflows.md) — the event stream and reset scopes.
- [Collaborate and share results](../../docs/collaborate.md) — Git LFS and `env=`.
- [oryxflow on GitHub](https://github.com/oryxintel/oryxflow)
