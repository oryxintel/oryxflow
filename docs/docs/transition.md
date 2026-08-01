# Transition to oryxflow: from scripts to reproducible data science pipelines

Most data-science code starts as a script: a chain of functions that read a file, transform it, and write the next file, wired together by hand at the bottom. It works until it doesn't — you change one step and have to remember which downstream files are now stale, six months later you can't tell which parameters produced which output, and the only safe move you have left is to re-run the whole thing, slow data pull included, to test a one-line change.

oryxflow turns that script into a pipeline of **tasks** and takes over that bookkeeping. Four things you were doing in your head become properties of the code:

- **You can believe the number** — a task's output is tied to the code and parameters that produced it, so it can't quietly be served to you after either has changed. Edit a task's logic, or a helper it calls, and that task and everything downstream recompute on the next run; the classic trap of testing new code against an old cached output simply isn't available.
- **You can reproduce it** — every output is traceable to the task, code, and parameters behind it, so "how was this made?" has an answer you can look up rather than reconstruct, and you can regenerate the result on demand.
- **Provenance you can query** — oryxflow records what ran, when, and why. "Is this stale?" and "did I already run this?" stop being things you track in your head.
- **And it's faster, not slower** — that rigor normally costs you rerun time. Here it doesn't: a task whose output is already current is skipped, so a small edit no longer re-pulls the raw data, and each parameter set keeps its own output side by side instead of overwriting the last one.

## Current Workflow Using Functions

Your code currently probably looks like the example below. How do you turn it into a oryxflow workflow?

```python
import pandas as pd

def get_data():
    data = pd.read_csv('rawdata.csv')
    data = clean(data)
    data.to_pickle('data.pkl')

def preprocess(data):
    data = scale(data)
    return data

# execute workflow
get_data()
df_train = pd.read_pickle('data.pkl')
do_preprocess = True
if do_preprocess:
    df_train = preprocess(df_train)
```

## Workflow Using oryxflow Tasks

In a oryxflow workflow, you define your own task classes and then execute the workflow by running the final downstream task which will automatically run required upstream dependencies.

The function-based workflow example will transform to this:

```python
import oryxflow
import pandas as pd

class TaskGetData(oryxflow.tasks.TaskPqPandas):

    # no dependency

    def run(self): # from `def get_data()`
        data = pd.read_csv('rawdata.csv')
        data = clean(data)
        self.save(data) # save output data

class TaskProcess(oryxflow.tasks.TaskPqPandas):
    do_preprocess = oryxflow.BoolParameter(default=True) # optional parameter

    def requires(self):
        return TaskGetData() # define dependency

    def run(self): 
        data = self.inputLoad() # load input data
        if self.do_preprocess:
            data = scale(data) # # from `def preprocess(data)`
        self.save(data) # save output data

flow = oryxflow.Workflow(TaskProcess)
flow.run() # execute task with dependencies
data = flow.outputLoad() # load output data
```

Learn more about [Writing and Managing Tasks](tasks.md) and [Running Workflows](run.md).

!!! tip

    The Claude Code plugin automates this transition: describe your existing script in plain language and it creates the task classes and wires the `@oryxflow.requires` dependencies. See [Build with Claude Code](claude-plugin/index.md).

## Design Pattern Templates for Machine Learning Workflows

For a larger real-life project layout, run [`/oryxflow:init-project`](claude-plugin/commands.md) in Claude Code — it scaffolds the tasks, parameters, flow, config, and supporting folders for you. Already have a messy project rather than a blank one? See [Migrate a messy notebook project](migrate-notebook-to-pipeline.md).
