# oryxflow

[![PyPI version](https://img.shields.io/pypi/v/oryxflow.svg)](https://pypi.org/project/oryxflow/)
[![License: MIT](https://img.shields.io/pypi/l/oryxflow.svg)](https://github.com/oryxintel/oryxflow/blob/main/LICENSE)
[![Socket Badge](https://socket.dev/api/badge/pypi/package/oryxflow)](https://socket.dev/pypi/package/oryxflow)
[![Docs](https://img.shields.io/badge/docs-docs.oryxflow.dev-blue)](https://docs.oryxflow.dev/)

**Faster, cheaper, more trustworthy data analysis — for humans and AI coding agents.**

oryxflow turns a data-science script into a pipeline that caches every step, reruns exactly what a
change affects, and records how each result was made. You never name an intermediate file or track
which parameters produced which output again.

It's a Python library. No server, no database, no account, no config files.

```bash
pip install oryxflow
```

## 30 seconds: compare two models without rebuilding the features

```python
import oryxflow
import pandas as pd
import sklearn.datasets, sklearn.ensemble, sklearn.linear_model

oryxflow.set_dir('data/')


class GetData(oryxflow.tasks.TaskPqPandas):
    """Load the raw training data once."""
    persists = ['x', 'y']

    def run(self):
        ds = sklearn.datasets.load_diabetes()
        self.save({'x': pd.DataFrame(ds.data, columns=ds.feature_names),
                   'y': pd.DataFrame(ds.target, columns=['target'])})


@oryxflow.requires(GetData)                      # declare the dependency
class ModelTrain(oryxflow.tasks.TaskPickle):
    """Fit one model. `model` is part of the output's identity."""
    model = oryxflow.ChoiceParameter(default='ols', choices=['ols', 'gbm'])

    def run(self):
        df_x, df_y = self.inputLoad()            # GetData's output, already loaded
        clf = {'ols': sklearn.linear_model.LinearRegression,
               'gbm': sklearn.ensemble.GradientBoostingRegressor}[self.model]()
        clf.fit(df_x, df_y.values.ravel())
        self.save(clf)                           # cached; no filename to invent
        self.saveMeta({'score': clf.score(df_x, df_y)})


flow = oryxflow.WorkflowMulti(ModelTrain, {'ols': {'model': 'ols'},
                                           'gbm': {'model': 'gbm'}})
result = flow.run()
print(result.summary())                          # what ran, and what came from cache
print(flow.outputLoadMeta())
# {'ols': {'score': 0.5177484222203498}, 'gbm': {'score': 0.7990392018966864}}
```

Look at what the summary says about the second experiment:

```text
===== gbm =====
Scheduled 2 tasks of which:
* 1 complete ones were encountered:
    - GetData                        <- loaded from cache, not recomputed
* 1 ran successfully:
    - ModelTrain(model=gbm)
```

`GetData` ran **once** and was reused. Run `flow.run()` again and nothing happens at all — every
task is a cache hit. Add a third model tomorrow and only that one trains. Now edit the body of
`GetData`: oryxflow notices the *code* changed and retrains both models on the new data, so you
can't compare a new model against stale features by accident. (Reformat the code or add a comment
and nothing reruns — it compares what the code *does*. And a step that last took over ten minutes
warns instead of silently recomputing, so a refactor can't quietly burn a long run.)

That's the whole idea. Caching is how it works; **trust is what you get.**

## What you get

- **You always get the right result.** Change a parameter, the data, or a task's code and oryxflow
  reruns exactly what that change affects — and everything downstream. Cosmetic edits (comments,
  formatting) don't trigger a rerun.
- **No file mess, no parameter bookkeeping.** No `features_v3_final.pkl`, no `to_pickle` /
  `read_pickle` plumbing, no spreadsheet of which run used which settings. Ask for a result by the
  task and parameters that made it.
- **Seconds instead of minutes.** Finished steps load from cache, so the 10-minute data pull runs
  once, not once per edit.
- **An answer to "is this stale?"** oryxflow records what ran, when, with which code and inputs, and
  why it recomputed — so staleness and provenance are queries, not guesses.
- **Works with anything.** A task's `run()` is just Python, so any ML library (sklearn, PyTorch,
  Keras, XGBoost) and any data stack works inside it. oryxflow manages the graph and the storage,
  not your math. Outputs save as parquet, pickle, CSV, JSON, Excel, Markdown, or an in-memory cache
  — locally or on S3/GCS.

## How oryxflow compares

oryxflow doesn't replace an experiment tracker or a production orchestrator — it fills the gap
between an ad-hoc script and a heavyweight platform, and composes with both. What's distinctive is
the combination of local-first simplicity, invalidation that notices a **code** change, and
always-on lineage.

| | Local, zero-infra | Automatic caching & reruns | Reruns on a **code** change | Queryable lineage | Experiment dashboard | Production scheduling |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| **oryxflow** | ✅ | ✅ | ✅ automatic | ✅ | — (use a tracker) | — (use an orchestrator) |
| Notebooks + pickle files | ✅ | ❌ hand-rolled | ❌ | ❌ | ❌ | ❌ |
| MLflow / W&B | partial | ❌ (tracks, doesn't rerun) | ❌ | logs runs | ✅ | ❌ |
| Airflow / Prefect / Dagster | ❌ server/infra | opt-in / configured | ❌ | run history | partial | ✅ |
| DVC | ✅ | ✅ (file-hash stages) | on declared file deps | via Git | ❌ | ❌ |

- **vs Airflow / Prefect / Dagster** — a different job. Those run scheduled production pipelines on
  real infrastructure; oryxflow is a `pip install` for the local research loop.
  [Read more →](https://docs.oryxflow.dev/blog/comparisons/oryxflow-vs-airflow/)
- **vs MLflow / W&B** — complementary, and you should expect to use both. Trackers answer "which run
  scored 0.91?"; oryxflow answers "which steps must rerun to reproduce it, and are its inputs
  stale?" Keep logging to your tracker *inside* oryxflow tasks.
  [Read more →](https://docs.oryxflow.dev/docs/experiment-tracking/)
- **vs DVC** — both cache pipelines. DVC hashes files and YAML-declared stages; oryxflow keeps
  identity in native Python, so a parameter change is a new cached result automatically and a code
  edit reruns the affected tasks on its own — no config files to maintain.
  [Read more →](https://docs.oryxflow.dev/blog/comparisons/oryxflow-vs-dvc/)
- **The full landscape** (Metaflow, Kedro, Ploomber, Flyte, ZenML, Snakemake…):
  [oryxflow vs the field](https://docs.oryxflow.dev/blog/comparisons/oryxflow-vs-pipeline-frameworks/)

## Start with a quick EDA — it scales from there

You don't need a task graph on day one, and you don't have to decide upfront. Start with a plain
script or an exploratory probe; as the work gains steps, cost, and parameter combinations — as it
always does — `/oryxflow:migrate` lifts what you already wrote into cached tasks. Simple scripts at
the start, arbitrary complexity later, with no rewrite and no cliff in between.

Already have a project that got away from you? Nine notebooks and a folder of `clean_v3.csv` is
exactly the starting point that
[Migrate a messy notebook project](https://docs.oryxflow.dev/docs/migrate-notebook-to-pipeline/) is
written for.

## Build it with Claude Code

The [oryxflow Claude Code plugin](https://github.com/oryxintel/oryxflow-claude-plugin) teaches your
coding agent to build the analysis this way — so it reuses expensive results instead of burning your
time and tokens redoing them, and can't quietly train a model on stale data. It's a plugin (a skill
plus slash commands), not an MCP server.

```text
/plugin marketplace add oryxintel/oryxflow-claude-plugin
/plugin install oryxflow@oryxflow
```

The `oryxflow` skill auto-activates whenever you work in a pipeline project. The slash commands:

| Command | What it does |
| --- | --- |
| `/oryxflow:init-project` | Scaffold a runnable project — tasks, params, flow, config, layout |
| `/oryxflow:migrate` | Restructure a messy notebook or script project into cached tasks |
| `/oryxflow:check-standards` | Review an existing project against the data-science conventions |
| `/oryxflow:init-gitlfs` | Set up Git LFS to version data outputs |
| `/oryxflow:update-project` | Reconcile an older scaffold with the current template |

More: [Claude Code for data science](https://docs.oryxflow.dev/docs/claude-code-for-data-science/).

## When *not* to use oryxflow

Being honest about fit is part of being trustworthy:

- **Production orchestration.** If you need cron-style scheduling, retries across a cluster, and
  SLAs, use Airflow, Prefect, or Dagster. oryxflow is built for the research loop, not production
  ops.
- **Experiment dashboards.** If you want a searchable UI charting every run's metrics, that's an
  experiment tracker's job (MLflow, Weights & Biases) — and oryxflow composes cleanly beside one.
- **Distributed or larger-than-memory execution.** That's Flyte or Metaflow territory.

And one boundary worth stating plainly: oryxflow makes your analysis **reproducible, not correct**.
It guarantees a result came from the code and inputs it recorded, and that stale steps rerun. It
does not check that your join grain was right or your denominator sensible.
[The full honest guide →](https://docs.oryxflow.dev/blog/guides/when-not-to-use-oryxflow/)

## Documentation

**[docs.oryxflow.dev](https://docs.oryxflow.dev/)** — the full guide and API reference.

Start here:

- [Why oryxflow](https://docs.oryxflow.dev/docs/why-oryxflow/) — the positioning in full
- [Quickstart](https://docs.oryxflow.dev/docs/quickstart/) — a running, self-caching pipeline in
  minutes
- [Transition from scripts](https://docs.oryxflow.dev/docs/transition/) — convert an existing
  analysis
- [Managing complex workflows](https://docs.oryxflow.dev/docs/managing-workflows/) — code
  invalidation, selective resets, multi-experiment flows

Worth reading:

- [4 reasons your machine learning code is probably bad](https://docs.oryxflow.dev/blog/machine-learning/why-machine-learning-code-is-bad/)
- [Why a caching DAG makes your AI coding agent a better data scientist](https://docs.oryxflow.dev/blog/ai-agents/caching-dag-for-ai-coding-agents/)
- [From notebook to a reproducible, cached pipeline](https://docs.oryxflow.dev/blog/reproducibility/notebook-to-reproducible-pipeline/)

Runnable examples in this repo:

- [Minimal example](docs/example-minimal.py)
- [Multi-parameter workflow](docs/example-flow-multi.py)
- [Full ML model comparison](docs/example-ml-compare.py)
- [Functions-only API](docs/example-functional.ipynb) — most of the benefit, barely any change to
  your code

## Installation

```bash
pip install oryxflow          # install
pip install oryxflow -U       # update
```

Python 3 only — use `pip3 install oryxflow` if `python` still points at Python 2 on your machine.

Behind an enterprise firewall, clone the repo and run `pip install .`

Latest development version:

```bash
pip install git+https://github.com/oryxintel/oryxflow.git -U
```

<details>
<summary><b>Sharing data and version-controlling outputs</b></summary>

By default, data is written to `data/`, which is gitignored so large files stay out of source
control. To version outputs, use Git LFS (or DVC):

1. Install the LFS extension, once per machine:

```bash
winget install GitHub.GitLFS   # or: choco install git-lfs, or: apt install git-lfs
git lfs install                # hooks LFS into your git config
```

2. Adjust `.gitignore` so `data/` and `reports/render` are tracked.

3. Tell LFS which files to track:

```bash
git lfs track "data/**"
git lfs track "reports/render/**"
git lfs track "*.ipynb"
```

4. Commit `.gitattributes` and `.gitignore`.

In Claude Code, `/oryxflow:init-gitlfs` does all of this for you. To hand a whole flow — code and
cached outputs — to a colleague, see
[Collaborate & share flows](https://docs.oryxflow.dev/docs/collaborate/).

</details>

## Pro version

Additional features for teams:

- Team sharing of workflows and data
- Integrations for databases and cloud storage (SQL, S3)
- Integrations for distributed compute (dask, PySpark)
- Integrations for cloud execution (Athena)
- Workflow deployment and scheduling

[Schedule a demo](https://calendar.app.google/FkNWJE9u7QuowfH89)

## Contributing

Contributions are welcome. Fork the repo, pick an open issue, then:

- Create a branch named `[issue_no]_yyyymmdd_[feature]`
- Implement the change
- Write unit tests for the desired behavior
- Open a pull request against `main`

Bug fixes follow the same flow — just use the bug name instead of a feature name. Make sure the
existing test suite still passes.

## License and security

MIT licensed — see [LICENSE](LICENSE).

Vetting oryxflow for a corporate package firewall? See
[Security & supply chain](https://docs.oryxflow.dev/docs/supply-chain/).
