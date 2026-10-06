---
title: Scaffold an LLM eval
description: Four slash commands turn "is this prompt better?" into a kept eval — a plan, a scaffold wired to it, a real case set, and a run that prints its bill before it spends. The files it writes, and what goes in each.
---

# Scaffold an LLM eval

You changed a prompt. Is it better?

The library answers that in six lines — see [LLM evals](../llm-evals.md). That form is right when
you want the answer **today** and you don't expect to ask again.

The plugin is for the other case: an eval you **keep**. One you re-run after every prompt change,
hand to a teammate, and cite in a review six months from now. Then the six lines want a home, a
case file worth reviewing, a baseline that stays honest, and a guardrail nobody can forget to
add. Four commands build exactly that.

```text
/oryxflow:eval-plan     # 1. decide what is measured — writes the plan, no code
/oryxflow:eval-init     # 2. scaffold the eval from that plan, ending in a smoke run
/oryxflow:eval-cases    # 3. grow 3 placeholder cases into a real 15-25
/oryxflow:eval-run      # 4. run it, print the verdict, interpret the result
```

They are **one pipeline, in that order** — each command reads what the one before it wrote. You
can stop after any of them and pick up later. Nothing runs on its own: two of them write files
and `eval-run` bills you for API calls, so you invoke each one deliberately.

They work in **any Python repo** — a web app's backend is the usual home — not only in an oryxflow
data project. The plugin's `oryxflow-evals` skill activates on its own when an agent plans or edits
an LLM prompt, and suggests the commands; it never runs one.

Evals need the library, with its extra, in the environment that runs your production code:

```text
pip install "oryxflow[evals]>=26.10.5"
```

## 1. `/oryxflow:eval-plan` — decide what is measured

The command that saves you the most money is the one that spends none. It settles six questions
before any code exists:

- **What is this for?** The decision the number informs, and what a bad output costs today.
- **Which models?** The one under test and the one judging it, both pinned — and whoever owns the
  prompt gets asked whether they are the right two.
- **What is being compared?** The current prompt and the proposed one, by path, plus which commit
  the baseline arm reads.
- **What counts as better?** One number, the bar it has to clear, and whether your case count can
  actually resolve a difference that size.
- **What must not get worse?** The guardrail — the thing a prompt can win on your headline metric
  by breaking.
- **Which cases, from where?** The source of the real examples, which of them are controls, and
  where the credentials live.

The answers go in `evals/<name>/README.md`, along with the judgement calls and the known gaps. It
is the file to read before changing anything else, and the one that tells a teammate — or you,
next quarter — what the number actually claims.

The file it writes follows [what an eval plan must contain](../llm-evals-checklist.md) — goal,
models, prompts, measure, data, credentials — and the command checks its own answers against that
list before saving, recording anything it cannot answer as a known gap. That check is why the plan
hands over as something a fresh session can execute, rather than a description of an eval somebody
still has to design.

## 2. `/oryxflow:eval-init` — scaffold it

Copies the eval template into `evals/` and wires it to the plan. The first eval in a repo also
gets `evals/_env.py`, which records once how this repo loads credentials and imports production
code. It never overwrites a file you already have, so re-running it on an existing eval is safe.

It finishes with a **three-call smoke run** — enough to prove the wiring end to end without a
bill. Anything still unfilled raises an error at that point, which is deliberate: a half-wired
eval that returned an empty result would otherwise cache as a measurement.

## 3. `/oryxflow:eval-cases` — grow the case set

The scaffold ships three placeholder rows. This command replaces them with 15-25 cases harvested
from real material — your tickets, transcripts, logs — rather than invented ones, and it works
the axes the plan named (hard/easy, long/short, in-scope/control) instead of producing a flat
list of whatever came to mind first.

Invented cases aren't banned, they're **marked**: `synthetic=1`. The headline number is harvested
cases only, with the invented ones reported beside it.

## 4. `/oryxflow:eval-run` — run it and read the verdict

Prints the projected bill — how many calls, how many are already cached, what it will cost — and
waits for you before spending anything. Then it runs and interprets the result: which arm won,
whether the gap is bigger than the run-to-run noise, and whether the winner broke the guardrail.

Every cell is cached, so a re-run of an unchanged arm costs nothing. Edit a prompt and only the
arms that read it recompute.

## What lands in `evals/`

`evals/` is **one oryxflow project**, and each eval is a folder inside it:

```text
evals/
├── _env.py               # once per repo: credentials + where production code is imported from
├── data/                 # one cache for every eval (gitignore it, or put it under LFS)
├── run_eval_my_eval.py   # the command line for one eval
└── my_eval/              # one eval - a Python package, so underscores, not dashes
    ├── README.md         # what this measures and why — written by eval-plan
    ├── agent.py          # the function under test, called the way production calls it
    ├── eval.py           # the measurement: arms, metric, guardrail, baseline
    ├── cases.csv         # the case set
    ├── fixtures/         # anything a case needs on disk
    └── results/          # verdicts and side-by-side outputs — commit these
```

Two files carry all the thinking: `agent.py` says **what runs**, `eval.py` says **what counts**.
`_env.py` carries the setup, once, so five evals do not re-derive it five ways.

**Run every eval from `evals/`.** The cache and relative paths resolve against the working
directory, so a run from anywhere else builds a second cache and re-bills you for cells you already
paid for; the command line warns when that happens. Credentials load by absolute path in
`_env.py` for the same reason — a loader that resolves relative to the working directory finds
nothing from here. Eval classes share the cache, so each needs its own name (`ReplyEval`, not
`PromptEval`).

## `agent.py` — the function under test

One rule holds this file together: **it imports the live implementation.** If something has to
change to make the entry point callable from here, change it in production — because the moment
`evals/` holds its own copy of the prompt, the two drift and the eval scores the copy.

That rule is about **direction of travel**, not about where a file happens to sit:

| A prompt moving | Is | Because |
| --- | --- | --- |
| production → the eval | the drift bug | the baseline stops being the baseline the first time either side is edited |
| the eval → production | the point of the eval | a candidate that hasn't shipped has to live somewhere while you measure it |

So the baseline arm always reads production, and a candidate arm may own its prompt inside the
eval until it wins — then it's promoted, byte-exact, and the arm is repointed at production. See
[Prompts as files](../llm-evals-prompts.md) for the promotion step and why it needs a re-run.

Because this file imports your project, **your project has to be importable from `evals/`**:
installed once with `pip install -e <the directory holding its pyproject.toml>` (often not the repo
root), or put on the path by `_env.py`. `eval-init` checks it before the smoke run rather than
letting it surface as an `ImportError` mid-eval.

| What you write | What it is |
| --- | --- |
| `Inputs(BaseModel)` | One field per input column in `cases.csv`. A misspelled column lands in metadata instead, and pydantic then raises naming the field it never got — which is what makes keeping cases in a spreadsheet safe. |
| `Output(BaseModel)` | What one case produced. **Nothing here is capped or trimmed** — see [never truncate a field your metric reads](../llm-evals.md#never-truncate-a-field-your-metric-reads). |
| `prompt_root(arm)` | Where this arm reads its prompts: the working tree, or `ev.git_tree(ref, paths)` for a baseline. A baseline is **checked out**, never rebuilt by string replacement. |
| `async def run_case(inputs, **arm)` | Runs one case through production and returns an `Output`. |
| `BASELINE_REF`, `PROMPT_PATHS`, `PROMPT_GLOBS` | The commit the baseline arm reads, and the files the live arm hashes to decide when to recompute. |

Two things about `run_case` that are easy to get wrong and expensive to discover later:

- **Write it `async`.** Cases are scheduled as coroutines, so a plain `def` returns the same
  numbers but blocks the loop — the concurrency you asked for silently does nothing. Eight
  half-second cases at `concurrency=8`: 1.0s async, 4.9s sync.
- **Let exceptions raise.** Catching one and returning it in a field makes the runner record a
  *successful* case: the failure list comes back empty, the failure rate reads zero, and the
  metric scores an error string as if it were an answer. Raising is what puts the case in the
  failure list and keeps it out of every rate.

## `eval.py` — the measurement

```python
class PromptEval(ev.TaskEval):
    """One line: the question this eval answers."""

    prompt_version = oryxflow.ChoiceParameter(default='live',
                                              choices=['live', 'baseline'])

    dataset = Dataset(name='cases', cases=CASES, evaluators=[OutputOk()])

    metric = ev.Metric('quality', 'OutputOk', where='acted',
                       coverage=ev.Metric('action rate', 'acted', where='~control'))

    guardrail = ev.Metric('false action rate', 'acted', where='control',
                          higher_is_better=False, budget=0.05)

    slices = ('kind', 'synthetic')

    async def case(self, inputs):
        return await agent.run_case(inputs, prompt_version=self.prompt_version)

    def code_version(self):
        if self.prompt_version == 'baseline':
            return ev.git_sha(agent.BASELINE_REF, repo=agent.ROOT)
        return oryxflow.hash_files(*agent.PROMPT_GLOBS, root=agent.ROOT)
```

What each part is for:

| Part | What it buys you |
| --- | --- |
| **One `Parameter` per arm axis** | Each becomes a repeatable command-line flag (`--prompt-version`) and each value is one cached cell. Two axes give you the full grid. |
| **`dataset`** | A pydantic-evals `Dataset`. `CASES` comes from `ev.load_cases('cases.csv', inputs=agent.Inputs)`. |
| **`metric`** | The headline number. **`coverage=` is not optional** — a rate over a filtered subset improves as the subset shrinks, so a prompt can score well on quality by staying silent on a third of the cases. Coverage is read first, quality second. |
| **`guardrail`** | Mandatory. Without one, a prompt that acts on *everything* scores 100% on "did it act" and ships a regression. Set `budget=` and the verdict refuses to call a win *clean* when it is broken. |
| **`slices`** | Metadata columns to break the metric down by — where a flat number hides that one kind of case got worse. |
| **`case()`** | Calls `agent.run_case` with this arm's parameters. That is all it does. |
| **`code_version()`** | The bytes that decide this arm's result. This is the whole caching trick: edit a prompt, and the arms that read it recompute while the rest are served from disk. A baseline arm returns the **resolved sha**, never the ref string — `HEAD~1` names different content after every commit. |

Three more knobs, commented out in the scaffold until you want them:

```python
preview_chars = 200     # display cap for `<field>_preview`; `<field>` is never cut
max_failure_rate = 0.2  # above this the run saves nothing
preflight = None        # default runs case() once - what `--check` costs
```

### Scoring one case

The class name of your evaluator is the column your metric reads:

```python
class OutputOk(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> bool:
        return ctx.output.message.startswith(ctx.metadata['expected'])
```

Be deterministic wherever you can — when the output is one of a fixed set of labels, scoring it
is a comparison, not a judging job. For **one** yes/no quality question don't write the class at
all; pydantic-evals ships the judge:

```python
dataset = Dataset(name='cases', cases=CASES,
                  evaluators=[LLMJudge(rubric='...', model=a_different_family,
                                       include_input=True)])
metric = ev.Metric('quality', 'LLMJudge')
```

Keep a custom `Evaluator` for several fields per case, or a verdict per item inside the output.
Either way the judge belongs **here**, not inside `run_case` — then a judge failure is a scoring
failure rather than a lost case. Judge from a *different* model family than the one under test;
self-preference is real and avoiding it is free. Return `{}` for a case there is nothing to
judge: those rows read as **not measured**, which is not a failure. And before you believe a
judge at all, [check it against human labels](../llm-evals.md#check-the-judge-before-you-believe-it).

## `run_eval_<name>.py` — the command line

```python
import oryxflow.evals as ev
from my_eval.eval import MyEval

if __name__ == '__main__':
    ev.cli(MyEval)
```

The flags are **derived from the task's Parameters**, so adding an arm axis adds its flag and
there is no second place to keep in sync:

```text
cd evals
python run_eval_my_eval.py --check                    # preflight only: one call
python run_eval_my_eval.py --prompt-version live --prompt-version baseline --repeats 3
```

On top of the derived flags: `--repeats`, `--concurrency`, `--reset`, `--rescore`, `--check`,
`--csv`, `--side-by-side`, `--yes`.

A one-off question does not need the folder: a **probe** is a single committed
`evals/run_eval_<name>.py` with its cases inline and `ev.sweep`, cached like the rest and still
there the next time the surface changes. It graduates to a folder when the cases are worth
keeping. The projected calls, the cached/new split and the cost estimate print before anything is
billed; `--yes` answers that confirmation in advance.

## `cases.csv` — the case set

One row per case. Columns matching a field on `Inputs` are inputs; everything else is metadata
you can filter and slice on.

| Column | What it does |
| --- | --- |
| `name` | Identifies the case in the per-case table and in failures. |
| *your input columns* | Routed to `Inputs` by name. |
| `control` | Cases where doing **nothing** is correct. Keep at least one; the guardrail reads them. |
| `synthetic` | `1` marks an invented case. The headline is harvested cases only. |
| `holdout` | `1` withholds a case whose text is embedded in the prompt — scoring it against its own answer key measures memorisation, in every arm. |
| *anything else* | Available to `where=` and to `slices`. |

A spreadsheet is the point: the person with the real examples is usually a domain expert who
doesn't edit Python, and a case set stops being reviewable as a diff long before it stops growing.

## The placeholder markers

Every spot the scaffold cannot fill for you carries a marker:

```python
# PLACEHOLDER SCAFFOLD - the plan's scoring rule; delete this line when filled.
```

`eval-init` fills the ones it can from the plan and deletes their markers; `eval-cases` deletes
the case-set one. Any left over are the checklist of what still needs your judgement — search for
`PLACEHOLDER SCAFFOLD` before you trust a number.

## Doing it by hand

You don't need the plugin. The whole scaffold is `ev.TaskEval` plus `ev.cli`, both documented in
[LLM evals](../llm-evals.md) — the commands write the files, decide nothing you couldn't decide
yourself, and leave behind plain Python you own. What they buy is the six questions asked in the
right order, before the first API call.

## Read next

- **[LLM eval quickstart](../llm-evals-quickstart.md)** — the same thing without the scaffold: a
  complete eval in one file.
- **[LLM evals](../llm-evals.md)** — the guide: `ev.sweep`, `ev.TaskEval`, metrics, guardrails,
  intervals, and the verdict.
- **[Classes and functions](../llm-evals-api.md)** — everything `oryxflow.evals` exports.
- **[Prompts as files](../llm-evals-prompts.md)** — getting a prompt out of a Python string, and
  why versions are commits rather than filenames.
- **[What an eval plan must contain](../llm-evals-checklist.md)** — the six sections behind the
  file `eval-plan` writes.
- **[Plugin commands](commands.md)** — the other five commands.
