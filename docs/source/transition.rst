Transition to oryxflow
==============================================

Most data-science code starts as a script: a chain of functions that read a file, transform it,
and write the next file, wired together by hand at the bottom. It works until it doesn't — you
change one step and have to remember which downstream files are now stale, you re-run the whole
thing (including the slow data pull) just to test a small change, and six months later you can't
tell which parameters produced which output.

oryxflow turns that script into a pipeline of **tasks** and takes over the bookkeeping. You get
three things you were doing in your head before:

* **No wasted recomputation** — a task that has already produced its output is skipped, so
  re-running the pipeline only runs what actually changed (a small edit no longer re-pulls the raw
  data).
* **Reproducibility** — every output is tied to the task and parameters that produced it, so you
  always know how a result was made and can reproduce it exactly.
* **Automatic parameter management** — change a parameter and oryxflow reruns exactly the tasks
  that depend on it, and keeps the outputs for each parameter set side by side.

Current Workflow Using Functions
------------------------------------------------------------

Your code currently probably looks like the example below. How do you turn it into a oryxflow workflow?

.. code-block:: python

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


Workflow Using oryxflow Tasks
------------------------------------------------------------

In a oryxflow workflow, you define your own task classes and then execute the workflow by running the final downstream task which will automatically run required upstream dependencies. 

The function-based workflow example will transform to this:

.. code-block:: python

    import oryxflow
    import pandas as pd

    class TaskGetData(oryxflow.tasks.TaskPqPandas):

        # no dependency

        def run(): # from `def get_data()`
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

Learn more about :doc:`Writing and Managing Tasks <../tasks>` and :doc:`Running Workflows <../run>`.

.. tip::

   The Claude Code plugin automates this transition: describe your existing
   script in plain language and it creates the task classes and wires the
   ``@oryxflow.requires`` dependencies. See
   :doc:`Using oryxflow with Claude Code <claude-plugin>`.


Design Pattern Templates for Machine Learning Workflows
------------------------------------------------------------

See code templates for a larger real-life project at https://github.com/d6t/d6tflow-template. Clone & code!
