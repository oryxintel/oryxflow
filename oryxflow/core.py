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


class StalenessWarning(UserWarning):
    """Code changed but code_version didn't (or can't be verified). Shown on every
    occurrence: python's default warning filter dedups by call site, which would
    silence the second run() in the same process -- exactly when it matters."""


import warnings as _warnings_mod
_warnings_mod.simplefilter('always', StalenessWarning)


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

    # Explicit code identity token (str or int), OPT-IN: when set it is the authority
    # for this task's own logic -- bump it to rerun this task and everything downstream
    # (code edits without a bump only warn). None (the default) with
    # settings.code_version_auto (the default) derives the token automatically from the
    # AST hash of the task's module + repo-local imports, so logic edits rerun with no
    # attribute to maintain; with code_version_auto=False, None leaves caching purely
    # parameter-based.
    code_version = None

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
        """``True`` if all of this task's outputs exist.

        Note: ``TaskData`` OVERRIDES this to ALSO require the stored code
        fingerprint to match the current one (``TaskData._code_ok``), so a
        ``code_version`` bump makes a task incomplete and forces a rerun -- the
        fingerprint is authoritative here, not merely advisory. The AST
        source-hash is a SEPARATE, warn-only advisory (fires when code changed
        but ``code_version`` did not); it does not gate completeness.
        """
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

    @property
    def _code_fingerprint(self):
        """Recursive code identity, compared against the state store by
        TaskData.complete() -- a mismatch forces a rerun (authoritative). The own
        token is the explicit ``code_version`` when declared; otherwise, with
        ``settings.code_version_auto`` (the default), the AST hash of the task's
        module + transitively imported repo-local files, so logic edits rerun
        automatically. None when neither applies here or upstream (feature inert).
        For tasks WITH an explicit code_version the AST source-hash stays a
        warn-only advisory and never gates completeness."""
        # Not memoized: instances are process-long-lived via the Register cache, so a
        # cached fingerprint would go stale on runtime bumps; recompute is a cheap md5
        # over a small DAG (module_hashes carries its own mtime-revalidated cache).
        dep_fps = [d._code_fingerprint for d in self.deps()]
        own = self.code_version
        if own is None:
            from oryxflow import settings as _settings_
            if _settings_.code_version_auto:
                from oryxflow import codehash as _codehash_
                h = _codehash_.task_code_hash(self)
                own = 'auto:{}'.format(h) if h is not None else None
        if own is None and all(f is None for f in dep_fps):
            return None
        parts = [self.task_family, str(own)] + sorted(f or '' for f in dep_fps)
        return hashlib.md5('|'.join(parts).encode('utf-8')).hexdigest()[:16]

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
    def __init__(self, success, ran, complete, failed, run_id=None, reasons=None,
                 warnings=None):
        self.success = success          # bool: no failures
        self.ran = ran                  # list[Task]  actually recomputed
        self.complete = complete        # list[Task]  cache hits (skipped)
        self.failed = failed            # list[TaskFailure]
        self.run_id = run_id            # id shared by this build's events
        self.reasons = reasons or {}    # {task_id: why it ran} for tasks in `ran`
        self.warnings = warnings or []  # unacknowledged code-change warning strings

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

    # --- aggregates across flows, so callers never hand-roll them ---
    @property
    def ran(self):
        return [t for r in self.values() for t in r.ran]
    @property
    def complete(self):
        return [t for r in self.values() for t in r.complete]
    @property
    def failed(self):
        return [f for r in self.values() for f in r.failed]
    @property
    def reasons(self):
        merged = {}
        for r in self.values():
            merged.update(r.reasons)
        return merged
    @property
    def warnings(self):
        return [w for r in self.values() for w in r.warnings]

    def summary(self):
        """Per-flow execution summaries, each under a ``===== <flow> =====`` header."""
        return "\n\n".join(
            "===== {} =====\n{}".format(name, res.summary()) for name, res in self.items())
    def __str__(self):
        return self.summary()


_git_info_cache = None  # (expires_at, sha, dirty) -- subprocesses are too slow per build


def _git_info():
    """(sha, dirty) of the working tree, best-effort; (None, None) outside a repo.
    Cached for a few seconds: builds are often issued in tight loops (WorkflowMulti,
    tests) and two subprocess calls per build dominate small-DAG runtimes."""
    global _git_info_cache
    now = time.monotonic()
    if _git_info_cache is not None and now < _git_info_cache[0]:
        return _git_info_cache[1], _git_info_cache[2]
    import subprocess
    try:
        sha = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True,
                             text=True, timeout=5).stdout.strip() or None
        status = subprocess.run(['git', 'status', '--porcelain'], capture_output=True,
                                text=True, timeout=5).stdout.strip()
        sha, dirty = sha, (bool(status) if sha else None)
    except Exception:
        sha, dirty = None, None
    _git_info_cache = (now + 10, sha, dirty)
    return sha, dirty


def build(tasks, workers=1, detailed_summary=False, flow=None, **ignored):
    """
    Run ``tasks`` and their dependencies sequentially, in dependency order.

    All state is local to this call, so a task's ``run()`` may itself call ``oryxflow.run`` /
    ``flow.run`` (the flow-within-a-flow pattern) without corrupting the outer build.

    External tasks (``external=True`` or ``run is None``) are never executed; if still
    incomplete after their dependencies, they are marked failed.

    :param flow: flow name when launched via ``Workflow``/``WorkflowMulti``; stamped into
        this build's event envelopes.
    :returns: a :py:class:`RunResult` with ``.ran``/``.complete``/``.failed`` task identities,
        ``.success``, ``.run_id``, per-task ``.reasons`` and code-change ``.warnings``.
    """
    import os
    import uuid
    import warnings as _stdwarnings
    from datetime import datetime, timezone
    from oryxflow import events as _events
    from oryxflow import state as _state
    from oryxflow import codehash as _codehash
    from oryxflow import log as _log
    from oryxflow import settings as _settings

    if not isinstance(tasks, (list,)):
        tasks = [tasks]

    visited = {}          # task_id -> bool (success)
    ran = []              # tasks executed this build
    already_complete = []  # tasks already complete
    failed = []           # TaskFailure for tasks that failed (run error, failed dep, missing external)
    reasons = {}          # task_id -> why it ran
    warned = []           # unacknowledged code-change warning strings
    advised = set()       # task_ids already checked by the code advisory this build

    run_id = uuid.uuid4().hex
    git_sha, git_dirty = _git_info()

    def _emit(event_type, payload, msg=None, level='info'):
        # single call site per engine fact: the event and the human loguru line
        # come from the same data and can't disagree
        _events.append(event_type, payload, run_id=run_id, flow=flow)
        if msg is not None:
            getattr(logger, level)("{}", msg)

    def _code_state(task):
        # (fingerprint, dirpath) when the code-invalidation feature applies, else (None, None)
        fp = task._code_fingerprint
        if fp is None or not hasattr(task, '_resolved_dirpath'):
            return None, None
        return fp, task._resolved_dirpath()

    def _hashes(task):
        try:
            return _codehash.module_hashes(task)
        except Exception:
            return {}

    def _warn_code(task, msg, changed_files):
        # decision 12a: the advisory must be visible without enable_logging(), so it
        # goes out on every channel -- warnings.warn, loguru, the event, RunResult
        warned.append(msg)
        _stdwarnings.warn(msg, StalenessWarning)
        _emit('code_warning',
              {'task_id': task.task_id, 'family': task.task_family,
               'changed_files': changed_files, 'code_version': task.code_version},
              msg=msg, level='warning')

    def _mtime_guard_trips(task, hashes):
        # decision 12c: local-fs heuristic only -- any hashed source newer than the output
        if _settings.cloud_fs_enabled:
            return False
        try:
            root = _codehash._project_root()
            src = [os.stat(str(root / rel)).st_mtime for rel in hashes]
            out = [os.stat(str(o.path)).st_mtime for o in flatten(task.output())
                   if getattr(o, 'path', None) is not None]
            return bool(src) and bool(out) and max(src) > min(out)
        except Exception:
            return False

    def _make_record(task, hashes, output_id, duration=None):
        # one shape for every stamp: both dimensions (own hashes + dep output-identity
        # fold) recorded on every write regardless of mode, plus the live fingerprint
        # (informational), the last materialization cost (drives the
        # expensive-recompute guard) and the schema/interpreter tags
        return {'fingerprint': task._code_fingerprint,
                'code_version': task.code_version,
                'source_hashes': hashes, 'output_id': output_id,
                'dep_state': task._dep_state() if hasattr(task, '_dep_state') else None,
                'duration_s': duration,
                'py': _codehash.PY_TAG, 'v': _state.RECORD_V,
                'ts': datetime.now(timezone.utc).isoformat()}

    def _advise(task):
        # advisory sweep over a complete subtree, POST-ORDER (deps first, so re-stamped
        # dep records exist before this task's dep_state folds their output_ids):
        # grandfather-stamp missing records, converge records across mode flips and
        # schema/interpreter migrations (preserving output_id so nothing ripples
        # downstream), and warn when a pinned task's code changed without a bump
        tid = task.task_id
        if tid in advised:
            return
        advised.add(tid)
        fp = task._code_fingerprint
        if fp is None:
            # fingerprint folds deps, so None means the entire upstream subtree is
            # untracked too -- nothing to advise anywhere above this task
            return
        for dep in flatten(task.requires()):
            _advise(dep)
        dirpath = task._resolved_dirpath() if hasattr(task, '_resolved_dirpath') else None
        if dirpath is None:
            return
        try:
            outputs = flatten(task.output())
            outputs_exist = bool(outputs) and all(o.exists() for o in outputs)
        except Exception:
            outputs_exist = False
        if not outputs_exist:
            return
        rec = _state.get_record(dirpath, tid)
        if rec is None:
            # grandfathering: output exists, record missing -> treat as current and
            # stamp a baseline (state-only, no event), unless the output predates
            # the code on disk
            hashes = _hashes(task)
            if _mtime_guard_trips(task, hashes):
                _warn_code(task, (
                    "task {}: output predates current code; can't verify -- "
                    "reset() or oryxflow.accept_code({}) to confirm").format(
                        task.task_family, task.task_family),
                    sorted(hashes))
            else:
                _state.put_record(dirpath, tid,
                                  _make_record(task, hashes, uuid.uuid4().hex[:16]))
            return
        if rec.get('v') != _state.RECORD_V or rec.get('py') != _codehash.PY_TAG:
            # schema/interpreter changed -> stored hashes aren't comparable; converge
            # silently at grandfather trust level, PRESERVING output_id (and the
            # recorded run cost) so downstream dep_state folds don't move
            _state.put_record(dirpath, tid,
                              _make_record(task, _hashes(task),
                                           rec.get('output_id') or uuid.uuid4().hex[:16],
                                           duration=rec.get('duration_s')))
            return
        stored = rec.get('source_hashes') or {}
        current = _hashes(task)
        if stored and current and stored != current:
            # code changed but the task is (deliberately) still complete: pinned by an
            # explicit code_version, or held by the expensive-recompute guard. Warn at
            # the decision point with the exits; never re-stamp here (that would bless
            # the change) -- accept_code is the explicit blessing.
            changed = sorted(k for k in set(stored) | set(current)
                             if stored.get(k) != current.get(k))
            if task.code_version is not None:
                _warn_code(task, (
                    "task {}: {} changed since cached run; code_version still {} -- "
                    "reusing cached output. Bump code_version to recompute, or "
                    "oryxflow.accept_code({}) only if certain the output is "
                    "equivalent -- when unsure, bump (best-effort check: can't "
                    "see data files or dynamic calls).").format(
                        task.task_family, ', '.join(changed),
                        task.code_version, task.task_family),
                    changed)
            else:
                _warn_code(task, (
                    "task {}: {} changed since cached run; last run took {:.0f}s -- "
                    "reusing cached output (expensive-recompute guard, "
                    "settings.code_version_auto_expensive_s={}). reset() to recompute, "
                    "oryxflow.accept_code({}) if output-equivalent, or set "
                    "code_version to manage this task explicitly.").format(
                        task.task_family, ', '.join(changed),
                        rec.get('duration_s') or 0,
                        getattr(_settings, 'code_version_auto_expensive_s', None),
                        task.task_family),
                    changed)
        elif (rec.get('fingerprint') != fp
                or rec.get('code_version') != task.code_version):
            # hashes match but the record predates a mode flip (pin added/removed with
            # unchanged code) or an upstream toggle -- converge silently, preserving
            # output_id and run cost
            _state.put_record(dirpath, tid,
                              _make_record(task, current,
                                           rec.get('output_id') or uuid.uuid4().hex[:16],
                                           duration=rec.get('duration_s')))

    def _reason_for(task):
        # why is this (incomplete) task about to run? deps were processed first, so a
        # rerun upstream is already in `ran`.
        try:
            outputs = flatten(task.output())
            if not outputs or not all(o.exists() for o in outputs):
                return 'output missing'
        except Exception:
            return 'output missing'
        fp, dirpath = _code_state(task)
        if fp is not None:
            rec = _state.get_record(dirpath, task.task_id)
            if rec is not None:
                if rec.get('code_version') != task.code_version:
                    return 'code change ({} -> {})'.format(
                        rec.get('code_version') if rec.get('code_version') is not None
                        else 'auto',
                        task.code_version if task.code_version is not None else 'auto')
                if task.code_version is None:
                    # own dimension moved (auto): name the changed files
                    stored = rec.get('source_hashes') or {}
                    current = _hashes(task)
                    changed = sorted(k for k in set(stored) | set(current)
                                     if stored.get(k) != current.get(k))
                    if changed:
                        shown = ', '.join(changed[:3]) + ('...' if len(changed) > 3 else '')
                        return 'code change (auto: {})'.format(shown)
                return 'upstream rerun'
        if any(d.task_id in reasons or d in ran for d in flatten(task.requires())):
            return 'upstream rerun'
        return 'upstream rerun' if flatten(task.requires()) else 'output missing'

    def _emit_failed(task, error, tb, duration):
        _emit('task_failed',
              {'task_id': task.task_id, 'family': task.task_family,
               'params': task.to_str_params(only_significant=False),
               'error': error, 'traceback': tb[-4096:] if tb else None,
               'duration_s': duration})

    def _process(task):
        tid = task.task_id
        if tid in visited:
            return visited[tid]

        if task.complete():
            _advise(task)
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
            _emit_failed(task, 'dependency failed', None, None)
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
            _emit_failed(task, 'external missing output', None, None)
            visited[tid] = False
            return False

        reason = _reason_for(task)
        logger.info("task start: {} ({})", task.task_family, tid)
        t0 = time.perf_counter()
        try:
            result = task.run()
            if inspect.isgenerator(result):
                if not _drive_generator(result):
                    failed.append(TaskFailure(task, reason="dependency failed"))
                    _emit_failed(task, 'dependency failed', None,
                                 time.perf_counter() - t0)
                    visited[tid] = False
                    return False
        except Exception as e:
            logger.opt(exception=True).error("task failed: {}", tid)
            tb = traceback.format_exc()
            failed.append(TaskFailure(task, exception=e, traceback=tb,
                                      reason="run error"))
            _emit_failed(task, '{}: {}'.format(type(e).__name__, e), tb,
                         time.perf_counter() - t0)
            visited[tid] = False
            return False

        duration = time.perf_counter() - t0
        fp, dirpath = _code_state(task)
        hashes = _hashes(task) if (fp is not None
                                   or getattr(_settings, 'events', True)) else {}
        if fp is not None:
            try:
                # fresh output_id: this task actually rematerialized, downstream
                # dep_state folds must move
                _state.put_record(dirpath, tid,
                                  _make_record(task, hashes, uuid.uuid4().hex[:16],
                                               duration=duration))
            except Exception as e:
                logger.debug("state record write failed for {}: {}", tid, e)
        try:
            from oryxflow import __version__ as _version
        except Exception:
            _version = None
        _emit('task_ran',
              {'task_id': tid, 'family': task.task_family,
               'params': task.to_str_params(only_significant=False),
               'code_version': task.code_version, 'fingerprint': fp,
               'auto': task.code_version is None and _settings.code_version_auto,
               'source_hashes': hashes, 'reason': reason, 'duration_s': duration,
               'git_sha': git_sha, 'git_dirty': git_dirty,
               'oryxflow_version': _version,
               'dirpath': str(dirpath if dirpath is not None else _settings.dirpath)},
              msg="task complete: {} in {:.3f}s".format(tid, duration))
        ran.append(task)
        reasons[tid] = reason
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

    _emit('run_started',
          {'tasks': sorted({t.task_family for t in tasks})},
          msg="run started: {} (run_id={})".format(
              ', '.join(sorted({t.task_family for t in tasks})), run_id))

    def _capture_task_log(level, message, context):
        extra = {}
        for k, v in list(context.items())[:20]:
            extra[k] = v if isinstance(v, (int, float, bool, type(None))) else str(v)[:512]
        _events.append('task_log',
                       {'task_id': context.get('task_id'),
                        'family': context.get('task_family'),
                        'level': level, 'message': str(message)[:4096], 'extra': extra},
                       run_id=run_id, flow=flow)

    previous_capture = _log.set_task_log_capture(_capture_task_log)
    _codehash.freeze()   # code can't change mid-build: skip per-complete() mtime re-stats
    try:
        for task in tasks:
            _process(task)
    finally:
        _codehash.unfreeze()
        _log.set_task_log_capture(previous_capture)

    success = len(failed) == 0
    _emit('run_finished',
          {'counts': {'ran': len(ran), 'complete': len(already_complete),
                      'failed': len(failed)}, 'success': success},
          msg="run finished: {} ran, {} complete, {} failed, success={}".format(
              len(ran), len(already_complete), len(failed), success))
    try:
        _events.flush()   # events are written async; the run's story is durable on return
    except Exception:
        pass
    result = RunResult(success, ran, already_complete, failed,
                       run_id=run_id, reasons=reasons, warnings=warned)
    if detailed_summary:                       # gated by execution_summary
        logger.info("run summary:\n{}", result.summary())
    return result
