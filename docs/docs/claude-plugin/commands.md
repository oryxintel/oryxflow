---
title: Plugin commands
description: The oryxflow Claude Code plugin's slash commands — scaffold a project, migrate an existing analysis into a cached pipeline, check house standards, put data under Git LFS, and build an LLM eval that measures a prompt change instead of guessing.
---

# Plugin commands

The plugin adds nine slash commands. Most of the time you won't need them explicitly — the
`oryxflow` skill activates on its own inside a project — but they're the fast path for the common
setup and maintenance jobs.

## Set up and maintain a project

- **`/oryxflow:init-project`** — set up a ready-to-run project structure in an empty directory,
  so you start writing tasks straight away instead of building the folders, files, and
  conventions by hand.
- **`/oryxflow:migrate`** — restructure an existing ad-hoc analysis (monolithic notebooks, linear
  scripts, hardcoded paths) into a cached, parameterized oryxflow pipeline, **one task at a
  time** — so you get reproducibility and caching without a risky big-bang rewrite.
- **`/oryxflow:init-gitlfs`** — put `data/` under Git LFS, so you version and share your data as
  easily as your code — teammates clone the repo and get the exact datasets each run produced.
- **`/oryxflow:update-project`** — bring an older project up to the current project structure, so
  you pick up the latest conventions and layout without a manual migration.
- **`/oryxflow:check-standards`** — check names, style, and docstrings against the house
  standards, so the codebase stays consistent and easy for teammates (and the AI) to navigate
  and extend.

## Measure a prompt change

Four more commands turn "is this prompt better?" into a number you can defend. They are **one
pipeline, in this order** — each reads what the one before it wrote, and you can stop after any
of them and pick up later:

- **`/oryxflow:eval-plan`** — *step 1 of 4.* Decide what the eval measures before any code
  exists: what counts as better, what must not get worse, what you're comparing against, and
  which cases. Writes the plan, spends nothing.
- **`/oryxflow:eval-init`** — *step 2 of 4.* Scaffold the eval from that plan into
  `evals/<name>/`, so you fill in judgement calls instead of boilerplate. Ends in a three-call
  smoke run that proves the wiring without a bill.
- **`/oryxflow:eval-cases`** — *step 3 of 4.* Grow the three placeholder cases into a real 15-25,
  harvested from your own material and worked across the axes that matter — so the case set is
  evidence rather than whatever came to mind first.
- **`/oryxflow:eval-run`** — *step 4 of 4.* Run it and read the verdict: which arm won, whether
  the gap beats the run-to-run noise, and whether the winner broke the guardrail. The projected
  bill prints before anything is spent.

Evals need the extra (`pip install "oryxflow[evals]"`). Every cell is cached, so a re-run of an
unchanged arm costs nothing and editing a prompt re-runs only the arms that read it.

[Scaffold an LLM eval](evals.md) walks through all four, and documents the files they write.

## The migration path most people want

If you already have a notebook or script that works, `/oryxflow:migrate` is the on-ramp. It
converts the analysis into tasks incrementally — each step becomes a cached, parameterized task
with its dependencies wired — so at every point you have a working pipeline, not a half-rewritten
one. The end state is reproducible and lineage-tracked, and the expensive steps stop rerunning on
every edit.

See the companion guide
[Turn a messy notebook into a reproducible pipeline](../../blog/posts/notebook-to-pipeline.md) for
what that transformation looks like step by step.

## After scaffolding

Once the project exists, the skill takes over automatically — it keeps the wiring consistent,
verifies your edits actually reran the tasks you expected, and answers staleness warnings the
right way. That ongoing discipline is the subject of
[Trustworthy AI data analysis](trust.md) and [Why library + plugin is a matched pair](why.md).

The `init-project` scaffold and the graduated path a growing project follows are covered in
[data-science project structure](project-structure.md); the naming, code-organization, and
docstring conventions `check-standards` enforces are in
[data-science coding standards](coding-standards.md).
