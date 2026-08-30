---
title: LLM eval quickstart
description: A complete LLM eval in one file — cases, two arms, an LLM judge, confidence intervals and a cached verdict. What each line does, what it prints, and what to add next.
---

# LLM eval quickstart

You changed a prompt. Is it better?

This page is the whole answer in one file: two versions of a prompt, run over a set of cases,
scored by a judge, and reported with a confidence interval so you know whether the gap you're
looking at is real. Nothing is hidden in a base class you have to go read — everything that
decides the number is on this page.

```text
pip install "oryxflow[evals]"
```

Nothing in `oryxflow.evals` is loaded by `import oryxflow`, so if you never run evals it costs
you nothing. Import it on purpose:

```python
import oryxflow.evals as ev
```

## The whole thing

Two files. First the cases — one row per case, in a spreadsheet, because the person with the real
examples usually doesn't edit Python:

```csv title="cases.csv"
name,question
capital_fr,What is the capital of France?
capital_jp,What is the capital of Japan?
largest_au,What is the largest city in Australia?
oldest_eu,Which European capital is the oldest?
```

Then the eval:

```python title="eval.py"
import oryxflow
import oryxflow.evals as ev
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_evals.evaluators import LLMJudge

MODEL = ...   # the model under test — anything pydantic-ai accepts
JUDGE = ...   # the judge — a DIFFERENT model family from MODEL

oryxflow.set_dir('evals/data')


class Inputs(BaseModel):
    question: str


async def answer(inputs, style):
    instruction = 'Answer in one word.' if style == 'terse' else 'Answer helpfully.'
    reply = await Agent(MODEL).run('{}\n\n{}'.format(instruction, inputs.question))
    return {'reply': reply.output}


if __name__ == '__main__':
    ev.sweep(answer,
             cases=ev.load_cases('cases.csv', inputs=Inputs),
             evaluators=(LLMJudge(rubric='The reply names one specific city.',
                                  model=JUDGE, include_input=True),),
             metric=ev.Metric('names a city', 'LLMJudge'),
             style=['terse', 'helpful']).verdict()
```

That's it. No class of your own, no output directory to configure, no `--reset` to remember.

## What each piece is

| In the file | What it does |
| --- | --- |
| `class Inputs` | One field per input column in `cases.csv`. `load_cases` routes columns by **name**, so a misspelled column lands in metadata instead and pydantic raises naming the field it never got. That check is what makes keeping cases in a spreadsheet safe. |
| `async def answer(inputs, style)` | **The thing under test.** It takes one case's inputs plus whatever you're varying, and returns a dict (or a pydantic model). Write it `async` — see below. |
| `style=['terse', 'helpful']` | Any keyword holding a **list** becomes an axis. Two values, two arms. Add `model_id=['small', 'large']` and you have four. A keyword holding a single value is just a setting applied to every arm. |
| `evaluators=(LLMJudge(...),)` | How a case is scored. `LLMJudge` ships with pydantic-evals and answers one yes/no question about each output. |
| `ev.Metric('names a city', 'LLMJudge')` | The number the arms are compared on. The second argument is the **column** the score lands in — for an evaluator, its class name. |
| `.verdict()` | Prints the comparison, with intervals. |

Two rules about `answer` that are easy to get wrong and expensive to discover later:

- **Write it `async`.** Cases are scheduled as coroutines, so a plain `def` returns the same
  numbers but blocks the loop — the concurrency you asked for silently does nothing. Eight
  half-second cases at `concurrency=8`: 1.0s async, 4.9s sync. You get a warning, but the
  numbers won't tell you.
- **Let exceptions raise.** Catching one and returning it in a field makes the runner record a
  *successful* case: the failure list comes back empty, the failure rate reads zero, and the
  metric scores an error string as if it were an answer. Raising is what puts the case in the
  failure list and keeps it out of every rate.

## Run it

```text
python eval.py
```

Before anything is billed you get the projected bill, and are asked:

```text
answer · 4 cases × 2 arms × 1 rep = 8 calls
  cached 0 · new 8   (+1 preflight call per new arm)
  estimated cost: not estimated -- pass cost_per_call= for a crude calls x cost_per_call figure

Run? [y/n/c]
```

`c` runs the **preflight only** — one call per arm, enough to prove the wiring works before you
pay for the rest. Then:

```text
NAMES A CITY (higher is better)
  terse      100%  (4/4)    95% CI [51%, 100%]
  helpful     75%  (3/4)    95% CI [30%, 95%]
  Δ  terse − helpful  = +25pp  [-25pp, +75pp]   inside noise

VERDICT  terse vs helpful = +25pp [-25pp, +75pp], inside noise at 1 rep -- raise repeats
         before calling this real.
```

**Read the last line first.** Four cases and one repeat cannot separate these two arms, and the
verdict says so rather than handing you a winner. That is the point of the whole exercise: real
harnesses have measured the *same* prompt at 69% and then 64% an hour later. The levers for a
narrower band are more cases and `repeats=`.

## Run it again — you pay nothing

```text
answer · 4 cases × 2 arms × 1 rep = 8 calls
  cached 8 · new 0
```

Each arm is one cached cell on disk. Add a third arm and you pay for the third arm. Edit `answer`
and its arms re-run; everything you have already evaluated stays put. Nothing to remember, and no
stale cell reported as a fresh number.

## What to add next, in this order

The quickstart above is honest but thin. Four additions, each worth more than the last:

1. **More cases.** Fifteen to twenty-five, harvested from real material rather than invented, and
   worked across the axes that matter (hard/easy, long/short, in-scope/out-of-scope). This moves
   the interval more than anything else on the list.
2. **A guardrail.** The thing a prompt can win your headline metric by breaking. Without one, a
   prompt that answers *everything* scores 100% on "did it answer" and ships a regression:

    ```python
    guardrail=ev.Metric('makes something up', 'Hallucinated',
                        higher_is_better=False, budget=0.05)
    ```

    A winner that breaks it is reported as **not a clean win**.

3. **A coverage companion.** A rate over a filtered subset improves as the subset shrinks, so a
   prompt can score well on quality by staying silent on a third of the cases. `coverage=` is
   rendered first, above the quality number:

    ```python
    metric=ev.Metric('quality', 'Good', where='answered',
                     coverage=ev.Metric('answered', 'answered'))
    ```

4. **Check the judge.** A judge nobody checked is an opinion with a confidence interval printed
   around it. Hand-label a sample, put those labels in a column, and name it — the verdict then
   reports how well the judge agrees with you, and a corrected rate:

    ```python
    metric=ev.Metric('names a city', 'LLMJudge', human='human_label')
    ```

Then `slices=('kind', 'length')` breaks the winning arm down by any metadata column, worst first
— which is where your next batch of cases comes from.

## Three shapes, in order of how much you need

| Reach for | When |
| --- | --- |
| **`ev.sweep(fn, ...)`** — this page | You want the answer today. A function, a grid, a metric. |
| **`ev.TaskEval` + `ev.cli`** | You want the eval to **stay right**: caching keyed on the prompt files it actually reads, a baseline arm checked out of git, and a command line whose flags are derived from your arms. See [the guide](llm-evals.md). |
| **The Claude Code plugin** | You want it **built for you**: four commands that settle what you're measuring before any code exists, scaffold it, grow a real case set, and run it. See [Scaffold an LLM eval](claude-plugin/evals.md). |

They are the same machinery at three levels of ceremony — `sweep` builds a `TaskEval` for you, and
the plugin writes one. Nothing you learn here stops applying at the next level up.

## Read next

- **[LLM evals](llm-evals.md)** — the full guide: case files, arms and baselines, what caching is
  keyed on, reading the verdict, and the rules about your own code that matter most.
- **[Classes and functions](llm-evals-api.md)** — everything `oryxflow.evals` exports, in the
  order you'll meet it.
- **[What an eval plan must contain](llm-evals-checklist.md)** — the six sections a plan needs
  before anyone can run it, for when you are building an eval from someone else's.
- **[Scaffold an LLM eval](claude-plugin/evals.md)** — the plugin's four eval commands.
