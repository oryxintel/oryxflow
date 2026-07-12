Writing and Managing Tasks
==============================================

What are tasks?
------------------------------------------------------------

A task is one step of your analysis — load the data, clean it, train the model — packaged so
oryxflow can manage it for you. Instead of a loose function plus a hand-written line to read its
input file and another to write its output, you declare what a task **depends on** and what it
**produces**, and the engine handles the rest: it runs upstream steps first, skips anything
already computed, and hands each task its inputs already loaded. Tasks are the main object you
will be interacting with. They let you:

* define input dependency tasks — so you declare the pipeline order once instead of re-wiring it
  every run
* process data
    * load input data from upstream tasks — already loaded, no manual file paths
    * save output data for downstream tasks — so results are cached and reused, not recomputed
* run tasks — the engine runs only what's missing
* load output data — fetch any result by referencing the task that made it

You write your own tasks by inheriting from one of the predefined oryxflow task formats, for example pandas dataframes saved to parquet. Picking the parent class is how you choose the output
format (parquet, CSV, pickle, in-memory, ...) without writing any save/load code yourself — see
:doc:`Targets <../targets>`.

.. code-block:: python

    class YourTask(oryxflow.tasks.TaskPqPandas):

Additional details on how to write tasks is below. To run tasks see :doc:`Running Workflows <../run>`.

Define Upstream Dependency Tasks
------------------------------------------------------------

You can define input dependencies by using a `@oryxflow.requires` decorator which takes input tasks. You can have no, one or multiple input tasks. This may be required when the decorator shortcut does not work.

.. tip::

   The :doc:`Claude Code plugin <claude-plugin>` writes this wiring for you -
   ask it to "add a task that takes ``<Upstream>``'s output" and it emits the
   task class with the correct ``@oryxflow.requires`` decorator.

.. code-block:: python

    # no dependency
    class TaskSingleInput(oryxflow.tasks.TaskPqPandas):
        #[...]

    # single dependency
    @oryxflow.requires(TaskSingleOutput)
    class TaskSingleInput(oryxflow.tasks.TaskPqPandas):
        #[...]

    # multiple dependencies
    @oryxflow.requires({'input1':TaskSingleOutput1, 'input2':TaskSingleOutput2})
    class TaskMultipleInput(oryxflow.tasks.TaskPqPandas):
        #[...]



Process Data
------------------------------------------------------------

You process data by writing a ``run()`` function. This function will take input data, process it and save output data.

.. code-block:: python

    class YourTask(oryxflow.tasks.TaskPqPandas):

        def run(self):
            # load input data
            # process data
            # save data


Load Input Data
------------------------------------------------------------

Input data from upstream dependency tasks can be easily loaded in ``run()``

.. code-block:: python

    # no dependency
    class TaskNoInput(oryxflow.tasks.TaskPqPandas):

        def run(self):
            data = pd.read_csv(oryxflow.settings.dirpath/'file.csv') # data/file.csv

    # single dependency, single output
    @oryxflow.requires(TaskSingleOutput)
    class TaskSingleInput(oryxflow.tasks.TaskPqPandas):
        def run(self):
            data = self.inputLoad()

    # single dependency, multiple outputs
    @oryxflow.requires(TaskMultipleOutput)
    class TaskSingleInput(oryxflow.tasks.TaskPqPandas):
        def run(self):
            data1, data2 = self.inputLoad()  # load all outputs
            # or load just one specific output by its persists name
            data1 = self.inputLoad(keys='output1')
            # equivalent lower-level spelling
            data1 = self.input()['output1'].load()

    # multiple dependencies, single output
    # prefer the named-dict form: you select deps by meaningful name, not by position
    @oryxflow.requires({'input1':TaskSingleOutput1, 'input2':TaskSingleOutput2})
    class TaskMultipleInput(oryxflow.tasks.TaskPqPandas):
        def run(self):
            data1 = self.inputLoad()['input1']
            data2 = self.inputLoad()['input2']
            # or
            data1 = self.inputLoad(task='input1')
            data2 = self.inputLoad(task='input2')

    # multiple dependencies, multiple outputs
    @oryxflow.requires({'input1':TaskMultipleOutput1, 'input2':TaskMultipleOutput2})
    class TaskMultipleInput(oryxflow.tasks.TaskPqPandas):
        def run(self):
            data = self.inputLoad(as_dict=True)
            data1a = data['input1']['output1']
            data1b = data['input1']['output2']
            data2a = data['input2']['output1']
            data2b = data['input2']['output2']
            # or
            data1a, data1b = self.inputLoad()["input1"]
            data2a, data2b = self.inputLoad()["input2"]
            # or
            data1a, data1b = self.inputLoad(task='input1')
            data2a, data2b = self.inputLoad(task='input2')

    # multiple dependencies (positional, without a dictionary), multiple outputs
    # works, but the named-dict form above is preferred — here deps are selected by
    # integer position (0, 1) instead of by name
    @oryxflow.requires(TaskMultipleOutput1, TaskMultipleOutput2)
    class TaskMultipleInput(oryxflow.tasks.TaskPqPandas):
        def run(self):
            data = self.inputLoad(as_dict=True)
            data1a = data[0]['output1']
            data1b = data[0]['output2']
            data2a = data[1]['output1']
            data2b = data[1]['output2']
            # or
            data1a, data1b = self.inputLoad()[0]
            data2a, data2b = self.inputLoad()[1]
            # or
            data1a, data1b = self.inputLoad(task=0)
            data2a, data2b = self.inputLoad(task=1)

Load External Files
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You probably want to load external data which is not the output of a task. There are a few options.

.. code-block:: python

    class TaskExternalData(oryxflow.tasks.TaskPqPandas):

        def run(self):

            import pandas as pd
            # read from oryxflow data folder
            data = pd.read_parquet(oryxflow.settings.dirpath/'file.pq')

            # totally manual
            data = pd.read_parquet('/some/folder/file.pq')

            # multiple files
            from d6tstack.combine_csv import CombinerCSV
            def do_stuff(df):
                return df
            df = CombinerCSV(glob.glob('*.csv'), apply_after_read=do_stuff).to_pandas)


For more advanced options see :doc:`Sharing Workflows and Outputs <../collaborate>`

Dynamic Inputs
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

See :doc:`Dynamic Tasks <../advtasksdyn>`

Save Output Data
------------------------------------------------------------

Saving output data is quick and convenient. You can save a single or multiple outputs.

.. code-block:: python

    # quick save one output
    class TaskSingleOutput(oryxflow.tasks.TaskPqPandas):

        def run(self):
            self.save(data_output)

    # save more than one output
    class TaskMultipleOutput(oryxflow.tasks.TaskPqPandas):
        persists=['output1','output2'] # declare what you will save

        def run(self):
            self.save({'output1':data1, 'output2':data2}) # needs to match persists

``persist`` (singular) is a backwards-compatible alias for ``persists``; prefer ``persists``.

When you have multiple outputs and don't declare ``persists`` you will get ``raise ValueError('Save dictionary needs to consistent with Task.persist')``


Where Is Output Data Saved?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Output data by default is saved in ``data/``, you can check with

.. code-block:: python

    oryxflow.settings.dirpath # folder where workflow output is saved
    TaskTrain().output().path # file where task output is saved

You can change where data is saved using ``oryxflow.set_dir('data/')``. See advanced options for :doc:`Sharing Workflows and Outputs <../collaborate>`
Global Data Path can be also changed by including the ``path`` parameter to the Workflow.

Changing Task Output Formats
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

See :doc:`Targets <../targets>`

Running tasks
------------------------------------------------------------

See :doc:`Running Workflows <../run>`

Load Output Data
------------------------------------------------------------

Once a workflow is run and the task is complete, you can easily load its output data by referencing the task.

.. code-block:: python

    data = flow.outputLoad() # load default task output
    data = flow.outputLoad(as_dict=True) # useful for multi output
    data2 = flow.outputLoad(TaskMultipleOutput, as_dict=True) # load another task output
    data2['data1']
    data2['data2']

**Before you load output data you need to run the workflow**. See :doc:`run the workflow <../run>`. If a task has not been run, it will show

::

    raise RuntimeError('Target does not exist, make sure task is complete')
    RuntimeError: Target does not exist, make sure task is complete


Which load method: ``output().load()`` vs ``outputLoad()`` vs ``outputLoadConcat()``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

There are three ways to get at a task's output, lowest- to highest-level. **Prefer the highest
one that fits** — most code should just use ``outputLoad()``.

* ``task.output().load()`` — **lowest level.** ``output()`` returns the *target object* (or a
  dict of targets for a multi-``persists`` task), and ``.load()`` reads it. Reach for this only
  when you want the target itself — its ``.path``, or a deliberate ``.load()``. You index the
  target yourself, e.g. ``task.output()['train'].load()``.
* ``task.outputLoad()`` / ``flow.outputLoad()`` — **the default; use this to fetch results.**
  Returns the *data* directly: a single object for a single-``persists`` task, a list (or a dict
  with ``as_dict=True``) for multiple outputs, and a ``{flow: data}`` dict for ``WorkflowMulti``.
  It also checks the task is complete for you.
* ``flow.outputLoadConcat()`` — **narrow and opt-in;** ``WorkflowMulti`` only. Row-stacks every
  flow's output into **one** DataFrame, tagging each flow's rows with its params. Use it *only*
  when every flow's output is a schema-compatible DataFrame you want combined (the
  iterate-and-aggregate case). It is a separate method on purpose: it collapses the per-flow
  ``{flow: data}`` dict into a single frame — a different operation from ``outputLoad``, and one
  that is meaningless for non-DataFrame outputs like models. See :doc:`advtasksdyn`.

The same three tiers exist on the **input** side inside ``run()``: ``self.input().load()`` (the
target), ``self.inputLoad()`` (the data — the default), and ``self.inputLoadConcat()`` (stack a
task's dependencies into one frame).


Loading Output Data with Parameters
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you are :doc:`using parameters <../advparam>` this is how you load outputs. Make sure you run the task with that parameter first.

.. code-block:: python

    params = {'default_params':{}, 'use_params':{'preprocess':True}}
    flow = oryxflow.WorkflowMulti(TaskSingleOutput, params)
    data = flow.outputLoad() # load default task output
    data['default_params']
    data['use_params']

    # multi output
    data2 = flow.outputLoad(TaskMultipleOutput, as_dict=True) # load another task output
    data2['default_params']['data1']
    data2['default_params']['data2']
    data2['use_params']['data1']
    data2['use_params']['data2']


Putting it all together
------------------------------------------------------------

See full example https://github.com/oryxintel/oryxflow/blob/master/docs/example-ml.md

See real-life project template https://github.com/d6t/d6tflow-template

Advanced: task attribute overrides
------------------------------------------------------------

`persist`: data items to save, see above
`external`: do check dependencies, good for sharing tasks without providing code
`code_version`: bump (str or int) when this task's logic changes so it and everything downstream recompute; see :ref:`code-versioning`
`keep_versions`: with ``code_version`` set, keep old versions at readable ``.../<Task>/v<version>/`` paths
`target_dir`: specify directory
`target_ext`: specify extension  
`save_attrib`: include taskid in filename
