---
date: 2026-07-25
slug: llm-evals-are-a-parameter-sweep
categories:
  - AI agents
description: Every LLM eval question reduces to one shape — a labeled dataset, run under N configurations, compared. That's a parameter sweep, and it already has good tooling. Use pydantic-evals for scoring and a caching DAG for the matrix.
faq:
  - q: "Are LLM evals just a parameter sweep?"
    a: "Structurally, yes. Is my classifier right, did my prompt change help, and is the cheap model good enough are all the same shape underneath: a labeled dataset, run under N configurations, compared. Cases, scorers, models, prompt variants and repetitions are the axes of a grid, which makes an eval matrix a Cartesian product over parameters — exactly what parameter-sweep tooling has handled well for a decade. What is genuinely new is the scoring, and pydantic-evals covers that."
  - q: "Do I need an LLM eval platform, or can I use a normal workflow tool?"
    a: "You need both, for different jobs. A platform earns its cost for live traces and a UI to click through failures, which is a real problem well solved. The matrix and the persistence are not LLM-specific — one cached output per parameter set is ordinary data-science plumbing, and doing it with a caching workflow engine means adding a model to your grid runs only that model instead of re-metering cells whose results already exist on your disk."
  - q: "How do I make sure my model comparison is apples-to-apples?"
    a: "Share the upstream. If the dataset build is its own cached task that every configuration depends on, all models are scored against byte-for-byte identical cases — not because you were careful, but because there is only one dataset output to read. That removes the quiet failure where you regenerated the case set halfway through a sweep and half your comparison is against a different denominator."
  - q: "Should the repetition index be a task parameter in an LLM eval?"
    a: "Make it a parameter when repetitions are expensive. Each rep then caches separately, so extending a three-rep study to five runs exactly two new evaluations instead of all five. The trade-off is a larger DAG. If repetitions are cheap, use pydantic-evals' repeat argument instead — one cached unit covers every rep, giving a smaller graph but a coarser cache, so changing the count re-runs the whole thing."
---

# LLM evals are a parameter sweep — use a parameter sweep tool

*The scoring is genuinely new. The matrix underneath it is a solved problem from 2015.*

<!-- more -->

Three questions teams actually ask about their LLM systems:

- Is my classifier right?
- Did my prompt change help?
- Is the cheap model good enough?

They feel like three different projects. They're one shape: **a labeled dataset, run under N configurations, compared.**

Write that out as a grid — cases × scorers × models × prompt variants × repetitions — and you have described a Cartesian product over parameters. A parameter sweep. Data science has had good tooling for parameter sweeps for a decade, built around one idea: cache one output per parameter set, and when a value changes, recompute exactly the cells that value touches.

The LLM eval space rebuilt that layer as SaaS, with a meter on it.

Some of what those platforms sell is genuinely new and genuinely worth paying for. Traces of an agent's tool calls, a UI for clicking through failures, span-level debugging — that's a hard problem, well solved, and not something you want to rebuild. But the *matrix* isn't LLM-specific, and neither is the persistence. Those are the parts where treating evals as a normal data-science sweep pays off immediately.

## The division of labor

- **[pydantic-evals](https://pydantic.dev/docs/ai/evals/)** — scoring. Cases, evaluators, reports. This is the new part.
- **[oryxflow](https://github.com/oryxintel/oryxflow)** — the matrix and persistence. One cached output per parameter set.
- **Logfire** (or your platform of choice) — traces and the debugging UI.

Nothing overlaps, which is the whole point. Each layer does one job and none of them needs to know about the others.

## The running example

Every agent has an intent classifier somewhere. Ours decides whether an incoming query needs a live web search: two labels, `needs_web` and `no_web`.

Two methodological points before code, because they're where evals usually go wrong.

**This is classification, not a job for LLM-as-judge.** The output is one of two fixed strings. Exact match is the correct scorer. `LLMJudge` exists for free-text outputs with no single right answer — using it here would add cost, latency and sampling noise to a comparison that was already deterministic.

**Aggregate accuracy lies.** Your two errors aren't symmetric. A false positive — searching unnecessarily — costs a few hundred milliseconds. A false negative — not searching when you should have — means the agent answers a question about this morning's news from training data, confidently. That's the failure users screenshot. If `needs_web` is 30% of traffic and you miss a tenth of it, headline accuracy reads 97% while recall on the class that actually matters is 90%.

pydantic-evals handles this without a custom scorer. `ConfusionMatrixEvaluator` and `PrecisionRecallEvaluator` are *report-level* evaluators — they run once over the whole experiment rather than per case — and you pass them via `report_evaluators` on the `Dataset`:

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

## The sweep

Everything you vary becomes a task parameter. oryxflow keys the cached output on those values, so each combination gets its own stored result — no filenames to invent, no `results_sonnet_v3_final2.json` graveyard.

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

        self.save(rows)
        self.saveMeta({
            'accuracy': float(rows['correct'].mean()),
            'recall_needs_web': float(
                rows.loc[rows['expected'] == 'needs_web', 'correct'].mean()),
        })
        report.print()
```

Save **per-case rows**, not a summary. A stored accuracy number can only answer the question you thought of at the time. Per-case rows let you slice by class, by case, by query type, six months later, without re-running a thing.

The grid is a `requires()` that returns a dict. `inputLoadConcat()` row-stacks the dependencies and tags each with the parameters of the task it came from, so `model` and `prompt_version` arrive as columns:

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

```python
flow = oryxflow.Workflow(EvalMatrix)
flow.run()
df = flow.outputLoad()

df.groupby(['model', 'prompt_version'])['correct'].mean()          # the headline
df[df.expected == 'needs_web'].groupby('model')['correct'].mean()  # what matters
```

## Two properties you inherit for free

Here's why this framing pays, beyond tidiness.

**Marginal cost.** Add a fourth model and only the fourth model runs. The six existing cells load from disk in milliseconds. For ordinary data work that's convenience — you saved some CPU. For LLM evals it's money, and it compounds with every axis: a 300-case set across three models and two prompts is 1,800 calls, and adding a model without caching means re-running all 2,400 instead of the 600 that are new. Under per-score metered billing you re-meter the cached cells too, so you pay twice for the privilege of not learning anything new.

**Trustworthy comparison.** If the dataset build is itself a cached upstream task, every configuration reads the *same* output — not because you were careful, but because there is only one output to read. That kills the quiet failure mode where you regenerated the case set midway through a sweep and half your comparison is against a different denominator. The comparison is apples-to-apples by construction.

That second property is the one people underrate. It's the same reason [parameter sweeps over shared features](parameter-sweeps-without-rerunning.md) are trustworthy in ordinary ML work: the shared upstream is computed once, so there's nothing to accidentally desynchronize.

## What ran, and why

A score of 0.91 with no record of which prompt, which model string, which dataset revision is worthless in three months. You'll find it and be unable to defend it — which is how most eval setups actually die, long before retention expires.

oryxflow writes an event stream as it runs: plain JSONL you can `jq`. Every execution records parameters, code version, a fingerprint of the task's source, upstream hashes, git SHA, duration, and the *reason* it ran.

```python
oryxflow.events.runs(task_family='EvalRun', last=2)   # diff params, code, hashes
```

Edit your scoring logic and the affected tasks recompute automatically, with a reason naming the changed symbol — `code change (auto: evals/run.py::EvalRun)`. You can't compare an old-scorer score against a new-scorer score by accident, because the old one stops being a valid cache entry the moment the code changes.

And it's durable in the boring sense: `data/` under Git LFS versions your results alongside the code that made them. Use `env=` to keep blessed baselines (`env='baseline'`) apart from Tuesday's throwaway grid (`env='dev'`), commit the scored parquet, and leave the megabyte traces in Logfire where the trace UI lives. Eval platform retention windows run [14 to 30 days on the common plans](llm-eval-results-retention.md); a git repo runs as long as the repo does.

## Where the analogy breaks

A sweep over hyperparameters and a sweep over models aren't identical, and four differences matter.

**1. The endpoint is outside the cache.** oryxflow hashes your code and inputs. It cannot see a provider updating weights behind a stable alias — so `claude-sonnet-5` as a parameter value means your cache will serve July's numbers as current in December.

*Fix:* pin the snapshot — `claude-sonnet-5-20260501`. The identity of what you measured is now part of the cache key, and a new snapshot is a new value that runs fresh while old numbers stay readable. To deliberately re-measure the same endpoint and check for drift, invalidate that family alone:

```python
flow.reset_upstream(EvalMatrix, only=EvalRun)   # every cell reruns; dataset build doesn't
```

**2. The function isn't deterministic.** In a hyperparameter sweep, re-running a cell reproduces it. Here it doesn't. oryxflow guarantees a result came from the code and inputs it recorded — it is a faithful record of *one sampling*, not a reproducible constant.

*Fix:* `temperature=0` where supported, as a parameter so the record shows it. Then measure the residual variance instead of hoping about it — see point 4.

**3. Latency measurement fights the harness.** pydantic-evals runs cases concurrently by default, so wall-clock under a sweep measures your own queueing.

*Fix:* a separate task with `max_concurrency=1`, discard the first call, report p50/p95 rather than the mean — one tail call destroys a mean and tells you nothing about typical experience.

**4. Repetitions are a design decision, not a detail.** Either the rep index is a parameter or it lives inside pydantic-evals:

- **Parameter** — each rep caches separately, so extending a three-rep study to five runs exactly two new evaluations. Bigger DAG.
- **`repeat=` in `evaluate_sync`** — one cached unit covers all reps. Smaller DAG, coarser cache; changing the count re-runs everything.

When reps are expensive, take the parameter and let `WorkflowMulti` fan out:

```python
class LatencyRun(oryxflow.tasks.TaskPqPandas):
    model = oryxflow.Parameter(default='claude-sonnet-5-20260501')
    n_run = oryxflow.IntParameter(default=1)          # rep index
    ...

flow = oryxflow.WorkflowMulti(LatencyRun, {f'rep{i}': {'n_run': i} for i in range(1, 4)})
flow.run()
df = flow.outputLoadConcat()          # every rep, tagged, one DataFrame
```

Decide you need five reps? Extend the dict — reps 1–3 come from cache, only 4 and 5 call the API. That's the same marginal-cost property, applied to the axis most likely to grow after you've already run the study.

## Takeaway

Score with pydantic-evals, because scoring LLM output well is a real problem and it solves it. Debug in Logfire, because trace UIs are worth paying for. But recognize the middle layer for what it is: a Cartesian product over parameters, with one cached result per cell.

That's not an LLM problem. It's a parameter sweep, and you can have the marginal-cost economics and the apples-to-apples guarantee that come with treating it like one.

```bash
pip install oryxflow pydantic-evals
```

## Frequently asked questions

### Are LLM evals just a parameter sweep?

Structurally, yes. "Is my classifier right", "did my prompt change help", and "is the cheap model good enough" are all the same shape underneath: a labeled dataset, run under N configurations, compared. Cases, scorers, models, prompt variants and repetitions are the axes of a grid, which makes an eval matrix a Cartesian product over parameters — exactly what parameter-sweep tooling has handled well for a decade. What is genuinely new is the scoring, and pydantic-evals covers that.

### Do I need an LLM eval platform, or can I use a normal workflow tool?

You need both, for different jobs. A platform earns its cost for live traces and a UI to click through failures, which is a real problem well solved. The matrix and the persistence are not LLM-specific — one cached output per parameter set is ordinary data-science plumbing, and doing it with a caching workflow engine means adding a model to your grid runs only that model instead of re-metering cells whose results already exist on your disk.

### How do I make sure my model comparison is apples-to-apples?

Share the upstream. If the dataset build is its own cached task that every configuration depends on, all models are scored against byte-for-byte identical cases — not because you were careful, but because there is only one dataset output to read. That removes the quiet failure where you regenerated the case set halfway through a sweep and half your comparison is against a different denominator.

### Should the repetition index be a task parameter in an LLM eval?

Make it a parameter when repetitions are expensive. Each rep then caches separately, so extending a three-rep study to five runs exactly two new evaluations instead of all five. The trade-off is a larger DAG. If repetitions are cheap, use pydantic-evals' `repeat` argument instead — one cached unit covers every rep, giving a smaller graph but a coarser cache, so changing the count re-runs the whole thing.

Then keep going:

- Sibling post: [Your eval platform deletes your results in 14 days](llm-eval-results-retention.md) — the retention argument.
- Sibling post: [Cheap, durable LLM evals: pydantic-evals + oryxflow](cheap-durable-llm-evals.md) — the cost arithmetic and the step-by-step build.
- [Parameter sweeps without rerunning upstream steps](parameter-sweeps-without-rerunning.md)
- [Managing complex workflows](../../docs/managing-workflows.md) — the event stream and reset scopes.
- [Why oryxflow](../../docs/why-oryxflow.md)
- [oryxflow on GitHub](https://github.com/oryxintel/oryxflow)
