---
title: Documentation
description: oryxflow documentation — install, quickstart, guides for tasks, workflows, parameters, I/O formats, and the API reference.
---

# Documentation

Everything you need to build data-science pipelines with oryxflow: declare each step as a
**task**, and the engine runs the DAG in order, skips what's already computed, reruns exactly
what a parameter, data, or code change affects, and lets you load any result by name. The result
is faster, cheaper, and more **trustworthy** data analysis — reproducible and lineage-tracked by
default, for humans and AI coding agents alike.

New here? Read **[Why oryxflow](why-oryxflow.md)** for the positioning, then start with
**[Installation](installation.md)** and the **[Quickstart](quickstart.md)**.

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

-   :material-robot: **[Build with Claude Code](claude-plugin/index.md)**

    Make AI-written data analysis trustworthy: scaffold the project, wire the DAG, and teach the
    agent to use the cache correctly.

-   :material-api: **[API Reference](reference.md)**

    Every public symbol, generated from the source docstrings.

</div>
