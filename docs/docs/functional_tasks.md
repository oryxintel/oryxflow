# Functional Tasks

## What are functional tasks?

Functional tasks are meant to provide a nice decorator based way of defining tasks.

## How to create a functional task?

For defining our tasks we will need to first define a <span class="title-ref">Workflow()</span> object.

```python
from oryxflow.functional import Workflow
flow = Workflow()
```

Each function is decorated with a <span class="title-ref">flow.task</span> decorator - that takes a <span class="title-ref">oryxflow.tasks.TaskName</span> as parameter

```python
@flow.task(oryxflow.tasks.TaskPqPandas)
def your_functional_task(task):
    print("Running a complicated task!!")
```

You might have noticed we provide a <span class="title-ref">task</span> parameter to the function above.

This is deliberate.

If you have worked with oryxflow.task before you would remember having a <span class="title-ref">self</span> parameter passed to <span class="title-ref">run()</span> method.

Here <span class="title-ref">task</span> is exactly that. It contains all methods available in <span class="title-ref">oryxflow.task.Task</span>

## Running a functional task

All functional tasks are run as <span class="title-ref">oryxflow.task</span> under the hood.

So we require to run them as you would run any <span class="title-ref">oryxflow.task</span>

<span class="title-ref">Workflow()</span> object comes with a run method which does exactly that.

```python
flow.run(your_functional_task)
```

Below is a minimal example of functional task that encompasses everything mentioned above.

```python
import oryxflow
from oryxflow.functional import Workflow
import pandas as pd

flow = Workflow()

@flow.task(oryxflow.tasks.TaskCache)
def sample_functional_task(task):
    df = pd.DataFrame({'a':range(3)})
    print("Functional task running!")
    task.save(df)

flow.run(sample_functional_task)
```

## Additional decorators

These decorators are to be decorated after @flow.task

- <span class="title-ref">@flow.persists</span>  
  - Takes in a list of variables that need to be persisted for the flow task.

  - ``` python
    @flow.persists(['a1', 'a2'])
    ```

- <span class="title-ref">@flow.params</span>  
  - Takes in keyword-arguments of parameters and their types to be used in the function body.

  - ``` python
    @flow.params(example_argument=oryxflow.IntParameter(default=42))
    ```

- <span class="title-ref">@flow.requires</span>  
  - Defines dependencies between flow tasks.

  - ``` python
    @flow.requires({"foo": func1, "bar": func2})
    @flow.requires(func1)
    ```

Example -

```python
...
@flow.task(oryxflow.tasks.TaskCache)
@flow.requires({"a":get_data1, "b":get_data2})
@flow.persists(['aa'])
def example_function(task):
    df = task.inputLoad()
    a = df["a"]
    b = df["b"]
    print(a,b)
    output = pd.DataFrame({'a':range(4)})
    task.save({'aa':output})
...
```

## Passing parameters to the <span class="title-ref">run()</span> method

We saw in one of the above section how to run functional tasks.

oryxflow also allows you to pass in parameters to these functions dynamically using <span class="title-ref">@flow.params()</span>

Below is an example of passing a 'multiplier' paramter to a functional task.

```python
@flow.params(multiplier=oryxflow.IntParameter(default=0))
def print_parameter(task):
    print(task.multiplier)

flow.run(print_parameter, params={'multiplier':42})
```

So basically, you define the parameter name and its type with <span class="title-ref">@flow.params</span> and then use the <span class="title-ref">run()</span> method's <span class="title-ref">params</span> to pass in the actual value

## Additional methods

Some of the functions that are in oryxflow are available in the <span class="title-ref">Workflow()</span> object too!

Here's a list of them -

- preview(function)
- outputLoad(function)
- run(functions_as_list)
- reset(function)
- outputLoadAll()

There are also some functions unique to the functional workflow.

- add_global_params(example_argument=oryxflow.IntParameter(default=42))
- resetAll()
- delete(function)
- deleteAll()
