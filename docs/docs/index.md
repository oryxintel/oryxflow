---
title: Documentation
description: oryxflow documentation — install, quickstart, guides for tasks, workflows, parameters, I/O formats, and the API reference.
---

# Documentation

Everything you need to build data-science pipelines with oryxflow: declare each step as a
**task**, and the engine runs the DAG in order, skips what's already computed, reruns exactly
what a parameter, data, or code change affects, and lets you load any result by name.

New here? Start with **[Installation](installation.md)** then the **[Quickstart](quickstart.md)**.

<div class="grid cards" markdown>

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

-   :material-robot: **[Build with Claude Code](claude-plugin.md)**

    Let an AI coding assistant scaffold the project and wire the DAG.

-   :material-api: **[API Reference](reference.md)**

    Every public symbol, generated from the source docstrings.

</div>
