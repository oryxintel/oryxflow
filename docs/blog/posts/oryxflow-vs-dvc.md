---
date: 2026-07-23
slug: oryxflow-vs-dvc
categories:
  - Comparisons
description: oryxflow vs DVC — DVC versions big data files as git-pinned pipeline stages; oryxflow builds native Python task identity from parameters and automatic code-change detection. A fair, deep comparison of what each one's identity is made of, and why they compose.
---

# oryxflow vs DVC: Python task identity vs file-hash data versioning

*They both promise reproducible pipelines, so they get pitched as alternatives. But they build
"the same task" out of completely different things — and once you see what, you'll want both.*

<!-- more -->

If you searched "DVC vs Python workflow library," here's the honest short answer up front: DVC
versions large data and model files alongside Git and reruns a pipeline stage when a declared file
hash changes; [oryxflow](https://github.com/oryxintel/oryxflow) is a small, local-first Python
library that turns your task classes into a cached, dependency-aware DAG whose identity is built
from parameters plus automatic code-change detection. Neither replaces the other. This is the deeper
dive on where the line falls (there's a shorter DVC section in
[Do you need MLflow, or pipeline caching?](mlflow-or-pipeline-caching.md) — this post goes further).

## What is oryxflow, in one sentence?

oryxflow is a zero-infrastructure Python library that makes an analysis you're actively editing
fast and trustworthy: you declare `Task` classes with parameters and `requires()` dependencies, it
runs the DAG in dependency order, caches every output, and reruns exactly what a code, data, or
parameter change affects — with a lineage trail of what ran and why.

## What is DVC actually for?

DVC (Data Version Control) is a **data-versioning** tool. Its center of gravity is putting large
files — datasets, model weights, intermediate artifacts — under version control *alongside your Git
history*, without bloating the repo. Git tracks a small `.dvc` pointer; the real bytes live in
remote storage (S3, GCS, SSH, and friends) that DVC pushes and pulls.

On top of that, DVC has a **pipeline** layer. You describe stages in `dvc.yaml`: each stage names a
shell **command**, its input **dependencies** (files/params), and its **outputs** (files). DVC
hashes those declared files and reruns a stage when a hash changes — file-hash pipeline caching. It
also has DVC Experiments for sweeping parameters and comparing runs, all pinned to Git commits.

That is a genuinely great fit when your pipeline is a chain of shell commands over big data files
that need to be versioned and shared, reproducible from a specific commit.

## So what's the real difference from oryxflow?

Not "caching" — both cache. The honest core difference is **what identity is built from**.

**DVC's identity is files and YAML-declared stages.** A stage is defined by its command string and
the hashes of the files you told it to watch. It's CLI-command-oriented: DVC doesn't look inside
your Python, it looks at the files crossing the boundary. If your logic changes but the declared
output file happens to hash the same — or if you forget to declare a dependency — DVC's model can't
see it.

**oryxflow's identity is native Python task identity.** A task's cache key is its parameters plus its
own code *and* every helper file it imports — compared by what the code *does*, not how it's written.
There's no `dvc.yaml`, no command strings, no manually declared file lists: the DAG *is* your `requires()`
methods, and the I/O is type-driven and zero-config — a `TaskPqPandas` saves a DataFrame to parquet,
`self.inputLoad()` hands it to the next task, and no path appears anywhere in your code.

Two consequences fall out of that, automatically:

- **A parameter change is a new cached identity.** Run a task with `alpha=0.1` and again with
  `alpha=0.2` and you get two distinct cached outputs, side by side — no config edit, no new stage.
- **A code change reruns downstream on its own.** Edit a function's logic and oryxflow reruns that
  task and everything downstream while the expensive upstream stays cached. Edit only comments or
  formatting and nothing recomputes — oryxflow compares what your code *does*, not how it's written,
  so cosmetic edits are free.
  DVC would need you to keep `dvc.yaml` and your file declarations in step with the code by hand.

Here's the whole loop in oryxflow — verified API, no config files anywhere:

```python
import oryxflow

oryxflow.set_dir('data/')

class MakeFeatures(oryxflow.tasks.TaskPqPandas):
    horizon = oryxflow.IntParameter(default=30)

    def run(self):
        raw = load_raw_frame()            # your code
        self.save(build_features(raw, self.horizon))

@oryxflow.requires(MakeFeatures)
class TrainModel(oryxflow.tasks.TaskPickle):
    alpha = oryxflow.FloatParameter(default=0.1)

    def run(self):
        features = self.inputLoad()       # upstream output, already loaded
        model = fit(features, self.alpha)
        self.save(model)
        self.saveMeta({'alpha': self.alpha})

flow = oryxflow.Workflow(TrainModel, {'alpha': 0.2, 'horizon': 60})
flow.run()                                # runs only what's missing or changed
model = flow.outputLoad()
```

Change `alpha`, `flow.run()` again, and only `TrainModel` recomputes — `MakeFeatures` is served from
cache because neither its params nor its code changed. Edit the body of `build_features`
and both rerun. You wrote no stage file to make that happen.

## oryxflow vs DVC, side by side

| | oryxflow | DVC |
| --- | --- | --- |
| **Primary job** | in-session Python compute graph | versioning big data/model files with Git |
| **Identity built from** | params + automatic code-change detection | file hashes + `dvc.yaml` stage commands |
| **Pipeline definition** | native `requires()` methods, no config | `dvc.yaml` stages (command + deps + outs) |
| **Data I/O** | type-driven, zero-config `save`/`inputLoad` | you write the command's file reads/writes |
| **Parameter change** | automatically a new cached identity | via params files / DVC Experiments |
| **Code edit reruns downstream** | automatic, including edits to helpers | only if a declared file hash changes |
| **Data versioning to remote storage** | ❌ (not its job) | ✅ its core strength |
| **Ties artifacts to Git commits** | ❌ | ✅ |
| **Setup** | `pip install`, local `data/` folder | Git + a DVC remote |
| **Lineage** | `.oryxflow/events.jsonl` event log | Git history + `dvc.lock` |

Read the ❌ rows the right way: they aren't oryxflow "losing." Versioning large files to a remote and
pinning them to commits is the job DVC exists for. If you need it, you need DVC.

## Which do you need?

- **The job is versioning big data/model files, sharing them off a remote, and reproducing a run
  from a specific Git commit** → DVC.
- **The job is iterating on Python tasks in a session — parameter sweeps, per-entity fan-outs, an
  edit-rerun loop you run all day, and you want reruns scoped automatically to what changed** →
  oryxflow.
- **Your pipeline is naturally a set of shell stages over versioned files** → DVC's model fits.
- **Your pipeline is naturally a graph of Python functions passing DataFrames** → oryxflow's model
  fits without the YAML.

## They compose

This isn't either/or. The clean pattern uses each for its strength:

- **DVC for artifact and data versioning** tied to Git — the big input datasets and the model files
  you want reproducible from a commit and shareable via a remote.
- **oryxflow for the Python compute graph on top** — the in-session iteration where you're changing
  code constantly and want instant, correctly-scoped reruns without touching a config file.

And if you *do* want oryxflow's local `data/` outputs versioned, you don't have to leave the
ecosystem: the Claude Code plugin's `/oryxflow:init-gitlfs` command puts `data/` under Git LFS for
you. (oryxflow ships as a [Claude Code plugin](https://github.com/oryxintel/oryxflow-claude-plugin) —
a skill and slash commands — not an MCP server.)

One honest caveat that applies to both, and to every caching tool: they make your pipeline
**reproducible, not correct**. oryxflow guarantees the model you're evaluating was trained on the
current data and that stale steps get rerun — it does not check that your logic is *right*. That's
still your job (and your tests').

## Takeaway

DVC and oryxflow both promise reproducibility, but they build "the same task" out of different
material: DVC hashes files and YAML-declared stages, oryxflow derives identity from Python
parameters and automatic code-change detection. Pick DVC when the job is versioning big data to Git and
a remote; pick oryxflow when the job is iterating on Python tasks in a session. On a real project you
often want both — DVC underneath for versioned artifacts, oryxflow on top for the compute graph.

```bash
pip install oryxflow
```

**Read next**

- **[Do you need MLflow, or pipeline caching?](mlflow-or-pipeline-caching.md)** — the shorter DVC
  section and the tracking-vs-caching split.
- **[oryxflow vs the field](oryxflow-vs-the-field.md)** — the full framework comparison.
- **[oryxflow vs Airflow](oryxflow-vs-airflow.md)** — research caching vs production scheduling.
- **[Why oryxflow](../../docs/why-oryxflow.md)** — reproducibility, lineage, and trustworthy AI data
  analysis.
- **[Managing complex workflows](../../docs/managing-workflows.md)** — how automatic code
  invalidation works.
- **[Build with Claude Code](../../docs/claude-plugin/index.md)** — the plugin, skill, and slash
  commands (including `/oryxflow:init-gitlfs`).
- Source & examples: <https://github.com/oryxintel/oryxflow>
