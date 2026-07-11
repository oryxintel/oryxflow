Logging
============================================================

oryxflow uses `loguru <https://loguru.readthedocs.io>`_ for logging. So it never interferes with
your application's own logging, **oryxflow logging is disabled by default** — out of the box
oryxflow emits nothing except the execution summary print. You opt in when you want to see what
the engine is doing or to log from inside your own tasks.

Turning logging on and off
------------------------------------------------------------

.. code-block:: python

    import oryxflow

    oryxflow.enable_logging()                 # INFO and above, written to stderr
    oryxflow.enable_logging(level="DEBUG")    # more detail (see levels below)
    oryxflow.disable_logging()                # silence oryxflow again

    flow = oryxflow.Workflow(MyTask)
    flow.run()

With the default ``sink=sys.stderr`` you get one clean oryxflow log stream: ``enable_logging()``
removes loguru's pristine default stderr handler and installs a oryxflow-filtered one at the
chosen level (so records are not printed twice). Calling it again just replaces that handler
rather than stacking another. ``disable_logging()`` silences oryxflow again at the source.

Color is auto-detected: by default (``colorize=None``) records are colored only when the sink
is an interactive terminal, so redirected or captured output (files, pipes, pytest capture)
stays free of ANSI escape codes. Force it either way with ``enable_logging(colorize=True)`` /
``enable_logging(colorize=False)``.

``enable_logging()`` returns the loguru handler id of the sink it added. Keep it if you want to
remove that specific sink later:

.. code-block:: python

    from loguru import logger
    hid = oryxflow.enable_logging()
    ...
    logger.remove(hid)

What gets logged
------------------------------------------------------------

The default ``enable_logging()`` level is ``INFO``. Each level is cumulative — ``DEBUG`` shows
everything ``INFO`` shows, plus more.

* **INFO** — the "what's happening" stream: task start, task complete (with duration), task and
  dependency failures, run summary, and task invalidation.
* **DEBUG** — adds the verbose detail: skipped (already-complete) tasks, save / load / input
  I/O with their keys, and generator yields.
* **WARNING** — an ``external=True`` task whose output is missing.
* **ERROR** — a task's ``run()`` raised; the full traceback is logged via loguru.

Example INFO output for a two-task flow::

    ... | INFO  | oryxflow.core - task start: Task1 (Task1__99914b932b)
    ... | INFO  | oryxflow.core - task complete: Task1__99914b932b in 0.041s
    ... | INFO  | oryxflow.core - task start: Task3 (Task3__a1b2c3d4e5)
    ... | INFO  | oryxflow.core - task complete: Task3__a1b2c3d4e5 in 0.046s
    ... | INFO  | oryxflow.core - run summary: scheduled=2 ran=2 complete=0 failed=0

At ``DEBUG`` you additionally see the I/O and cached skips::

    ... | DEBUG | oryxflow.tasks - saved Task1__99914b932b keys=['data']
    ... | DEBUG | oryxflow.tasks - loaded input for Task3__a1b2c3d4e5 keys=['input1']
    ... | DEBUG | oryxflow.core - task skipped (already complete): Task1__99914b932b

Logging inside your own tasks
------------------------------------------------------------

Every task has a contextual ``self.logger``: a loguru logger pre-tagged with this task's
``task_id`` and ``task_family``. Use it in your ``run()`` (or any task method) so your messages
carry the task identity automatically:

.. code-block:: python

    class TaskTrain(oryxflow.tasks.TaskPickle):
        def run(self):
            df = self.inputLoad()
            self.logger.info("training on {} rows", len(df))    # tagged task_id / task_family
            model = train(df)
            self.logger.debug("converged in {} iterations", model.n_iter_)
            self.save(model)

``self.logger`` lives in oryxflow's logging namespace (its records show under the name
``oryxflow.task``), so — like the engine logs — it is silent until you call
``oryxflow.enable_logging()`` and is silenced again by ``disable_logging()``, no matter which
module your task class is defined in. The ``task_id`` / ``task_family`` tags are attached to each
record's ``extra`` dict; include them in a custom format to display them:

.. code-block:: python

    from loguru import logger
    oryxflow.enable_logging(level="DEBUG")
    logger.add("flow.log", filter="oryxflow",
               format="{time} | {level} | {extra[task_family]} | {message}")

If you would rather log independently of oryxflow's on/off switch, just use your own
``from loguru import logger`` directly in your task code instead of ``self.logger``.

Routing oryxflow logs into your application's logging
------------------------------------------------------------

By default ``enable_logging()`` takes over loguru's default stderr handler (see above). If your
application configures its own loguru handlers and you want oryxflow's records to flow into *your*
sinks rather than have oryxflow touch any handler, pass ``sink=None`` — it only re-enables the
namespace:

.. code-block:: python

    from loguru import logger
    logger.add("app.log")          # your application's own sink

    import oryxflow
    oryxflow.enable_logging(sink=None)   # re-enable the namespace, add/remove NO handler
    # oryxflow records now go wherever your app's loguru sinks point

Note: with ``sink=None``, if you have your own catch-all sink it will receive oryxflow records at
whatever level *that* sink is set to (``enable_logging``'s ``level=`` only governs the sink it
adds, which it does not add in this mode).

Default log level setting
------------------------------------------------------------

When you call ``enable_logging()`` without a ``level=``, it uses ``oryxflow.settings.log_level``
(default ``'INFO'``). Set it once to change the default for every later ``enable_logging()`` call,
or pass ``level=`` to override per call:

.. code-block:: python

    oryxflow.settings.log_level = 'DEBUG'   # change the global default
    oryxflow.enable_logging()               # now defaults to DEBUG
    oryxflow.enable_logging(level='INFO')   # per-call override still wins
