# Workflow

Workflow object is used to orchestrate tasks and define a task pipeline

## Define a workflow object

Workflow object can be defined by passing the parameters and the default task for the pipeline. Both the arguments are optional.

```python
flow = Workflow(task=Task1, params = params)
```

### Defining the flow with just params

To define a workflow object with just parameters:

```python
flow = Workflow(params = params)
```

### Previewing the flow

The pipeline can be previewed for the defined flow and passing the task. If nothing is passed, default task used during the initiation of the flow object is used

```python
flow.preview(Task1)
```

### Running the flow

A list of tasks can run for the defined parameters of the flow. Other arguments that can be passed during the flow are: <span class="title-ref">forced</span>, <span class="title-ref">forced_all</span>,\`forced_all_upstream\`, <span class="title-ref">confirm</span>, <span class="title-ref">workers</span>, <span class="title-ref">abort</span>, <span class="title-ref">execution_summary</span>. Any additional named arguments can also be passed for the task objects.

```python
flow.run(Task1)
```

### Getting the output load for the flow

To get the outputload for a specific task, after running it:

```python
flow.run(Task1)
flow.outputLoad(Task1)
```

### Getting the output load for the flow including upstream tasks

To get the outputload for a specific task along with its upstream tasks, after running it:

```python
flow.run(Task1)
flow.outputLoadAll(Task1)
```

### Reset task for the flow

To reset the task for the flow:

```python
flow.reset(Task1)
```

### Reset downstream tasks for the flow

To reset the task for the flow:

```python
flow.reset_downstream(Task1)
```

### Setting the default task for the flow

To set the default task for the flow:

```python
flow.set_default(Task1)
```

### Getting the task the for flow

A task object can be retrieved by calling the get_task method

```python
flow.get_task(Task1)
```

## Define a multi experiment workflow object

A multi experiment workflow can be defined with multiple flows and separate parameters for each flow and a default task. It is mandatory to define the flows and parameters for each of the flows.

```python
flow2 = oryxflow.WorkflowMulti(params = {'experiment1': {'do_preprocess': False}, 'experiment2': {'do_preprocess': True}}, task=Task1)
```

### Defining the flow with just params

To define a workflow object with just parameters:

```python
flow = WorkflowMulti(params = params)
```

### Constructing the params grid

`params` maps a **flow name** to that flow's `{param: value}` dict, e.g. `{'experiment1': {'do_preprocess': False}, 'experiment2': {'do_preprocess': True}}`. You rarely write that by hand — pass a compact spec and let `WorkflowMulti` expand it.

**One param, many values** — pass a single-key dict whose value is a list; you get one flow per value:

```python
oryxflow.WorkflowMulti(CountryTask, params={'country': ['US', 'UK']})
# -> flows {0: {'country': 'US'}, 1: {'country': 'UK'}}
```

**Several params (cartesian product)** — a multi-key dict of lists expands to every combination, with descriptive string flow names:

```python
oryxflow.WorkflowMulti(TaskTrain, params={'model': ['ols', 'gbm'], 'scale': [False, True]})
# -> flows 'model_ols_scale_False', 'model_ols_scale_True', 'model_gbm_scale_False', ...
```

**Explicit list of param sets** — when the combinations are not a full grid, pass a list (flows are keyed by position):

```python
oryxflow.WorkflowMulti(Task1, params=[{'param1': 1}, {'param1': 2}])
# -> flows {0: {'param1': 1}, 1: {'param1': 2}}
```

For finer control, build the `params` dict yourself with the helpers in `oryxflow.utils` (all take an optional `params_base` merged into every flow):

- `params_generator_single({'a': [1, 2, 3]})` — one flow per value of a single param.
- `params_generator_dictlist({'p1': ['a', 'b'], 'p2': ['c', 'd']})` — cartesian product, integer-keyed.
- `params_generator_df(df)` — one flow per row of a DataFrame (each row's columns become that flow's params); handy when your grid comes from a table.

```python
params_all = oryxflow.utils.params_generator_single({'country': ['US', 'UK']}, {'env': 'prod'})
flow = oryxflow.WorkflowMulti(CountryTask, params=params_all)
```

This is the **top-level** grid — the flows to run. It is distinct from any nested enumeration you index inside a task's `requires()` (see "Hierarchical iterate-and-aggregate" in [advtasksdyn](advtasksdyn.md)), which is your own domain data, not a `WorkflowMulti` grid.

!!! tip

    Projects scaffolded by the [Claude Code plugin](claude-plugin.md) keep this wiring in two conventional homes — the grid in `flow_params.py`, the flow objects in `flow.py` — so experiments stay organized as the grid grows, and the AI extends them in the right place.

### Operations on multi experiment workflow

All the operations like <span class="title-ref">run</span>, <span class="title-ref">preview</span>, <span class="title-ref">outputLoad</span>, <span class="title-ref">outputLoadAll</span>, <span class="title-ref">reset</span>, <span class="title-ref">reset_downstream</span> , <span class="title-ref">get_task</span> can be called for the multi flow object. Each of these functions takes an extra `flow` argument selecting which flow to act on. If `flow` is not passed, the operation is applied to every flow.

```python
flow2.run(Task1, flow = "experiment1")
flow2.preview(Task1, flow = "experiment2")
flow2.get_task(Task1, flow = "experiment1")
flow2.outputLoad(Task1, flow = "experiment1")
flow2.outputLoadAll(Task1, flow = "experiment1")
flow2.reset(Task1, flow = "experiment1")
flow2.reset_downstream(Task1, flow = "experiment1")
```

### Concatenate outputs across flows

To load a task's output for every flow and stack them into a single DataFrame (each flow's rows tagged with that flow's params), use \`outputLoadConcat\`:

```python
flow = oryxflow.WorkflowMulti(CountryTask, params={'country': ['US', 'UK']})
flow.run()
dfall = flow.outputLoadConcat(CountryTask)   # one frame, 'country' column tags each flow
```

The one-liner <span class="title-ref">runIterConcat</span> builds the <span class="title-ref">WorkflowMulti</span>, runs it and returns the concatenated frame in one call:

```python
dfall = oryxflow.runIterConcat(CountryTask, params={'country': ['US', 'UK']})
```
