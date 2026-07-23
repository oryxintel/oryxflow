---
title: Plugin commands
description: The oryxflow Claude Code plugin's slash commands — scaffold a project, migrate an existing analysis into a cached pipeline, check house standards, and put data under Git LFS.
---

# Plugin commands

The plugin adds five slash commands. Most of the time you won't need them explicitly — the
`oryxflow` skill activates on its own inside a project — but they're the fast path for the common
setup and maintenance jobs.

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

## The migration path most people want

If you already have a notebook or script that works, `/oryxflow:migrate` is the on-ramp. It
converts the analysis into tasks incrementally — each step becomes a cached, parameterized task
with its dependencies wired — so at every point you have a working pipeline, not a half-rewritten
one. The end state is reproducible and lineage-tracked, and the expensive steps stop rerunning on
every edit.

See the companion guide
[From notebook to a reproducible, cached pipeline](../../blog/posts/notebook-to-pipeline.md) for
what that transformation looks like step by step.

## After scaffolding

Once the project exists, the skill takes over automatically — it keeps the wiring consistent,
verifies your edits actually reran the tasks you expected, and answers staleness warnings the
right way. That ongoing discipline is the subject of
[Why library + plugin is a matched pair](why.md).
