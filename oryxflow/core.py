"""
Mini-engine: a small, self-contained execution engine for oryxflow. Provides the ``Task`` base
class, target bases, ``flatten``/``getpaths``, deterministic task ids, the
``inherits``/``requires`` decorators, ``find_deps`` and a sequential ``build``.

The execution engine is **sequential**: the DAG is run in dependency order in-process. The
``workers`` argument is accepted for API compatibility but ignored.
"""

import re
import json
import time
import hashlib
import inspect
import traceback

from oryxflow.log import logger, TaskLogger
from oryxflow.parameter import (
    Parameter,
    MissingParameterException,
    UnknownParameterException,
    DuplicateParameterException,
)


# Parameters of the task id. ``settings.set_parameter_len`` tunes these at import time.
TASK_ID_INCLUDE_PARAMS = 3
TASK_ID_TRUNCATE_PARAMS = 16
TASK_ID_TRUNCATE_HASH = 10
TASK_ID_INVALID_CHAR_REGEX = re.compile(r'[^A-Za-z0-9_]')


def flatten(struct):
    """
    Create a flat list of all items in a structured object (dicts, lists, items)::

        >>> sorted(flatten({'a': 'foo', 'b': 'bar'}))
        ['bar', 'foo']
        >>> sorted(flatten(['foo', ['bar', 'troll']]))
        ['bar', 'foo', 'troll']
        >>> flatten('foo')
        ['foo']
        >>> flatten(42)
        [42]
    """
    if struct is None:
        return []
    flat = []
    if isinstance(struct, dict):
        for _, result in struct.items():
            flat += flatten(result)
        return flat
    if isinstance(struct, str):
        return [struct]

    try:
        iterator = iter(struct)
    except TypeError:
        return [struct]

    for result in iterator:
        flat += flatten(result)
    return flat


def getpaths(struct):
    """Map all Tasks in a structured object to their ``.output()``."""
    if isinstance(struct, Task):
        return struct.output()
    elif isinstance(struct, dict):
        return struct.__class__((k, getpaths(v)) for k, v in struct.items())
    elif isinstance(struct, (list, tuple)):
        return struct.__class__(getpaths(r) for r in struct)
    else:
        try:
            return [getpaths(r) for r in struct]
        except TypeError:
            raise Exception('Cannot map %s to Task/dict/list' % str(struct))


def task_id_str(task_family, params):
    """
    Return a canonical, deterministic string identifying a task.

    The id is ``{family}_{param_summary}_{md5(sorted_json)[:10]}`` so that
    ``task_id.split('_')[0]`` yields the task family (the directory convention).

    :param task_family: the task family (class name)
    :param params: dict mapping parameter names to serialized (str) values
    """
    param_str = json.dumps(params, separators=(',', ':'), sort_keys=True)
    param_hash = hashlib.md5(param_str.encode('utf-8')).hexdigest()

    param_summary = '_'.join(p[:TASK_ID_TRUNCATE_PARAMS]
                             for p in (params[p] for p in sorted(params)[:TASK_ID_INCLUDE_PARAMS]))
    param_summary = TASK_ID_INVALID_CHAR_REGEX.sub('_', param_summary)

    return '{}_{}_{}'.format(task_family, param_summary, param_hash[:TASK_ID_TRUNCATE_HASH])


# Memoizes task instances by (class, serialized params). This is load-bearing: ``Workflow``
# propagates per-flow ``path``/``flows`` by mutating attributes on a task instance and relies on
# later ``Task(...)`` calls (eg from ``outputPath``/``FlowExport``) returning that same instance.
# Keyed by all params (significant or not).
_instance_cache = {}


class Register(type):
    """
    Minimal metaclass providing a class-level ``task_family`` property and instance memoization.

    A property defined directly on :py:class:`Task` is only invoked for instances; reading
    ``SomeTaskClass.task_family`` needs this metaclass property (used eg by ``WorkflowMulti``).

    ``__call__`` memoizes instances so two ``Cls(**same_params)`` calls return the identical
    object (and ``__init__`` runs only on the first), preserving the instance identity that
    ``Workflow``'s path/flow propagation depends on.
    """

    @property
    def task_family(cls):
        return cls.__name__

    def __call__(cls, *args, **kwargs):
        try:
            params = cls.get_params()
            param_values = cls.get_param_values(params, args, kwargs)
            param_objs = dict(params)
            key = (cls, tuple((n, param_objs[n].serialize(v)) for n, v in param_values))
            hash(key)
        except Exception:
            # On any trouble building a stable key, fall back to a fresh (uncached) instance.
            return super().__call__(*args, **kwargs)

        inst = _instance_cache.get(key)
        if inst is None:
            inst = super().__call__(*args, **kwargs)
            _instance_cache[key] = inst
        return inst


class Task(metaclass=Register):
    """
    Base class for all oryxflow tasks.

    Subclasses declare :py:class:`~oryxflow.parameter.Parameter` members and override
    :py:meth:`run`, :py:meth:`requires` and :py:meth:`output`.
    """

    @classmethod
    def get_params(cls):
        """Return all ``(name, Parameter)`` pairs for this task, in declaration order."""
        params = []
        for param_name in dir(cls):
            param_obj = getattr(cls, param_name)
            if not isinstance(param_obj, Parameter):
                continue
            params.append((param_name, param_obj))

        params.sort(key=lambda t: t[1]._counter)
        return params

    @classmethod
    def get_param_names(cls, include_significant=False):
        """Return parameter names. ``include_significant=True`` returns all params."""
        return [name for name, p in cls.get_params() if include_significant or p.significant]

    @classmethod
    def get_param_values(cls, params, args, kwargs):
        """
        Resolve parameter values from positional args, kwargs and defaults.

        :param params: list of ``(param_name, Parameter)``
        :param args: positional arguments
        :param kwargs: keyword arguments
        :returns: list of ``(name, value)`` tuples, one per parameter
        """
        result = {}
        params_dict = dict(params)
        task_family = cls.get_task_family()
        exc_desc = '%s[args=%s, kwargs=%s]' % (task_family, args, kwargs)

        # Positional arguments.
        positional_params = [(n, p) for n, p in params if p.positional]
        for i, arg in enumerate(args):
            if i >= len(positional_params):
                raise UnknownParameterException(
                    '%s: takes at most %d parameters (%d given)' % (exc_desc, len(positional_params), len(args)))
            param_name, param_obj = positional_params[i]
            result[param_name] = param_obj.normalize(arg)

        # Keyword arguments.
        for param_name, arg in kwargs.items():
            if param_name in result:
                raise DuplicateParameterException(
                    '%s: parameter %s was already set as a positional parameter' % (exc_desc, param_name))
            if param_name not in params_dict:
                raise UnknownParameterException('%s: unknown parameter %s' % (exc_desc, param_name))
            result[param_name] = params_dict[param_name].normalize(arg)

        # Defaults for anything still unset.
        for param_name, param_obj in params:
            if param_name not in result:
                if not param_obj.has_task_value():
                    raise MissingParameterException(
                        "%s: requires the '%s' parameter to be set" % (exc_desc, param_name))
                result[param_name] = param_obj.task_value()

        return [(param_name, result[param_name]) for param_name, param_obj in params]

    def __init__(self, *args, **kwargs):
        params = self.get_params()
        param_values = self.get_param_values(params, args, kwargs)

        for key, value in param_values:
            setattr(self, key, value)

        self.param_kwargs = dict(param_values)

        self.task_id = task_id_str(self.get_task_family(), self.to_str_params(only_significant=True))
        self.__hash = hash(self.task_id)

    @property
    def task_family(self):
        """The task family (class name) of this task instance."""
        return self.__class__.__name__

    @property
    def logger(self):
        """Contextual logger for task authors; auto-tagged with task identity.

        Lives in the ``oryxflow`` namespace, so it is silent until
        ``oryxflow.enable_logging()`` is called.
        """
        if getattr(self, "_logger", None) is None:
            self._logger = TaskLogger(task_id=self.task_id, task_family=self.task_family)
        return self._logger

    @classmethod
    def get_task_family(cls):
        """The task family (class name) for this class."""
        return cls.__name__

    def to_str_params(self, only_significant=False, only_public=False):
        """
        Convert parameters to a ``{name: serialized_value}`` dict.

        :param bool only_significant: only include parameters marked significant.
        :param bool only_public: accepted for API compatibility; visibility is not modeled.
        """
        params_str = {}
        params = dict(self.get_params())
        for param_name, param_value in self.param_kwargs.items():
            if (not only_significant) or params[param_name].significant:
                params_str[param_name] = params[param_name].serialize(param_value)
        return params_str

    def clone(self, cls=None, **kwargs):
        """
        Create a new task instance from this one, overriding some args.

        Parameters common to this task and ``cls`` are carried over; ``kwargs`` take precedence.
        """
        if cls is None:
            cls = self.__class__

        new_k = {}
        for param_name, param_class in cls.get_params():
            if param_name in kwargs:
                new_k[param_name] = kwargs[param_name]
            elif hasattr(self, param_name):
                new_k[param_name] = getattr(self, param_name)

        return cls(**new_k)

    def __hash__(self):
        return self.__hash

    def __repr__(self):
        params = dict(self.get_params())
        repr_parts = []
        for param_name, param_value in self.param_kwargs.items():
            if params[param_name].significant:
                repr_parts.append('%s=%s' % (param_name, params[param_name].serialize(param_value)))
        return '{}({})'.format(self.get_task_family(), ', '.join(repr_parts))

    def __eq__(self, other):
        return self.__class__ == other.__class__ and self.task_id == other.task_id

    def complete(self):
        """``True`` if all of this task's outputs exist."""
        import warnings
        outputs = flatten(self.output())
        if len(outputs) == 0:
            warnings.warn(
                "Task %r without outputs has no custom complete() method" % self,
                stacklevel=2,
            )
            return False
        return all(output.exists() for output in outputs)

    def output(self):
        """The output Target(s) this task produces. Default: none."""
        return []

    def requires(self):
        """The Task(s) this task depends on. Default: none."""
        return []

    def input(self):
        """The outputs of the tasks returned by :py:meth:`requires`."""
        return getpaths(self.requires())

    def deps(self):
        """Flattened list of required tasks."""
        return flatten(self.requires())

    def run(self):
        """The task computation, to be overridden in a subclass."""
        pass


class Target:
    """Minimal abstract target base."""

    def exists(self):
        raise NotImplementedError


class LocalTarget(Target):
    """
    Tiny local target base.

    ``self.path`` is stored as-is (NOT coerced to ``str``); subclasses
    (``CacheTarget``/``_LocalPathTarget``) override the rest and normalize the path.
    """

    def __init__(self, path=None):
        self.path = path

    def exists(self):
        return False


# ----------------------------------------------------------------------------------------------
# inherits / requires decorators
# ----------------------------------------------------------------------------------------------

class inherits:
    """
    Copy parameters (and nothing else) from one or more task classes onto the decorated task,
    and add ``clone_parent``/``clone_parents`` helpers. Avoids pythonic inheritance.

    Supports positional tasks (``clone_parents`` returns a list) or named tasks via keyword
    arguments (``clone_parents`` returns a dict).
    """

    def __init__(self, *tasks_to_inherit, **kw_tasks_to_inherit):
        super(inherits, self).__init__()
        if not tasks_to_inherit and not kw_tasks_to_inherit:
            raise TypeError("tasks_to_inherit or kw_tasks_to_inherit must contain at least one task")
        if tasks_to_inherit and kw_tasks_to_inherit:
            raise TypeError("Only one of tasks_to_inherit or kw_tasks_to_inherit may be present")
        self.tasks_to_inherit = tasks_to_inherit
        self.kw_tasks_to_inherit = kw_tasks_to_inherit

    def __call__(self, task_that_inherits):
        task_iterator = self.tasks_to_inherit or self.kw_tasks_to_inherit.values()
        for task_to_inherit in task_iterator:
            for param_name, param_obj in task_to_inherit.get_params():
                if not hasattr(task_that_inherits, param_name):
                    setattr(task_that_inherits, param_name, param_obj)

        if self.tasks_to_inherit:
            def clone_parent(_self, **kwargs):
                return _self.clone(cls=self.tasks_to_inherit[0], **kwargs)
            task_that_inherits.clone_parent = clone_parent

            def clone_parents(_self, **kwargs):
                return [
                    _self.clone(cls=task_to_inherit, **kwargs)
                    for task_to_inherit in self.tasks_to_inherit
                ]
            task_that_inherits.clone_parents = clone_parents
        elif self.kw_tasks_to_inherit:
            def clone_parents(_self, **kwargs):
                return {
                    task_name: _self.clone(cls=task_to_inherit, **kwargs)
                    for task_name, task_to_inherit in self.kw_tasks_to_inherit.items()
                }
            task_that_inherits.clone_parents = clone_parents

        return task_that_inherits


class requires:
    """Same as :py:class:`inherits`, but also auto-defines the ``requires`` method."""

    def __init__(self, *tasks_to_require, **kw_tasks_to_require):
        super(requires, self).__init__()
        self.tasks_to_require = tasks_to_require
        self.kw_tasks_to_require = kw_tasks_to_require

    def __call__(self, task_that_requires):
        task_that_requires = inherits(*self.tasks_to_require, **self.kw_tasks_to_require)(task_that_requires)

        def requires(_self):
            return _self.clone_parent() if len(self.tasks_to_require) == 1 else _self.clone_parents()
        task_that_requires.requires = requires

        return task_that_requires


# ----------------------------------------------------------------------------------------------
# Dependency discovery
# ----------------------------------------------------------------------------------------------

def _get_task_requires(task):
    return set(flatten(task.requires()))


def dfs_paths(start_task, goal_task_family, path=None):
    """Yield tasks on every dependency path from ``start_task`` to ``goal_task_family``."""
    if path is None:
        path = [start_task]
    if start_task.task_family == goal_task_family or goal_task_family is None:
        for item in path:
            yield item
    for next_task in _get_task_requires(start_task) - set(path):
        for t in dfs_paths(next_task, goal_task_family, path + [next_task]):
            yield t


def find_deps(task, upstream_task_family):
    """Return the set of all tasks on all paths between ``task`` and ``upstream_task_family``."""
    return {t for t in dfs_paths(task, upstream_task_family)}


# ----------------------------------------------------------------------------------------------
# Sequential executor
# ----------------------------------------------------------------------------------------------

def _task_label(task):
    params = task.to_str_params(only_significant=True)   # dict: name -> serialized value
    inner = ", ".join("{}={}".format(k, v) for k, v in params.items())
    return "{}({})".format(task.task_family, inner) if inner else task.task_family


class TaskFailure:
    """One failed task: the task instance plus why it failed."""
    def __init__(self, task, exception=None, traceback=None, reason=None):
        self.task = task
        self.exception = exception      # the real run() exception, or None
        self.traceback = traceback      # formatted traceback string, or None
        self.reason = reason            # 'run error' | 'dependency failed' | 'external missing'
    def __str__(self):
        if self.exception is not None:
            return "{}: {}: {}".format(_task_label(self.task),
                                       type(self.exception).__name__, self.exception)
        return "{}: {}".format(_task_label(self.task), self.reason or "failed")


class RunResult:
    """What happened to the DAG in one build: identities, status, failure context."""
    def __init__(self, success, ran, complete, failed):
        self.success = success          # bool: no failures
        self.ran = ran                  # list[Task]  actually recomputed
        self.complete = complete        # list[Task]  cache hits (skipped)
        self.failed = failed            # list[TaskFailure]

    # --- back-compat aliases ---
    @property
    def scheduling_succeeded(self):
        return self.success
    @property
    def first_exception(self):
        return next((f.exception for f in self.failed if f.exception is not None), None)
    def summary(self):
        """Return the execution summary text (``print(result.summary())``)."""
        return str(self)

    def __bool__(self):
        return self.success

    # --- queries (by task CLASS) ---
    def did_run(self, task_cls):
        return any(isinstance(t, task_cls) for t in self.ran)
    def ran_of(self, task_cls):
        return [t for t in self.ran if isinstance(t, task_cls)]
    def failure_of(self, task_cls):
        return next((f for f in self.failed if isinstance(f.task, task_cls)), None)

    def __str__(self):
        # luigi-compatible wording so the plugin SKILL.md guidance stays accurate.
        n = len(self.complete) + len(self.ran) + len(self.failed)
        def block(label, rendered, cap=None):
            lines = ["* {} {}".format(len(rendered), label) + (":" if rendered else "")]
            shown = rendered if cap is None else rendered[:cap]
            lines += ["    - {}".format(s) for s in shown]
            if cap is not None and len(rendered) > cap:
                lines.append("    ... and {} more".format(len(rendered) - cap))
            return lines
        parts = ["Scheduled {} tasks of which:".format(n)]
        parts += block("complete ones were encountered",
                       [_task_label(t) for t in self.complete], cap=10)
        parts += block("ran successfully", [_task_label(t) for t in self.ran])
        parts += block("failed", [str(f) for f in self.failed])   # TaskFailure.__str__
        smiley = ":)" if self.success else ":("
        why = ("there were no failed tasks or missing dependencies" if self.success
               else "there were failed tasks")
        parts.append("This progress looks {} because {}".format(smiley, why))
        return "\n".join(parts)


class MultiRunResult(dict):
    """Return value of ``WorkflowMulti.run()``: a ``{flow_name: RunResult}`` dict that also
    carries ``.summary()``/``.success`` so ``print(result.summary())`` works the same as for a
    single ``Workflow`` (whose ``run()`` returns a plain :py:class:`RunResult`)."""
    @property
    def success(self):
        return all(bool(r) for r in self.values())
    def __bool__(self):
        return self.success
    def summary(self):
        """Per-flow execution summaries, each under a ``===== <flow> =====`` header."""
        return "\n\n".join(
            "===== {} =====\n{}".format(name, res.summary()) for name, res in self.items())
    def __str__(self):
        return self.summary()


def build(tasks, workers=1, detailed_summary=False, **ignored):
    """
    Run ``tasks`` and their dependencies sequentially, in dependency order.

    All state is local to this call, so a task's ``run()`` may itself call ``oryxflow.run`` /
    ``flow.run`` (the flow-within-a-flow pattern) without corrupting the outer build.

    External tasks (``external=True`` or ``run is None``) are never executed; if still
    incomplete after their dependencies, they are marked failed.

    :returns: a :py:class:`RunResult` with ``.ran``/``.complete``/``.failed`` task identities,
        ``.success`` and the ``did_run``/``ran_of``/``failure_of`` queries.
    """
    if not isinstance(tasks, (list,)):
        tasks = [tasks]

    visited = {}          # task_id -> bool (success)
    ran = []              # tasks executed this build
    already_complete = []  # tasks already complete
    failed = []           # TaskFailure for tasks that failed (run error, failed dep, missing external)

    def _process(task):
        tid = task.task_id
        if tid in visited:
            return visited[tid]

        if task.complete():
            logger.debug("task skipped (already complete): {}", tid)
            already_complete.append(task)
            visited[tid] = True
            return True

        # Process dependencies first.
        dep_ok = True
        for dep in flatten(task.requires()):
            if not _process(dep):
                dep_ok = False
        if not dep_ok:
            logger.error("task failed (dependency failed): {}", tid)
            failed.append(TaskFailure(task, reason="dependency failed"))
            visited[tid] = False
            return False

        # External tasks are never executed; their output must come from elsewhere.
        is_external = getattr(task, 'external', False) or getattr(task, 'run', None) is None
        if is_external:
            if task.complete():
                already_complete.append(task)
                visited[tid] = True
                return True
            logger.warning("external task missing output: {}", tid)
            failed.append(TaskFailure(task, reason="external missing output"))
            visited[tid] = False
            return False

        logger.info("task start: {} ({})", task.task_family, tid)
        t0 = time.perf_counter()
        try:
            result = task.run()
            if inspect.isgenerator(result):
                if not _drive_generator(result):
                    failed.append(TaskFailure(task, reason="dependency failed"))
                    visited[tid] = False
                    return False
        except Exception as e:
            logger.opt(exception=True).error("task failed: {}", tid)
            failed.append(TaskFailure(task, exception=e, traceback=traceback.format_exc(),
                                      reason="run error"))
            visited[tid] = False
            return False

        logger.info("task complete: {} in {:.3f}s", tid, time.perf_counter() - t0)
        ran.append(task)
        visited[tid] = True
        return True

    def _drive_generator(gen):
        # Drive a generator-style run() that yields tasks (eg TaskAggregator) or dynamic
        # requirements, processing each yielded batch before resuming.
        try:
            next_requires = next(gen)
            while True:
                logger.debug("generator yielded {} requires", len(flatten(next_requires)))
                ok = True
                for req in flatten(next_requires):
                    if not _process(req):
                        ok = False
                if not ok:
                    gen.close()
                    return False
                next_requires = gen.send(getpaths(next_requires))
        except StopIteration:
            return True

    for task in tasks:
        _process(task)

    success = len(failed) == 0
    result = RunResult(success, ran, already_complete, failed)
    if detailed_summary:                       # gated by execution_summary
        logger.info("run summary:\n{}", result.summary())
    return result
