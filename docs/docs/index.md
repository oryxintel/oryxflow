---
title: Documentation
description: "oryxflow documentation — build trustworthy, reproducible data science pipelines in Python: install, quickstart, guides for tasks, workflows, parameters, I/O formats, and the API reference."
---

# oryxflow documentation

Everything you need to build data-science pipelines you can believe. Declare each step as a
**task**. The engine runs your steps in dependency order and reruns exactly what a parameter,
data, or code change affects, so no result is ever built on stale data — and it records what
produced every output, which makes AI data analysis reproducible by default, for humans and AI
coding agents alike. There are no filenames to invent and no note to keep about which parameters
produced which output; you load any result by name. None of that costs you time, either: anything
already computed loads from cache instead of running again.

New here? Read **[Why oryxflow](why-oryxflow.md)** for the positioning, then start with
**[Installation](installation.md)** and the **[Quickstart](quickstart.md)**. Already have a project
that got out of hand? Go straight to
**[Migrate a messy notebook project](migrate-notebook-to-pipeline.md)**.

## Guides for reproducible data science

<div class="grid cards" markdown>

-   :material-shield-check: **[Why oryxflow](why-oryxflow.md)**

    What it's for and when *not* to use it — reproducibility, lineage, and trustworthy AI data
    analysis, plus honest comparisons.

-   :material-download: **[Installation](installation.md)**

    Install oryxflow and its optional extras (cloud storage, export, dask).

-   :material-rocket-launch: **[Quickstart](quickstart.md)**

    From nothing to a running, self-caching pipeline in a few minutes.

-   :material-swap-horizontal: **[Transition from scripts](transition.md)**

    Turn an existing analysis script into cached tasks.

-   :material-broom: **[Migrate a messy notebook project](migrate-notebook-to-pipeline.md)**

    Nine notebooks and a folder of `clean_v3.csv`? Restructure it so a wrong number stops being
    possible — by hand or in one command.

-   :material-cube-outline: **[Writing & managing tasks](tasks.md)**

    Dependencies, inputs, outputs, and save formats.

-   :material-sitemap: **[Workflows](workflow.md)** & **[Running workflows](run.md)**

    Wrap tasks in a flow; preview, run, and reset.

-   :material-tune: **[Parameters](advparam.md)**

    Parameter inheritance and how it drives selective reruns.

-   :material-content-save-outline: **[Task I/O formats](targets.md)**

    Parquet, pickle, CSV, in-memory cache, and cloud storage.

-   :material-layers-triple: **[Managing complex workflows](managing-workflows.md)**

    Automatic code invalidation, selective resets, multi-experiment flows.

-   :material-chart-line: **[Experiment tracking](experiment-tracking.md)**

    How oryxflow pairs with MLflow or Weights & Biases — different halves of the same project.

-   :material-robot: **[Build with Claude Code](claude-plugin/index.md)**

    Make AI-written data analysis trustworthy: scaffold the project, wire the DAG, and teach the
    agent to use the cache correctly.

-   :material-file-code: **[Built for AI coding agents](ai-ready.md)**

    `llms.txt` for one-request ingestion, core examples executed by the test suite, and a reference
    generated from docstrings that cover the whole public API.

-   :material-api: **[API Reference](reference.md)**

    Every public symbol, generated from the source docstrings.

</div>
