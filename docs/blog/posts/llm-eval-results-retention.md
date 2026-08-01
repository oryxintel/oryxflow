---
date: 2026-07-25
slug: llm-eval-results-deleted-retention
categories:
  - AI agents
description: LLM eval platforms delete your results after 14–30 days. But an LLM eval is just a labeled dataset run under N configs — a parameter sweep. Keep the scored rows locally with pydantic-evals and oryxflow, and pay only for the cell you added.
faq:
  - q: "How long do LLM eval platforms keep my results?"
    a: "Not long, by design. As of July 2026 Logfire retains 30 days on its free and Team plans and up to 90 days on Growth; Braintrust retains 14 days on its free Starter plan and 30 days on Pro at $249/month. Longer windows are Enterprise-priced. These are working sets, not archives — if you want a July benchmark still readable in December, you have to keep a copy yourself."
  - q: "How do I keep LLM eval results permanently without paying for longer retention?"
    a: "Save the scored per-case rows to your own repo and treat the eval matrix as a cached parameter sweep. Score with pydantic-evals, then wrap each (model, prompt) run in an oryxflow task so the result is cached on disk keyed by its parameters, versioned with Git LFS, and still readable years later. The scored rows are kilobytes; leave the bulky raw traces in your observability platform where the debugging UI lives."
  - q: "Should I use LLM-as-judge to evaluate an intent classifier?"
    a: "No. When the output is one of a fixed set of labels, exact match against the expected label is the correct scorer — an LLM judge adds cost, latency and noise to a comparison that is already deterministic. Use EqualsExpected for the per-case score and a ConfusionMatrixEvaluator report evaluator for the class breakdown, because aggregate accuracy hides the error that actually costs you: the false negative that stops the agent from searching."
  - q: "How do I avoid re-running my whole eval matrix when I add one model?"
    a: "Make each cell of the matrix a separately cached unit keyed by its parameters. In oryxflow an EvalRun task parameterized by model and prompt_version caches one output per combination, so adding a fifth model to a four-model grid runs only the fifth model's cases — the other four load from disk. The saving is real money, because with per-score metered billing re-running the matrix re-meters every cell you already paid for."
---

# Your eval platform deletes your results in 14 days

*LLM evals are a parameter sweep. Use a parameter sweep tool.*

<!-- more -->

You benchmarked four models in July. In August the numbers are gone.

Nothing broke. No one deleted anything. Retention expired.

Here's the landscape as of July 2026:

| Platform | Free tier | Paid |
|---|---|---|
| [Logfire](https://pydantic.dev/pricing) | 30 days | up to 90 days (Growth); custom on Enterprise |
| [Braintrust](https://www.braintrust.dev/pricing) | 14 days (Starter) | 30 days (Pro, $249/mo); custom on Enterprise |

This isn't a criticism of either product. It's a statement about what they are: **eval platforms are working sets, not archives.** They're optimized for the thing you're debugging *this week* — live traces, a UI to click through failures, diffs against yesterday's run. That's a genuinely hard problem and both solve it well.

But the thing that expires is *tiny*. A few hundred cases across a few dozen experiments, stored as scored rows, is kilobytes. There is no technical reason to lose it. It expires because storage duration is a pricing lever, which is a perfectly reasonable business decision and a terrible fit for the question "did the July prompt actually beat the June one?"

Two details make the point sharper. Braintrust's own docs say cloud-storage export is ["only available on the Enterprise plan"](https://www.braintrust.dev/docs/admin/automations/data-management) — so getting your data out is itself a tier. And Pydantic's Logfire docs, to their credit, [tell you the answer outright](https://pydantic.dev/docs/logfire/guides/otel-collector/s3-backup/): if you need longer than 30 days, write to both Logfire and long-term storage such as S3. The vendor agrees with the premise. The only question is what "long-term storage" should look like for eval results specifically.

## The reframe

Three questions teams actually ask about their LLM systems:

- Is my classifier right?
- Did my prompt change help?
- Is the cheap model good enough?

Underneath, these are one shape: **a labeled dataset, run under N configurations, compared.**

That's a parameter sweep. Data science has had good tooling for parameter sweeps for a decade — cache one output per parameter set, don't recompute what hasn't changed, keep the results next to the code that made them. The LLM eval space rebuilt that as SaaS, with a metered billing model attached to the scoring step.

You don't have to pick a side. Split the job by what each layer is actually good at:

- **[pydantic-evals](https://pydantic.dev/docs/ai/evals/)** — scoring. Cases, evaluators, reports.
- **[oryxflow](https://github.com/oryxintel/oryxflow)** — the matrix, the persistence, and the provenance. One cached output per parameter set, tied to the code and parameters that produced it.
- **Logfire** (or your platform of choice) — traces and the debugging UI.

Nothing overlaps. That's the whole idea. pydantic-evals doesn't want to be a cache; oryxflow doesn't want to be a scorer; neither wants to render a waterfall of spans.

## The running example: does this query need web search?

Every agent has one of these. A router decides whether an incoming query needs a live web search or can be answered from the model's own knowledge. Two labels: `needs_web`, `no_web`.

Make the methodological call before writing any code, because this is where most eval posts go wrong: **this is classification, not a job for LLM-as-judge.** The output is one of two fixed strings. Exact match is the correct scorer. An LLM judge here adds cost, latency, and sampling noise to a comparison that was already deterministic.

Then the trap. Aggregate accuracy lies to you.

Your two error types are not symmetric. A false positive — searching when you didn't need to — costs you a few hundred milliseconds and a fraction of a cent. A false negative — *not* searching when you needed to — means the agent answers a question about this morning's news from training data, confidently and wrongly. That's the failure users screenshot.

If `needs_web` is 30% of your traffic and you miss a tenth of it, that's 3% of cases. Your accuracy reads 97%. The number that matters — recall on `needs_web` — is 90%, and it's invisible in the headline.

The good news: you don't have to write a custom scorer for this. pydantic-evals ships `ConfusionMatrixEvaluator` and `PrecisionRecallEvaluator` as *report-level* evaluators — they run once over the whole experiment rather than per case — and you pass them via `report_evaluators` on the `Dataset`.

```python
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EqualsExpected, ConfusionMatrixEvaluator

CASES = [
    Case(name='weather_now',   inputs='what is the weather in Lisbon right now',
         expected_output='needs_web'),
    Case(name='reverse_list',  inputs='how do I reverse a list in python',
         expected_output='no_web'),
    Case(name='latest_release', inputs='what shipped in the newest postgres release',
         expected_output='needs_web'),
    Case(name='explain_tcp',   inputs='explain the TCP handshake',
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

That's the scoring layer, complete. It knows nothing about models, caching, or where results live.

## Wiring the matrix

Now the sweep. One task, parameterized by the things you vary, saving one row per case:

```python
import pandas as pd
import oryxflow

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

        self.save(rows)
        self.saveMeta({
            'accuracy': float(rows['correct'].mean()),
            'recall_needs_web': float(
                rows.loc[rows['expected'] == 'needs_web', 'correct'].mean()),
            'n_failures': len(report.failures),
        })
        self.logger.info('acc={} recall_needs_web={}',
                         rows['correct'].mean(),
                         rows.loc[rows['expected'] == 'needs_web', 'correct'].mean())
        report.print()          # confusion matrix, in your terminal
```

Then the matrix itself. `requires()` returning a dict fans out over every combination, and `inputLoadConcat()` stacks the results:

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

`inputLoadConcat()` tags each dependency's rows with that task's parameters, so `model` and `prompt_version` come back as **columns** — no manual bookkeeping, no filename parsing:

```python
flow = oryxflow.Workflow(EvalMatrix)
flow.run()
df = flow.outputLoad()

df.groupby(['model', 'prompt_version'])['correct'].mean()          # the headline
df[df.expected == 'needs_web'].groupby('model')['correct'].mean()  # the number that matters
```

### The payoff

Add a fourth model to the grid and **only the fourth model runs.** The six existing cells load from disk in milliseconds; the two new ones call the API.

For ordinary data work that's convenience — you saved a few minutes of CPU. For LLM evals it's *money*, and the arithmetic is worth doing explicitly. Take a 300-case set, three models, two prompts: six cells, 1,800 API calls, and however many scores your platform meters. Adding a model without caching means re-running all 2,400 calls. With caching it's 600. You pay for a quarter of the work because three quarters of it hasn't changed.

Contrast that with per-score metered billing, where re-running the matrix re-meters every cell — including the five you already paid for last week and whose results are byte-identical.

## The provenance you get without asking

Most eval setups rot for a reason that has nothing to do with retention: a score of 0.91 with no record of *which* prompt, *which* model string, *which* dataset revision is worthless in three months. You'll find the number in a spreadsheet and be unable to defend it.

oryxflow writes an event stream as you go — plain JSONL you can `jq`. Each task execution records its parameters, code version, a fingerprint of the task's source, upstream output hashes, the git SHA, the duration, and the *reason* it ran:

```python
oryxflow.events.runs(task_family='EvalRun', last=2)   # diff params, code, hashes
```

That last field is the one people underrate. Edit your scoring logic and the affected tasks recompute automatically, with a reason naming the changed symbol — `code change (auto: evals/run.py::EvalRun)`. You can't accidentally compare a v2 score computed under the old scorer against a v3 score computed under the new one, because the old one no longer exists as a valid cache entry.

## Sharing it

Put `data/` under Git LFS. The Claude Code plugin does it in one step with `/oryxflow:init-gitlfs`; by hand it's `git lfs track "data/**"`. Now your eval results version alongside the code that produced them, and a teammate who clones the repo gets the numbers without re-running anything.

Two things people get wrong:

- **Use `env=` to separate blessed baselines from scratch sweeps.** `oryxflow.Workflow(EvalMatrix, env='baseline')` writes under `data/env=baseline/`; your ad-hoc Tuesday-afternoon grid goes to `env='dev'` and never enters LFS. Skip this and LFS bloats with abandoned experiments.
- **Commit the scored rows, not the traces.** Per-case parquet is kilobytes. Raw spans are megabytes and belong in Logfire, where the UI for reading them lives. GitHub's free LFS quota is 1 GB with bandwidth metered separately — easy to stay inside if you're storing scores, easy to blow through if you're storing traces.

## The honest section

Four things this setup does not solve, and what to do about each.

**1. The model endpoint is a cache blind spot.** oryxflow hashes your code and your inputs. It cannot see that a provider updated the weights behind a stable alias. Point `EvalRun` at `claude-sonnet-5` and your cache will confidently serve July's numbers as current in December.

*Fix:* put the pinned snapshot id in the parameter — `claude-sonnet-5-20260501`, not the alias. Now the identity of the thing you measured is part of the cache key, and moving to a new snapshot is a new parameter value that runs fresh while the old numbers stay readable. When you deliberately want to re-measure the *same* endpoint — checking for drift, say — invalidate just that family:

```python
flow.reset_upstream(EvalMatrix, only=EvalRun)   # every cell reruns; nothing else does
```

**2. You're caching a stochastic process.** oryxflow guarantees a result came from the code and inputs it recorded. It does not guarantee that re-running produces the same numbers. A cached run is a faithful record of *one sampling*, not a reproducible constant.

*Fix:* set `temperature=0` where the provider supports it and make it a parameter so the record shows it. Accept that the remaining variance is real, and if it's large enough to change your decision, measure it — which is caveat 4.

**3. Latency needs care.** pydantic-evals runs cases concurrently by default. Measure wall-clock under that and you're measuring your own queueing, not the model.

*Fix:* a dedicated task with `max_concurrency=1`, discard the first call as warm-up, and report p50/p95 rather than the mean — one slow tail call wrecks a mean and tells you nothing about typical experience.

```python
class LatencyRun(oryxflow.tasks.TaskPqPandas):
    model = oryxflow.Parameter(default='claude-sonnet-5-20260501')
    n_run = oryxflow.IntParameter(default=1)      # rep index

    def run(self):
        timings = []
        classify = timed(build_classifier(self.model, 'v3'), timings)
        dataset.evaluate_sync(classify, max_concurrency=1, progress=False)
        s = pd.Series(timings[1:])                # drop warm-up
        self.save(pd.DataFrame({'latency_s': s}))
        self.saveMeta({'p50': float(s.quantile(.5)), 'p95': float(s.quantile(.95))})
```

**4. Repetitions.** You have to decide whether the rep index is a *parameter* or lives inside pydantic-evals. Both are defensible:

- **Rep as a parameter** (`n_run` above) — each repetition caches separately, so adding reps 4 and 5 to a three-rep study runs exactly two new evaluations. The DAG gets bigger.
- **`repeat=` inside `evaluate_sync`** — one cached unit covers all reps, smaller DAG, but changing the rep count re-runs everything.

Prefer the parameter when reps are expensive, and fan out with `WorkflowMulti`:

```python
flow = oryxflow.WorkflowMulti(LatencyRun, {f'rep{i}': {'n_run': i} for i in range(1, 4)})
flow.run()
df = flow.outputLoadConcat()          # every rep, tagged, in one DataFrame

df.groupby('model')['latency_s'].quantile([.5, .95])
```

Decide you need five reps instead of three? Extend the dict. Reps 1–3 load from cache; only 4 and 5 hit the API.

## Takeaway

An LLM eval is a labeled dataset, run under N configurations, compared. That's a parameter sweep, and parameter sweeps have had good tooling for a decade.

Score with pydantic-evals, because it's a well-designed scoring library. Debug in Logfire, because a trace UI is genuinely worth paying for. But keep the scored rows yourself, cached by parameter set, versioned next to the code that made them — because they're kilobytes, they're the actual output of your work, and no retention policy should be able to decide when you stop being able to answer "did that change help?"

```bash
pip install oryxflow pydantic-evals
```

## Frequently asked questions

### How long do LLM eval platforms keep my results?

Not long, by design. As of July 2026 Logfire retains 30 days on its free and Team plans and up to 90 days on Growth; Braintrust retains 14 days on its free Starter plan and 30 days on Pro at $249/month. Longer windows are Enterprise-priced. These are working sets, not archives — if you want a July benchmark still readable in December, you have to keep a copy yourself.

### How do I keep LLM eval results permanently without paying for longer retention?

Save the scored per-case rows to your own repo and treat the eval matrix as a cached parameter sweep. Score with pydantic-evals, then wrap each (model, prompt) run in an oryxflow task so the result is cached on disk keyed by its parameters, versioned with Git LFS, and still readable years later. The scored rows are kilobytes; leave the bulky raw traces in your observability platform where the debugging UI lives.

### Should I use LLM-as-judge to evaluate an intent classifier?

No. When the output is one of a fixed set of labels, exact match against the expected label is the correct scorer — an LLM judge adds cost, latency and noise to a comparison that is already deterministic. Use `EqualsExpected` for the per-case score and a `ConfusionMatrixEvaluator` report evaluator for the class breakdown, because aggregate accuracy hides the error that actually costs you: the false negative that stops the agent from searching.

### How do I avoid re-running my whole eval matrix when I add one model?

Make each cell of the matrix a separately cached unit keyed by its parameters. In oryxflow an `EvalRun` task parameterized by `model` and `prompt_version` caches one output per combination, so adding a fifth model to a four-model grid runs only the fifth model's cases — the other four load from disk. The saving is real money, because with per-score metered billing re-running the matrix re-meters every cell you already paid for.

Then keep going:

- Sibling post: [LLM evals are a parameter sweep](llm-evals-are-a-parameter-sweep.md) — the same argument from the tooling side.
- Sibling post: [Cheap, durable LLM evals: pydantic-evals + oryxflow](cheap-durable-llm-evals.md) — the step-by-step build.
- [Parameter sweeps without rerunning upstream steps](parameter-sweeps-without-rerunning.md)
- [Managing complex workflows](../../docs/managing-workflows.md) — the event stream and reset scopes.
- [Collaborate and share results](../../docs/collaborate.md) — Git LFS and `env=`.
- [oryxflow on GitHub](https://github.com/oryxintel/oryxflow)
