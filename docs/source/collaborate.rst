Sharing Workflows and Outputs
==============================================

Introduction
------------------------------------------------------------

Handing off analysis is usually painful: you zip up a folder of data files and a separate script,
and the person on the other end has to figure out which file came from which step and how to
regenerate them. Because a oryxflow task bundles the code, its parameters, and its output
together, you can share the **whole reproducible pipeline** — the recipient runs the tasks that
still need running and loads any result by name, no manual file-shuffling.

Common cases where you want to do this:

* data engineers share cleaned, ready-to-use data with data scientists — without re-sending it every time it changes
* vendors sharing data with clients — as a pipeline the client can re-run, not a one-off dump
* teachers sharing data with students — everyone starts from the same reproducible outputs

oryxflow gives you three approaches, from simplest to most advanced — this page covers them in order:

#. **Share the data folder** — version ``data/`` with Git LFS (optionally split by ``env=`` so you
   hand off only what you mean to). The recipient clones and gets the exact outputs.
#. **Share code-free stubs** — ``FlowExport`` hands over the outputs as loadable tasks *without* the
   ``run()`` code that produced them, for when the logic is private.
#. **Bridge separate flows** — ``attach_flow`` lets one flow read another flow's outputs at run
   time, across projects or environments.

Sharing the data itself: Git LFS
------------------------------------------------------------

Because oryxflow writes every task output under ``data/``, the simplest way to share results is to
version that folder alongside your code with `Git LFS <https://git-lfs.com>`_. Then you version
and share your data as easily as your code: a teammate clones the repo and gets the exact datasets
each run produced, so nobody re-runs the expensive tasks just to obtain outputs someone already
computed. This is the recommended approach for most teams.

The :doc:`Claude Code plugin <claude-plugin>` sets this up for you in one step with
``/oryxflow:init-gitlfs`` (it puts ``data/`` under Git LFS and wires the ``.gitattributes``). To
do it by hand, install Git LFS and track the data directory::

    git lfs install
    git lfs track "data/**"
    git add .gitattributes data/
    git commit -m "Track data outputs with Git LFS"

The next section refines this — splitting ``data/`` by ``env=`` so you share only part of it. The
later Export/Import and Attach sections cover the cases Git LFS doesn't: handing off the *task
code* to another project, or reading one flow's outputs from inside another flow.

Separating environments with ``env=``
------------------------------------------------------------

Often you don't want to share *everything* under ``data/`` — production outputs, a colleague's
scratch experiments, and your own dev runs may all live there, and only some of it is worth
handing off. The ``env=`` argument to ``Workflow`` keeps them in separate subfolders so you can be
selective about what gets shared (Git-LFS-track or commit just the one you mean to).

Passing ``env='prod'`` writes all task output under ``data/env=prod/`` instead of ``data/``:

.. code-block:: python

    flow = oryxflow.Workflow(TaskTrain, env='prod')
    flow.run()          # output now under data/env=prod/
    # a separate dev environment, isolated from prod:
    flow_dev = oryxflow.Workflow(TaskTrain, env='dev')
    flow_dev.run()      # output under data/env=dev/

Because each environment is its own directory, you can share just the one you mean to — commit
``data/env=prod/`` and leave ``data/env=dev/`` out — without the environments overwriting each
other or leaking. When you later read these outputs (a plain ``import`` or ``FlowImport``, both
below), pass the same ``env=`` to point at the environment you want:

.. code-block:: python

    flow_prod = oryxflow.Workflow(TaskTrain, env='prod')
    df = flow_prod.outputLoad()     # reads data/env=prod/

Just make sure the environment you point at is the one whose outputs actually exist — reading
``env='prod'`` when the shared data was saved without an ``env`` (plain ``data/``) will find the
tasks incomplete and rerun them.

Sharing outputs without the code (``FlowExport``)
------------------------------------------------------------

The point of exporting is to **share output data without sharing the code that produced it.** Often
the ``run()`` logic is the sensitive part — a proprietary model, a paid data source, an internal
cleaning routine — but the *output* is what a colleague, client, or student actually needs.
``FlowExport`` lets you hand over the results as a first-class oryxflow flow while keeping that
logic private.

It works because the exported file contains only **stub** task definitions. For each task it emits
the parent class (so the output format is known), the ``persists`` names, the ``path``, the
``task_group``, and the parameters — everything needed to *locate and load* the output — plus
``external=True``, which tells oryxflow to treat the output as already-produced and never run the
task. The original ``run()`` body is **not** included. The recipient wires the stubs into a
``Workflow`` (see *Loading shared outputs in your project* below) and calls ``outputLoad()`` to work with your
results through oryxflow — parameter management and all — without ever seeing how they were made.

You can Export your tasks into a new File or print the tasks in the console.
All parameters, paths, task_group will be exported.

.. code-block:: python

    class Task1(oryxflow.tasks.TaskPqPandas):
        def run(self):
            self.save(...)   # your private logic — NOT exported

    @oryxflow.requires(Task1)
    class Task2(oryxflow.tasks.TaskPqPandas):
        def run(self):
            self.save(...)   # your private logic — NOT exported

    flow = oryxflow.Workflow(Task2)

    # This will only export Task 2 to console
    e = oryxflow.FlowExport(tasks=Task2())
    e.generate()

    # This will export All the flow (Task1, Task2) to a file
    e = oryxflow.FlowExport(flows=flow, save=True, path_export='tasks_export.py')
    e.generate()

The generated ``tasks_export.py`` holds stubs like this — note there is no ``run()``:

.. code-block:: python

    import oryxflow
    import datetime

    class Task1(oryxflow.tasks.TaskPqPandas):
        external=True
        persists=['data']

    class Task2(oryxflow.tasks.TaskPqPandas):
        external=True
        persists=['data']

Ship this file together with the ``data/`` directory (the Git-LFS approach above is an easy way),
and the recipient can load every output through oryxflow without the source code.

Loading shared outputs in your project
------------------------------------------------------------

The simplest way to use ``tasks_export.py`` is to drop it into your project next to the shared
``data/`` directory and ``import`` it like any other module. The stubs are ordinary task classes,
so you use them in a standard ``Workflow`` — point oryxflow at the data directory, then load:

.. code-block:: python

    import oryxflow
    import tasks_export                     # the stub file you were given

    oryxflow.set_dir('data/')               # the shared data directory

    flow = oryxflow.Workflow(tasks_export.Task2)
    flow.complete()                         # True — external stub sees the existing output file
    df = flow.outputLoad()                  # load the results, no producer code needed

Because the stubs are ``external=True``, oryxflow treats their output as already-produced: nothing
runs, ``complete()`` is ``True`` as long as the output files are in place, and ``outputLoad()``
just reads them. If the tasks have parameters, pass them as usual to select which output you want
(``oryxflow.Workflow(tasks_export.Task2, {'country': 'US'})``) — the same parameter → path
resolution applies, so you never chase file paths by hand.

Loading shared outputs from another project (``FlowImport``)
------------------------------------------------------------

The plain ``import`` above assumes ``tasks_export.py`` and its ``data/`` sit inside your project.
When the file and data live in **another directory or project**, ``FlowImport`` loads the module
and resolves its data path for you, so you don't have to copy anything in:

.. code-block:: python

    scraper = oryxflow.FlowImport(path='../another-project/', module='tasks_export.py', path_data='data/')
    flow_import = oryxflow.Workflow(scraper.tasks.Task2, path=scraper.dirpath)
    df = flow_import.outputLoad()

``FlowImport`` returns an object exposing the imported task classes under ``.tasks`` and the
resolved data directory under ``.dirpath``; you pass that ``dirpath`` as the workflow's ``path`` so
the flow reads from the other project's ``data/``. It's the same idea as the plain import — just
without moving files between projects.

Reading another flow's outputs (``attach_flow``)
------------------------------------------------------------

In more complex projects, users need to import data from many sources.
Flows can be attached together in order to access the data generated in one flow inside the other.

Attach a flow to a workflow with ``attach_flow(flow, name)``; every task in that workflow then sees
it under ``self.flows[name]``, so a task's ``run()`` can load another flow's output without wiring
an explicit ``requires()`` dependency.

``requires`` vs ``attach_flow``: the defining difference
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

The two mechanisms look similar — both let one task read another's output — but they solve
different problems, and the difference comes down to two things: **whose data root** the read uses,
and **whether oryxflow tracks it**.

A normal ``@oryxflow.requires`` edge assumes both tasks live in **one flow with one data root**.
When you build a ``Workflow``, it pushes its ``path`` (and ``env``) down onto *every* upstream task
instance — that is how the whole DAG agrees on where ``data/`` is. oryxflow then *tracks* that edge:
if the upstream output is missing, it builds it.

``attach_flow`` is the opposite on both counts:

* the attached flow is a **self-contained handle that keeps its own** ``path``/``env``, so
  ``self.flows['x'].outputLoad()`` reads from *that* flow's data location — which can be a different
  project's ``data/`` or a different ``env=``;
* it is **not tracked as a dependency** — oryxflow will not build the source flow for you.

So the mental split is: ``requires`` = "this is part of my pipeline, same data root, build it if
needed"; ``attach_flow`` = "reach into a *separate* flow that has its own lifecycle and data root,
and read what it already produced."

Why not just load it yourself?
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

The obvious alternative is to load the other flow's output in your driver script and pass the
DataFrame into your task. **Don't** — it throws away the one thing oryxflow is for. In oryxflow an
output's location is a deterministic function of *(task, parameters)*: you never type a path, you
ask for a result and the engine computes where it lives. Hand-loading reintroduces exactly the path
bookkeeping the library removes, and it gets worse the moment the source flow is parameterized:

* a source with **no parameters** — you already have to know or hardcode one path;
* a **parameterized** source — now there is a *path per parameter combination*, and to pass the
  right DataFrame you would have to reconstruct the source's ``task_id`` → path mapping yourself for
  every parameter set, and keep it in sync as parameters change. That is reimplementing oryxflow's
  parameter management by hand, in glue code.

``attach_flow`` avoids all of this. Because you attach the flow **object**, not its data, the
"ask by parameters, not paths" property survives across the flow boundary — the attached flow
resolves its own output paths from its own parameters:

.. code-block:: python

    self.flows['scraped'].outputLoad(Task2)                 # single flow: its parameters
    self.flows['scraped'].outputLoad(Task2, flow='2024')    # WorkflowMulti: pick a parameter set by name

You never touch a path, parameterized or not, cross-project or not.

.. code-block:: python

    class Task1(oryxflow.tasks.TaskCachePandas):
        def run(self):
            self.save(pd.DataFrame({'a': [1, 2]}))

    class Task3(oryxflow.tasks.TaskCachePandas):
        def run(self):
            # read the attached flow's default-task output
            temp_flow_df = self.flows['flow'].outputLoad()
            self.save(temp_flow_df)

    # Define both flows and run the source flow first so its output exists
    flow = oryxflow.Workflow(Task1)
    flow2 = oryxflow.Workflow(Task3)
    flow.run()

    # Attach the first flow to the second under the name 'flow', THEN run
    # (attachment is propagated to the tasks when flow2.run() executes)
    flow2.attach_flow(flow, 'flow')
    flow2.run()

    df = flow2.outputLoad()   # Task3's output, sourced from the attached flow

Step by step, here is what actually happens:

#. ``flow.run()`` executes ``Task1`` and saves its output (here into the in-memory cache, because
   ``Task1`` is a ``TaskCachePandas``). At this point ``flow`` is a live ``Workflow`` object that
   knows how to load that output — ``flow.outputLoad()`` would return the DataFrame.
#. ``flow2.attach_flow(flow, 'flow')`` records the whole ``flow`` *object* — not its data — under
   the key ``'flow'`` on ``flow2``. Nothing runs yet; you are just registering that ``flow2`` may
   need to reach into ``flow`` later.
#. ``flow2.run()`` propagates that registration onto the task instances just before executing them:
   every task in ``flow2`` gets ``self.flows = {'flow': flow}``. This is why the attach must come
   *before* the run — a task created and run without it would have ``self.flows`` empty.
#. Inside ``Task3.run()``, ``self.flows['flow']`` is the attached ``flow`` object, so
   ``self.flows['flow'].outputLoad()`` calls ``outputLoad()`` on it and returns ``Task1``'s
   DataFrame. ``Task3`` then saves it as its own output.

The key idea is that the link is between *flows*, resolved lazily at load time — ``Task3`` never
declares ``Task1`` in its ``requires()``. That keeps the two flows independent (separate projects,
separate ``data/`` directories, separate run/reset scopes) while still letting one read the
other's results. Crucially, unlike a ``requires`` edge, the attached flow **keeps its own**
``path``/``env``, so ``self.flows['flow'].outputLoad()`` reads from *that* flow's data root — which
may be a different project's ``data/`` or a different ``env=``. That is what makes it the right tool
for the cross-project case above: import another project's tasks, wrap them in a ``Workflow``
pointed at that project's ``dirpath``, attach it, and read its outputs by parameter without ever
resolving a path.

.. code-block:: python

    # another project's flow, pointed at ITS data root (see FlowImport above)
    scraper = oryxflow.FlowImport(path='../another-project/', module='tasks_export.py', path_data='data/')
    flow_prod = oryxflow.Workflow(scraper.tasks.Task2, path=scraper.dirpath, env='prod')

    # your flow whose task consumes the scraped data
    flow_mine = oryxflow.Workflow(MyTask)
    flow_mine.attach_flow(flow_prod, 'scraped')
    flow_mine.run()          # inside MyTask.run(): self.flows['scraped'].outputLoad(Task2)

Why not just ``@oryxflow.requires(scraper.tasks.Task2)`` on ``MyTask``? Because ``flow_mine`` would
push *your* ``path`` onto the imported task, so it would look for the output under *your* ``data/``
instead of ``../another-project/data/`` — find it missing, and try to rebuild it (needing that
project's raw inputs and code). ``attach_flow`` sidesteps that precisely because the attached flow
retains its own path.

Two trade-offs to know:

* **Not tracked as a dependency.** oryxflow will not build the source flow for you: if the output
  doesn't exist yet, ``self.flows['flow'].outputLoad()`` raises rather than running it, so you must
  run the source flow first (as ``flow.run()`` does above).
* **Attached at a fixed parameter slice.** ``attach_flow`` hands you the source flow configured at
  attach time — clean when the consumer wants a *fixed* slice ("the prod-2024 scrape"). If instead
  you want *each instance* of a parameterized consumer to automatically pull the **matching**
  parameter set from the source (consumer ``country=US`` → source ``country=US``), that per-instance
  coupling is what a real ``@oryxflow.requires`` edge does best — parameters propagate upstream
  automatically and it is tracked and auto-built.

Decision rule:

* **Same project, and the upstream should follow the consumer's parameters** → ``@oryxflow.requires``
  (parameters propagate, tracked, auto-built). Don't reach for ``attach_flow`` to wire tasks that
  belong to *one* pipeline.
* **Separate data root (another project or another** ``env`` **), or an independently-managed flow**
  → ``attach_flow``, which keeps parameter → path resolution across the boundary — the thing manual
  DataFrame passing destroys.