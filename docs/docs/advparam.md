# Advanced: Parameters

Intelligent parameter management is one of the most powerful features of oryxflow. Parameters are how you try different settings — a preprocessing flag, a model choice, a date range — without copying files or renaming outputs by hand. Give a task parameters and oryxflow keeps a **separate cached output per parameter set**, so you can compare runs side by side and switch between them instantly; change a parameter and it reruns exactly the tasks that depend on it and leaves the rest untouched. This is what makes experimentation cheap. New users often have questions on parameter management, this is an important section to read.

## Specifying parameters

Tasks can take any number of parameters.

```python
import datetime

class TaskTrain(oryxflow.tasks.TaskPqPandas):
    do_preprocess = oryxflow.BoolParameter(default=True)
    model = oryxflow.Parameter(default='xgboost')
```

## Running tasks with parameters

Just pass the parameters values, everything else is the same.

```python
oryxflow.Workflow(TaskTrain).run() # use default do_preprocess=True, model='xgboost'
oryxflow.Workflow(TaskTrain, dict(do_preprocess=False, model='nnet')).run() # specify non-default parameters
# or
params = dict(do_preprocess=False, model='nnet')
oryxflow.Workflow(TaskTrain, params).run() # specify non-default parameters
```

Note that you can pass parameters for upstream tasks directly to the terminal task, they will be automatically passed to upstream tasks. See below for details.

## Loading Output Data with Parameters

If you are [using parameters](advparam.md) this is how you load outputs. Make sure you run the task with that parameter first.

```python
df = oryxflow.Workflow(TaskTrain).outputLoad() # load data with default parameters
params = dict(do_preprocess=False, model='nnet')
df = oryxflow.Workflow(TaskTrain, params).outputLoad() # specify non-default parameters
```

## Parameter types

Parameters can be typed.

```python
import datetime

class TaskTrain(oryxflow.tasks.TaskPqPandas):
    do_preprocess = oryxflow.BoolParameter(default=True)
    dt_start = oryxflow.DateParameter(default=datetime.date(2010,1,1))
    dt_end = oryxflow.DateParameter(default=datetime.date(2020,1,1))

    def run(self):
        if self.do_preprocess:
            if self.dt_start>datetime.date(2010,1,1):
                pass
```

For the full list of parameter types and their options, see the [API reference](reference.md).

## Avoid repeating parameters in every class

You often need to pass parameters between classes. With oryxflow, you do not need to repeat parameters in every class, they are automatically managed, that is they are automatically passed to upstream tasks from downstream tasks.

```python
class TaskTrain(oryxflow.tasks.TaskPqPandas):
    do_preprocess = oryxflow.BoolParameter(default=True)
    dt_start = oryxflow.DateParameter(default=datetime.date(2010,1,1))
    dt_end = oryxflow.DateParameter(default=datetime.date(2020,1,1))
    # ...

@oryxflow.requires(TaskTrain) # automatically inherits parameters
class TaskEvaluate(oryxflow.tasks.TaskPickle):

    # requires() is automatic
    # do_preprocess => inherited from TaskTrain
    # dt_start => inherited from TaskTrain
    # dt_end => inherited from TaskTrain

    def run(self):
        print(self.do_preprocess) # inherited
        print(self.dt_start) # inherited

oryxflow.Workflow(TaskEvaluate, {'do_preprocess': False}).preview()  # specify non-default parameters
'''
+--[TaskEvaluate-{'do_preprocess': 'False', 'dt_start': '2010-01-01', 'dt_end': '2020-01-01'} (PENDING)]
+--[TaskTrain-{'do_preprocess': 'False', 'dt_start': '2010-01-01', 'dt_end': '2020-01-01'} (PENDING)] => automatically passed upstream
'''
```

Note that you can pass parameters for upstream tasks directly to the terminal task, they will be automatically passed to upstream tasks. <span class="title-ref">do_preprocess=False</span> will be passed down from <span class="title-ref">TaskEvaluate</span> to <span class="title-ref">TaskTrain</span>.

If you require multiple tasks, you can inherit parameters from those tasks. <span class="title-ref">TaskEvaluate</span> depends on both <span class="title-ref">TaskTrain</span> and <span class="title-ref">TaskPredict</span>.

```python
class TaskTrain(oryxflow.tasks.TaskPqPandas):
    do_preprocess = oryxflow.BoolParameter(default=True)

class TaskPredict(oryxflow.tasks.TaskPqPandas):
    dt_start = oryxflow.DateParameter(default=datetime.date(2010,1,1))
    dt_end = oryxflow.DateParameter(default=datetime.date(2020,1,1))

@oryxflow.requires(TaskTrain,TaskPredict) # inherit all params from input tasks
class TaskEvaluate(oryxflow.tasks.TaskPickle):
    # do_preprocess => inherited from TaskTrain
    # dt_start => inherited from TaskPredict
    # dt_end => inherited from TaskPredict

    def run(self):
        print(self.do_preprocess) # inherited from TaskTrain
        print(self.dt_start) # inherited from TaskPredict

oryxflow.Workflow(TaskEvaluate, {'do_preprocess': False}).preview()  # specify non-default parameters
'''
+--[TaskEvaluate-{'do_preprocess': 'False', 'dt_start': '2010-01-01', 'dt_end': '2020-01-01'} (PENDING)]
   |--[TaskTrain-{'do_preprocess': 'False'} (PENDING)] => automatically passed upstream
   +--[TaskPredict-{'dt_start': '2010-01-01', 'dt_end': '2020-01-01'} (PENDING)] => automatically passed upstream
'''
```

<span class="title-ref">@oryxflow.requires</span> also works with aggregator tasks.

```python
@oryxflow.requires(TaskTrain,TaskPredict) # inherit all params from input tasks
class TaskEvaluate(oryxflow.tasks.TaskAggregator):
    pass
```

For another ML example, see [Example (ML)](example-ml.md).

For more details, see the [API reference](reference.md).

A project scaffolded with [`/oryxflow:init-project`](claude-plugin/commands.md) wires parameter inheritance in for you.

## Avoid building a flow inside a task

When you need one expensive task per country and then a report that combines them, it is tempting to loop over the countries inside the combining task and run a flow for each one:

```python
# avoid this
class Report(oryxflow.tasks.TaskJson):

    def run(self):
        summaries = {}
        for country in cfg.COUNTRIES:
            flow = oryxflow.Workflow(CountrySummary, params={'country': country})
            flow.run()
            summaries[country] = flow.outputLoad(CountrySummary)
        self.save(summaries)
```

This runs, and the answer is right, which is why it survives. What you give up is everything oryxflow does *around* running:

- **`preview()` doesn't show the per-country tasks**, so you can't see how many are pending before you start — the count that matters when each one is a slow API call.
- **They don't appear in the run summary either.** Nothing tells you how many actually ran versus came from cache.
- **Targeted resets silently do nothing.** `flow.reset_upstream(Report, only=CountrySummary)` and `flow.reset_downstream(CountrySummary, Report)` both report no error and invalidate nothing — the per-country tasks aren't in the report's dependencies, so there is nothing for them to find. You change how a summary is written, reset "just that step", re-run, and get the old text back with no indication anything was skipped.

A full `flow.reset_upstream(Report)` may still catch them, but only by accident — it works when the per-country task happens to share an upstream task that *was* invalidated. Not something to rely on.

Declare them as dependencies instead and all three come back:

```python
# better
@oryxflow.requires_each(CountrySummary, country=cfg.COUNTRIES)
class Report(oryxflow.tasks.TaskJson):

    def run(self):
        self.save(self.inputLoad())
```

Now `preview()` lists every country, the run summary counts them, and every reset — targeted or not — reaches them.

The report usually needs something shared as well — the table the summaries were written from, a benchmark to compare them against. Stack a second decorator for it, and `flatten=False` keeps the two apart in `run()`:

```python
@oryxflow.requires({'input': ReportInput})
@oryxflow.requires_each(CountrySummary, country=cfg.COUNTRIES)
class Report(oryxflow.tasks.TaskJson):

    def run(self):
        deps = self.inputLoad(flatten=False)
        self.save({'drivers': deps['input'], 'by_country': deps['CountrySummary']})
```

When the country list is computed from the report's own parameters rather than fixed, pass a function instead of a list; all of these forms are covered in [Dynamic Workflow Generation](advtasksdyn.md).

!!! warning "Check where the old nested flow was writing before you convert"

    The inner `oryxflow.Workflow(...)` above was built with no `path` or `env`, so its outputs went to the default `data/`. The `Report` you convert to probably runs in a flow that *does* set one — `oryxflow.Workflow(Report, path=..., env='prod')` — and a task's outputs live under its flow's directory. Convert without checking and the per-country work is looked for somewhere it was never written: the cache looks empty and every country re-runs.

    Nothing warns you, because "no output at that path" is indistinguishable from "never ran". If the per-item work is expensive — an LLM call, a paid API — that silence has a price. So before converting, find where the nested flow actually wrote, and either move those outputs under the outer flow's directory or accept the one-off recompute knowingly.

    The reverse is worth noticing too: a nested flow with no `env=` was writing to one directory *regardless of the environment the outer flow was running in*, so `prod` and `dev` were sharing those outputs. Converting is what fixes that — the re-run is the cost of un-sharing them.

## Avoid repeating parameters when referring to tasks

To run tasks and load their output for different parameters, you have to pass them to the task. Instead of hardcoding them each time, it is best to keep them in a dictionary and pass that to the task.

```python
# avoid this
flow = oryxflow.Workflow(TaskTrain, dict(do_preprocess=False, model='nnet'))
flow.run()
flow.outputLoad()

# better
params = dict(do_preprocess=False, model='nnet')
flow = oryxflow.Workflow(TaskTrain, params)
flow.run()
flow.outputLoad()
```
