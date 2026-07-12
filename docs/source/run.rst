Running Tasks and Managing Workflows
==============================================

A workflow object is used to orchestrate tasks and define a task pipeline.

Define a workflow object
------------------------------------------------------------

Workflow object can be defined by passing the default task and the parameters for the pipeline. Both the arguments are optional.

.. code-block:: python

    flow = oryxflow.Workflow(Task1, params)
    flow = oryxflow.Workflow(Task1) # use default params

Note you want to pass the task definition, not an instantiated task.

.. code-block:: python

    import tasks
    flow = oryxflow.Workflow(tasks.Task1) # yes
    flow = oryxflow.Workflow(tasks.Task1()) # no

Previewing Task Execution Status
------------------------------------------------------------

Running a task will automatically run all the upstream dependencies. Before running a workflow, you can preview which tasks will be run.

.. code-block:: python

    flow.preview() # default task
    flow.preview(TaskTrain) # single task
    flow.preview([TaskPreprocess,TaskTrain]) # multiple tasks

Running Multiple Tasks as Workflows
------------------------------------------------------------

To run all tasks in a workflow, run the downstream task you want to complete. It will check if all the upstream dependencies are complete and if not it will run them intelligently for you. 

.. code-block:: python

    flow.run() # default task
    flow.run(TaskTrain) # single task
    flow.run([TaskPreprocess,TaskTrain]) # multiple tasks

If your tasks are already complete, they will not rerun. To force rerunning of all tasks but there are better alternatives, see below.

.. code-block:: python

    flow.run(forced_all_upstream=True, confirm=False) # use flow.reset() instead


Run and Load in One Call
------------------------------------------------------------

For quick scripts and notebooks, ``oryxflow.runLoad`` builds a workflow, runs the task (with all upstream dependencies) and returns its loaded output in a single call - saving you from creating a ``Workflow`` object just to fetch one result.

.. code-block:: python

    # equivalent to: oryxflow.Workflow(TaskTrain, params).run() then outputLoad()
    model = oryxflow.runLoad(TaskTrain, params={'do_preprocess': True})

    # reset=True forces a rerun first (for a data/input change or a suspect cache;
    # for a *code* change, bump the task's code_version instead — see "Handling Code Change")
    df = oryxflow.runLoad(TaskPreprocess, params={'do_preprocess': True}, reset=True)

    # runIt runs without loading the output (same as runLoad(..., load=False))
    oryxflow.runIt(TaskTrain)


How is a task marked complete?
------------------------------------------------------------

This is the mechanism behind "don't recompute what's already done" — the thing that lets you
re-run a pipeline freely and only pay for what changed. Tasks are complete when task output
exists. This is typically the existance of a file, database table or cache. See :doc:`Task I/O Formats <../targets>` how task output is stored to understand what needs to exist for a task to be complete.

.. code-block:: python

    flow.get_task().complete() # status
    flow.get_task().output().path # where is output saved?
    flow.get_task().output()['output1'].path # multiple outputs

A task with a ``code_version`` set carries one more completeness condition: its stored *code
fingerprint* must still match its current code. Bump ``code_version`` — or edit the code and let
the staleness advisory catch it — and the task is no longer complete even though its output file
is still on disk, so "the output exists" never silently masks a code change. Tasks without a
``code_version`` behave exactly as described here. Be honest about the limit: the fingerprint sees
your task code and the project-local modules it imports, but **not** data-file contents or
external APIs — a cache hit is not proof of freshness for those (reset is the verb there). See
:ref:`Code changes: bump code_version <code-versioning>` for the full model.

Task Completion with Parameters
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If a task has parameters, it needs to be run separately for each parameter to be complete when using different parameter settings. The `oryxflow.WorkflowMulti` helps you do that

.. code-block:: python

    flow = oryxflow.WorkflowMulti(Task1, {'flow1':{'preprocess':False},'flow2':{'preprocess':True}})
    flow.run() # will run all flow with all parameters

Disable Dependency Checks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

By default, for a task to be complete, it checks if all dependencies are complete also, not just the task itself. To check if just the task is complete without checking dependencies, set ``oryxflow.settings.check_dependencies=False``

.. code-block:: python

    flow.reset(TaskGetData, confirm=False)
    oryxflow.settings.check_dependencies=True # default
    flow.preview() # TaskGetData is pending so all tasks are pending
    '''
    +--[TaskTrain-{'do_preprocess': 'True'} (PENDING)]
       +--[TaskPreprocess-{'do_preprocess': 'True'} (PENDING)]
          +--[TaskGetData-{} (PENDING)]
    '''
    oryxflow.settings.check_dependencies=False # deactivate dependency checks
    flow.preview()
    +--[TaskTrain-{'do_preprocess': 'True'} (COMPLETE)]
       +--[TaskPreprocess-{'do_preprocess': 'True'} (COMPLETE)]
          +--[TaskGetData-{} (PENDING)]
    oryxflow.settings.check_dependencies=True # set to default


Debugging Failures
------------------------------------------------------------

If a task fails, oryxflow raises a ``RuntimeError`` chained to the original error that caused
the failure (``... the direct cause of the following exception ...``). Read the FIRST
traceback -- the line in your task's ``run()`` is the real cause. Example::

    File "tasks.py", line 37, in run     <== the real error is here
        1/0
    ZeroDivisionError: division by zero

    The above exception was the direct cause of the following exception:
    ...
    RuntimeError: Exception found running flow, check trace

Tips:

* The first traceback (the ``ZeroDivisionError`` above) points at your bug; the trailing
  ``RuntimeError`` is just oryxflow reporting that the flow aborted.
* Set a breakpoint in the task's ``run()`` and step through it.
* Run a single task in isolation to debug it directly: ``TaskTrain().run()``
  (note: this skips dependency resolution -- make sure upstream outputs already exist).
* Turn on engine logging to see which task failed and timing:
  ``oryxflow.enable_logging()`` (see "Logging" below).
* Every failure is also recorded durably in the event stream: ``oryxflow.events.status()`` returns
  recent failures (with the error and a bounded traceback) even after the script has exited, so a
  post-mortem doesn't depend on still having the run's stdout. See
  :ref:`Managing Complex Workflows <managing-complex-workflows>`.


Rerun Tasks When You Make Changes
------------------------------------------------------------

You have several options to force tasks to reset and rerun. See sections below on how to handle parameter, data and code changes.

.. tip::

   Editing a task's code with unchanged parameters would otherwise let the cache
   serve the stale output. The modern fix is to bump the task's ``code_version``
   in the same edit — the task and everything downstream then recompute (see
   :ref:`Code changes: bump code_version <code-versioning>`), and if you forget,
   the staleness advisory warns you. The :doc:`Claude Code plugin <claude-plugin>`
   does this for you: when it edits a task it bumps ``code_version`` so your change
   actually takes effect.

.. code-block:: python

    # preferred way: reset single task, this will automatically run all upstream dependencies
    flow.reset(TaskGetData, confirm=False) # remove confirm=False to avoid accidentally deleting data

    # force execution including upstream tasks
    flow.run([TaskTrain()],forced_all=True, confirm=False)

    # force run everything
    flow.run(forced_all_upstream=True, confirm=False)


Which reset method: ``reset`` / ``reset_upstream`` / ``reset_downstream``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

All three are available on both ``Workflow`` and ``WorkflowMulti``. Each invalidates task
outputs so the next ``run()`` recomputes them; pick by *how much* of the DAG you want to reset:

* ``flow.reset(task)`` — **one task.** Invalidate just this task's output. The next run
  recomputes it and — because ``complete()`` is recursive — anything downstream of it; upstream
  tasks stay complete and are reused. The everyday choice.

* ``flow.reset_upstream(anchor)`` — **the whole upstream cone.** Invalidate ``anchor`` and every
  task it transitively depends on. Add ``only=Family`` (or a list of families) to reset *only*
  those families within the cone: the traversal still walks the full upstream to discover the
  instances, but only the matching ones are invalidated.

  .. code-block:: python

      flow.reset_upstream(Sector)                          # reset everything upstream (leaf included)
      flow.reset_upstream(Sector, only=CountryFeatures)    # reset just this family across the cone
      flow.reset_upstream(Sector, only=[CountryFeatures, DataLoadState])   # multiple families

  ``only=`` matches by **family/type**, which is what lets it reach tasks deep in the DAG whose
  params are *internal* (e.g. a per-``country``/``state`` task you can't easily name from the
  flow's params) — and the families need not be adjacent. This is the tool for "reset the derived
  layers everywhere but keep the expensive source task." See :doc:`advtasksdyn` for the
  hierarchical example it comes from. On a ``WorkflowMulti`` you can omit ``anchor`` — it defaults
  to the flow's default task, so ``flow.reset_upstream(only=CountryFeatures)`` resets that family
  across every flow.

* ``flow.reset_downstream(task, task_downstream=None)`` — **a task/family and everything
  downstream of it.** Only the *family* of ``task`` is used (pass the class), so — like ``only=``
  — it reaches deep tasks whose params are internal to the DAG without naming instances.
  ``task_downstream`` is the terminal task the walk stops at and **defaults to the flow's default
  task**. Every task on the paths between them is invalidated **explicitly** (each output
  deleted), so the downstream recomputes even when the recursive ``complete()`` cascade is
  unavailable. Tasks upstream of the named family (the expensive source) are left intact.

  .. code-block:: python

      flow.reset_downstream(CountryFeatures)                 # CountryFeatures + all downstream, up to the flow root
      flow.reset_downstream(CountryFeatures, Sector)         # explicit terminal task
      flow.reset_downstream([CountryFeatures, CountryRisk])  # several families + their downstream, one call

``reset_upstream(root, only=F)`` vs ``reset_downstream(F)`` — both target a family without naming
instances, but differ in *what* they reset and whether they lean on the cascade:

* ``reset_upstream(root, only=CountryFeatures)`` invalidates **only** the ``CountryFeatures``
  instances; ``Sector`` (downstream) recomputes on the next run *via recursive* ``complete()``.
  Use it when the cascade is reliable (the default, ``check_dependencies=True``).
* ``reset_downstream(CountryFeatures)`` invalidates ``CountryFeatures`` **and everything
  downstream** explicitly, so it does not depend on the cascade. Use it when you want to be
  certain, or where the cascade can't be trusted (``check_dependencies=False``).

Mental model: ``reset`` = one node; ``reset_upstream`` = the cone *above* a node (optionally
filtered to families; downstream recompute relies on the cascade); ``reset_downstream`` = a
node/family and everything *below* it down to a terminal task (invalidated explicitly).


When to reset and rerun tasks?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Three things make a cached result out of date, and each has its *own* right verb — reset is only
one of them:

* **parameters changed** → nothing to do; a new parameter is a new identity and reruns
  automatically, keeping the outputs for each parameter set side by side.
* **code changed** (this task's ``run()`` or a helper it imports) → **bump** ``code_version`` so
  the task and everything downstream recompute. Don't hand-chain resets for code changes.
* **data or an external input changed** (a raw file, an API response — things the code
  fingerprint can't see) → **reset** the task that ingests it.

The full "which verb, when" decision table is in
:ref:`Managing Complex Workflows <managing-complex-workflows>`. The sections below cover each case.

Handling Parameter Change
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

As long as the parameter is defined in the task, oryxflow will automatically rerun tasks with different parameters. 

.. code-block:: python

    flow = oryxflow.WorkflowMulti(Task1, {'flow1':{'preprocess':False},'flow2':{'preprocess':True}})
    flow.run() # executes 2 flows, one for each task

For oryxflow to intelligently figure out which tasks to rerun, the parameter has to be defined in the task. The downstream task (`TaskTrain`) has to pass on the parameter to the upstream task (`TaskPreprocess`).

.. code-block:: python

    class TaskGetData(oryxflow.tasks.TaskPqPandas):
    # no parameter dependence

    class TaskPreprocess(oryxflow.tasks.TaskCachePandas):  # save data in memory
        do_preprocess = oryxflow.BoolParameter(default=True) # parameter for preprocessing yes/no

    @oryxflow.requires(TaskPreprocess)
    class TaskTrain(oryxflow.tasks.TaskPickle):
        # pass parameter upstream
        # no need for to define it again: do_preprocess = oryxflow.BoolParameter(default=True)


See [oryxflow docs for handling parameter inheritance](https://oryxflow.readthedocs.io/en/stable/api/oryxflow.util.html#using-inherits-and-requires-to-ease-parameter-pain)

Default Parameter Values in Config
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

As an alternative to inheriting parameters, you can define defaults in a config files. When you change the config it will automatically rerun tasks.

.. code-block:: python

    class TaskPreprocess(oryxflow.tasks.TaskCachePandas):  
        do_preprocess = oryxflow.BoolParameter(default=cfg.do_preprocess) # store default in config


Handling Data Change
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A raw data file or an external API response is invisible to oryxflow — no parameter and no code
fingerprint moves, so nothing reruns on its own. When you know an input changed, ``reset()`` the
task that *ingests* it (the loader/source task) so the recompute starts where the new data enters
and cascades downstream. Resetting a task further downstream would just reload the same cached old
input.

Handling Code Change
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Prefer ``code_version`` over a manual reset: bump the task's ``code_version`` in the same edit as
the logic change and the task *and everything downstream* recompute on the next run, with no
resets to chain. Forget to bump and oryxflow's staleness advisory warns you (it hashes the task
and the project-local modules it imports, ignoring comment/formatting-only edits). Reset stays
valid — it recomputes regardless — but it is per-task and doesn't propagate the way a bump does.
See :ref:`Code changes: bump code_version <code-versioning>` for the full workflow, the staleness
warning and its three exits (bump / ``accept_code`` / reset), and ``keep_versions`` for keeping
old versions side by side.

Forcing a Single Task to Run
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

You can always run single tasks by calling the `run()` function. This is useful during debugging. However, this will only run this one task and not take care of any downstream dependencies.

.. code-block:: python

    # forcing execution
    flow.get_task().run()
    # or
    TaskTrain().run()

Hiding Execution Output
------------------------------------------------------------

By default, the workflow execution summary is shown, because it shows important information which tasks were run and if any failed. At times, eg during deployment, it can be desirable to not show the execution output.

.. code-block:: python

    oryxflow.settings.execution_summary = False # global
    # or
    flow.run(execution_summary=False) # at each run

Logging
------------------------------------------------------------

oryxflow can log engine activity (task start/complete, timing, failures) and gives each task a
contextual ``self.logger`` for logging from inside your own ``run()``. Logging is disabled by
default. Quick start:

.. code-block:: python

    import oryxflow
    oryxflow.enable_logging()                 # INFO+ to stderr
    oryxflow.enable_logging(level="DEBUG")    # also I/O, cached-skips, dependency detail
    oryxflow.disable_logging()                # silence again

See :doc:`logging` for the full guide, including ``self.logger``, log levels, and routing
oryxflow records into your application's own loguru sinks.

Cloud Storage
------------------------------------------------------------

Point your pipeline at cloud storage and the whole team reads and writes the same outputs — no
one re-runs a task someone else already ran, and results are backed up off your laptop. By
default task output is written under the local data directory (``oryxflow.set_dir()``). You can instead store output in cloud storage (S3, GCS, etc.) - oryxflow uses `fsspec <https://github.com/fsspec>`_ / `universal-pathlib <https://pypi.org/project/universal-pathlib/>`_ under the hood, so task code does not change.

Install the relevant extra first, e.g. ``pip install oryxflow[gcs]`` or ``pip install oryxflow[s3]`` (``cloud-base`` for other fsspec protocols), then enable it once before running:

.. code-block:: python

    import oryxflow

    # Google Cloud Storage shortcut
    oryxflow.enable_gcs(bucket='my-bucket', prefix='myproject')

    # any fsspec protocol (s3, gcs, dropbox, ...)
    oryxflow.enable_cloud_storage(protocol='s3', bucket='my-bucket', prefix='myproject')

    flow = oryxflow.Workflow(TaskTrain)
    flow.run()   # task output now reads/writes under s3://my-bucket/myproject/

``prefix`` is optional and behaves like a top-level folder within the bucket.
