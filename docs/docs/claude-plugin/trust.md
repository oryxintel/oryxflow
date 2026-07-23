---
title: Trustworthy AI data analysis
description: How the oryxflow Claude Code plugin makes an AI agent's data analysis verifiable — session-start status, rerun verification, answered staleness warnings, and durable lineage — so you check the work cheaply instead of taking it on faith.
---

# Trustworthy AI data analysis

**You should not take an AI's data analysis on faith — but you also shouldn't have to
re-derive it by hand to check it.** The oryxflow plugin's job is to make the agent's work
*cheap to verify*: reproducible, inspectable, and honest about what actually ran.

An AI coding agent writes correct pandas as well as most people. That isn't where the risk
lives. The risk is that data mistakes rarely announce themselves. In ordinary programming a
wrong result tends to crash, fail a test, or refuse to render. In data work it usually doesn't:
a join quietly changes the row count, an arithmetic step misaligns two indexes, a percentage is
computed against the wrong denominator — and the pipeline still runs to completion and prints a
plausible number. The number is wrong and nothing says so.

That's why the useful question isn't *"is the agent trustworthy?"* but *"can I check its work
without rebuilding it?"* The plugin is built to make the answer yes.

## What tends to go wrong

A handful of failure modes come up again and again, and none are exotic:

- **Silent data errors.** A merge that should be one-to-many is actually many-to-many and
  inflates every downstream total. These complete without error.
- **Reusing stale output after an edit.** The agent changes a task's code, reruns, and — if the
  engine treated the task as already done — trains on the *previous* output. The run looks
  successful; the number is from before the edit.
- **Results you can't reproduce.** An analysis assembled from inline snippets over one session
  produced its number once. Asked for it again next month, there's no reliable way to regenerate
  it.
- **State that was never written down.** What was in memory, which cell ran, in what order —
  when the result depends on things nothing captured, it can't be audited.

The common thread: the output looks identical whether the computation underneath was right or
wrong. Trust has to come from somewhere other than the output.

## What the plugin does about it

The library carries the state an agent can't hold in its head — a cache, a dependency graph, and
a lineage log. The plugin makes the agent *use* that machinery like a careful analyst would:

- **It starts every session by reading cache state**, not assuming it. Before touching anything,
  the agent checks pending staleness warnings, the last run of each task, and recent failures —
  so it never mistakes a stale cache for a fresh one.
- **It verifies that an edit actually took effect.** After changing a task, "the run didn't
  error" is not evidence the new code ran. The agent confirms the edited task shows up as *having
  recomputed* — a one-line check that turns an easy-to-miss failure into a caught one.
- **It answers every staleness warning with the right exit** — recompute, accept an
  output-equivalent refactor, or pin a task deliberately — instead of letting warnings pile up
  and get ignored.
- **It leaves decision-relevant numbers as durable lineage.** Row counts, drop rates, headline
  metrics get logged and persisted, so next session has a memory of what happened rather than
  starting blind.
- **It applies the silent-error habits as it writes** — validate the merge and assert the row
  relationship, look at the frame's shape and null counts before stating a finding, quote every
  number from a saved artifact rather than eyeballing it off a chart. These are ordinary
  practices; the value is that they're loaded into the agent's working context at the moment the
  task is written, not run as a review checklist afterward.

Together these make the work *checkable by construction*: any result can be re-opened, any rerun
confirmed, and the common silent errors are caught by habit instead of luck.

## The honest boundary: reproducible ≠ correct

Being trustworthy means being clear about the edge. oryxflow guarantees that a result was
produced by the exact code and inputs it recorded. It does **not** guarantee the result is
*right*. A pipeline with a bug in its feature logic is reproduced just as faithfully as a correct
one — you'll get the same wrong number every time, cleanly tied to the flawed code that made it.

That's not a weakness; it's the honest scope. Whether the model suits the question, whether the
method is sound, whether an analysis that runs cleanly is even answering the right question —
those stay judgment, and judgment doesn't delegate to a library or an agent. What changes is that
*checking* is now cheap: you can re-open any result, confirm what actually ran, and re-derive the
conclusion, instead of reconstructing the whole analysis before you can begin to question it.

## Takeaway

In this domain the failure isn't a crash — it's a confident wrong number, produced as fluently as
a right one, from a mistake that raised no error. The realistic response isn't to hope the agent
is careful; it's to keep the work on a footing where results are reproducible, reruns are
checkable, and silent errors are caught by habit. The library provides that footing; the plugin
supplies the habits. Together they don't make the agent trustworthy — they make its work **cheap
to verify**, which is the more useful thing, and the whole point of *faster, cheaper, and more
trustworthy* AI data analysis.

**Read next**

- [Why library + plugin is a matched pair](why.md) — the division of labor in full.
- [Project structure that stays clean](project-structure.md) — the shape that keeps AI code from
  rotting.
- [Coding standards the agent applies](coding-standards.md) — the conventions loaded where the
  work happens.
- [What caching does *not* protect against](../managing-workflows.md#what-caching-does-not-protect-against)
  — the full boundary.
- [Best practices for AI-assisted data analysis](../../blog/posts/ai-data-analysis-best-practices.md)
  — the same discipline as a standalone guide.
