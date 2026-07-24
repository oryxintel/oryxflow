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

    def run(self):
        yield self.clone(TaskTrain)
        yield self.clone(TaskPredict)
```

For another ML example, see [Example (ML)](example-ml.md).

For more details, see the [API reference](reference.md).

The project template also implements task parameter inheritance <https://github.com/d6t/d6tflow-template>

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
