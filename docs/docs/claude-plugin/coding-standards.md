---
title: Data-science coding standards for Claude Code
description: The oryxflow Claude Code plugin ships pro-level data-science coding conventions — canonical column names, code grouped by subject, docstrings as documentation — loaded into the agent's context so they shape the analysis code as it's written, not audited after.
---

# Data-science coding standards Claude Code applies as it writes

**A data-analysis project rarely starts messy — it turns messy.** One clean notebook becomes a
tangle of drifting column names, copy-pasted cleaning, and functions that never got written; hand
the work to an AI agent and the drift arrives *faster*. A coding standard only prevents that if
it's present the moment the code is written. The oryxflow Claude Code plugin ships its
data-science conventions *where the agent works* — loaded into context as it edits, phrased as
rules with the reasoning attached — so they're applied as the line is typed, not reviewed
afterward in a wiki nobody opens.

These aren't motivational-poster generalities. They're specific, opinionated defaults that keep a
project readable and safe as it grows, and they matter more with an AI in the loop: an agent will
happily produce four variants of the same column name across three files unless something steers
it otherwise.

## Naming: one canonical name, carried end to end

Every rename is a mapping a reader has to hold and can mis-apply — and a round-trip (raw →
display → raw) is a classic wrong-column bug. The convention minimizes those layers:

- **One canonical `snake_case` name per column**, renamed once at ingestion and never re-aliased
  downstream. `yield_dividend`, not `div_yld` in one file and `Dividend Yield` in another.
- **Order tokens broad → narrow**, so a family shares a leading prefix and clusters:
  `yield_dividend`, `yield_earnings`, `yield_fcf` — not `dividend_yield`. The same rule names
  tasks (`FundamentalsLeadLag`, not `LeadLagAnalysis`) and variables (`df_returns_gross`).
- **The operation goes last, as a suffix** — `_yoy`, `_ma4`, `_lag1`, and stats like `_avg` /
  `_min` / `_max`. Never a leading `avg_` / `pct_` / `n_` prefix, which hides the metric and
  scatters a family under the unit instead of the subject.
- **Pretty labels live only at the plotting layer** — Title-Case axis and legend names in `viz/`,
  never a second data-level rename.

The payoff: a whole count/ratio family shares one stem, so the arithmetic reads straight off the
names — `coverage_pct = covered / total` — and a searched-for metric is where you'd expect it.

## Code organization: group by subject

The flat starter stops scaling once a project has many tasks plus reusable helpers and plots. The
convention groups supporting code by the *subject* it concerns — a task, a dataset, or a
cross-cutting concept — mirroring how the pipeline itself is keyed on tasks:

- **`eda/<subject>/`** — read-only exploration and verification probes (this is where "just
  checking X" goes, instead of an inline one-off). A probe writes no pipeline artifact.
- **`utils/<subject>.py`** — one subject's helpers; a helper shared by two or more subjects moves
  to a concept module (`utils/geo.py`). Only truly generic helpers live in `__init__.py`, so it
  never becomes a junk drawer.
- **`viz/<subject>.py`** — that subject's figures. The starter `visualize.py` graduates here once
  you have per-subject or shared plotting.

Files are named for the specific thing they do, dropping the redundant subject token
(`eda/sales/verify_coercion.py`, not `verify_sales.py`) — so the second probe in a folder always
has a clear home.

## Docstrings are the documentation

In an oryxflow project the code *is* the pipeline doc — there's no separate description that can
drift out of sync. So a task's docstring isn't a throwaway one-liner; it states what the task
produces and its input-to-output contract:

```python
@oryxflow.requires(DataSales)
class MonthlyRevenue(oryxflow.tasks.TaskPqPandas):
    """Revenue aggregated to one row per (region, month).

    In:  raw order lines (from DataSales).
    Out: one row per region-month; revenue_gross, revenue_net, order_count.
         Null revenue_net where a refund post-dates the close.
    """
```

That single habit means "what does this pipeline do?" is answered by reading the code, not by
re-scanning the whole project or trusting the agent's memory of a past session.

## Code style that avoids quiet breakage

A few defaults prevent the failure modes that don't announce themselves:

- **Log with the task logger, not `print`.** The engine already logs task scheduling, timing, and
  completion for free; inside a task, log the domain signal you'd watch live — shapes, drop rates,
  headline metrics — through the task logger so it survives into the lineage record.
- **No `try/except` that swallows errors.** Let code fail natively so a real problem surfaces
  instead of hiding behind a plausible fallback.
- **Use off-the-shelf libraries.** Reach for statsmodels / scipy / sklearn rather than
  hand-rolling the math; a broken import means a broken environment to fix, not a cue to
  reimplement around it.
- **ASCII-only in code and output**, so nothing breaks on a Windows console.

## Check an existing codebase against them

`/oryxflow:check-standards` reviews names, style, and docstrings against the house standards and
reports what's drifted — so a project the agent (or you) built quickly can be brought back into
line without a manual audit. It's the maintenance counterpart to the conventions the skill
applies automatically while writing new code.

## Why loaded-in-context beats a style guide

A style guide reviewed after the fact catches a fraction of what it should and annoys everyone. A
convention loaded into the agent's working context — phrased as an imperative plus the failure it
prevents — gets applied as the line is written. That's the difference between a standard that's
aspirational and one that's *operative*, and it's why these conventions live next to the skill
rather than in a document someone would have to remember to open.

The point isn't that these particular rules are the one true style. It's that getting
pro-level structure *by construction* — as the code is typed — rather than by after-the-fact
vigilance is what keeps an AI-built project readable, navigable, and safe to extend as it grows.

**Read next**

- [Data-science project structure](project-structure.md) — the layout these conventions ride
  alongside.
- [Trustworthy AI data analysis](trust.md) — verifying the work the conventions help produce.
- [Why library + plugin is a matched pair](why.md) — why the conventions ship with the skill.
- [Plugin commands](commands.md) — including `check-standards`.
