---
title: Blog
description: Articles on reproducible pipelines, data lineage, tool comparisons (vs Airflow, MLflow, DVC), and trustworthy AI-assisted data science with oryxflow.
---

# Blog

Notes on making data analysis **faster, cheaper, and more trustworthy** — for humans and AI
coding agents. Reproducible pipelines, data lineage, honest tool comparisons, and why a
lightweight caching DAG changes how you build data-science workflows.

## Start here

- **[Why oryxflow](../docs/why-oryxflow.md)** — the positioning in one page: reproducibility,
  lineage, and trustworthy AI data analysis, plus when *not* to reach for it.
- **[Reproducible data science workflows in Python](posts/reproducible-data-science-workflows-python.md)**
  — what makes a workflow reproducible, and the missing middle between notebooks and orchestrators.

**Reproducibility & caching**

- [Stop rerunning your whole pipeline](posts/stop-rerunning-your-pipeline.md) — caching
  intermediate DataFrames without the stale-`.pkl` graveyard.
- [Cache intermediate DataFrames in Python](posts/cache-intermediate-dataframes-python.md) — why
  hand-rolled pickle caches go stale, and caching by task identity instead.
- [From notebook to a reproducible, cached pipeline](posts/notebook-to-pipeline.md) — migrate an
  analysis into tasks, one step at a time.
- [4 reasons your machine learning code is bad](posts/4-reasons-your-ml-code-is-bad.md) — the
  failure modes a task DAG fixes.

**Tool comparisons**

- [oryxflow vs the field](posts/oryxflow-vs-the-field.md) — the full framework landscape for
  iterative, AI-assisted analysis.
- [oryxflow vs Airflow](posts/oryxflow-vs-airflow.md) — research workflows vs production
  orchestration.
- [oryxflow vs Prefect](posts/oryxflow-vs-prefect.md) — zero-config code-aware caching vs
  configurable orchestration.
- [oryxflow vs Dagster](posts/oryxflow-vs-dagster.md) — a lightweight research loop vs an asset
  platform.
- [oryxflow vs DVC](posts/oryxflow-vs-dvc.md) — native Python task identity vs file-hash data
  versioning.
- [MLflow or pipeline caching?](posts/mlflow-or-pipeline-caching.md) — experiment tracking vs
  workflow caching (and DVC).
- [MLflow alternatives](posts/mlflow-alternatives.md) — the experiment-tracker landscape, and
  where a caching DAG fits beside (not against) it.
- [Airflow alternatives for data science](posts/airflow-alternatives-for-data-science.md) —
  research-loop tools vs production orchestrators, and when you don't need an orchestrator at all.

**Trustworthy AI-assisted data science**

- [Why a caching DAG makes your AI coding agent a better data scientist](posts/caching-dag-for-ai-agents.md)
  — where agents fail at pipeline work, and what removes those failures.
- [Best practices for AI-assisted data analysis](posts/ai-data-analysis-best-practices.md) — how to
  keep AI-generated analysis reproducible, auditable, and honest about correctness.
- [The best Claude Code plugins and tools for data science](posts/best-claude-code-plugins-data-science.md)
  — a by-the-job roundup of the AI data-science tooling landscape.
- [The best AI tools for data analysis](posts/best-ai-data-analysis-tools.md) — the layers of the
  AI data stack, and the trust layer most of them miss.

**Practical patterns**

- [Parameter sweeps without rerunning upstream steps](posts/parameter-sweeps-without-rerunning.md)
  — compare many configurations while shared steps compute once.
- [When *not* to use oryxflow](posts/when-not-to-use-oryxflow.md) — an honest guide to non-fit.

---

## All posts
