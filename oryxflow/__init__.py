import warnings

from importlib.metadata import version as _pkg_version, PackageNotFoundError as _PkgNotFound
try:
    __version__ = _pkg_version("oryxflow")
except _PkgNotFound:          # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

from oryxflow import core
from oryxflow.core import flatten, RunResult, MultiRunResult, TaskFailure, StalenessWarning
from oryxflow.log import logger, enable_logging, disable_logging
from oryxflow.parameter import (
    Parameter,
    IntParameter, FloatParameter, BoolParameter,
    DateParameter, DictParameter, ListParameter, ChoiceParameter, EnumParameter,
)

from pathlib import Path

import oryxflow.targets, oryxflow.tasks, oryxflow.settings
import oryxflow.utils
import oryxflow.events as events
import oryxflow.state
import oryxflow.codehash
from oryxflow.cache import data as data
import oryxflow.cache

def set_dir(dir=None):
    """
    Initialize oryxflow

    Args:
        dir (str): data output directory

    """
    if dir is None:
        dirpath = oryxflow.settings.dirpath
        dirpath.mkdir(exist_ok=True)
    else:
        dirpath = Path(dir)
        oryxflow.settings.dir = dir
        oryxflow.settings.dirpath = dirpath

    oryxflow.settings.isinit = True
    return dirpath

def enable_cloud_storage(protocol, bucket, prefix=None):
    """
    Initialize cloud storage
    Uses https://github.com/orgs/fsspec/repositories

    Args:
        protocol (str): fsspec eg gcs, s3, dropbox etc. See https://pypi.org/project/universal-pathlib/
        bucket (str): bucket name
        prefix (str): prefix similar to folder

    """
    from pathlib import PurePosixPath
    fs_path = PurePosixPath(bucket)
    if prefix is not None:
        fs_path = fs_path / prefix
    oryxflow.settings.cloud_fs_prefix = f'{protocol}://{fs_path}'
    oryxflow.settings.cloud_fs_enabled = True

    return oryxflow.settings.cloud_fs_prefix

def enable_gcs(bucket, prefix=None):
    """
    Initialize google cloud storage, for reference see https://cloud.google.com/storage/docs/listing-objects
    Uses https://gcsfs.readthedocs.io/en/latest/

    Args:
        bucket (str): bucket name
        prefix (str): prefix similar to folder


    """
    return enable_cloud_storage('gcs', bucket, prefix)



def preview(tasks, indent='', last=True, show_params=True, clip_params=False, print_it=True):
    """
    Preview task flows

    Args:
        tasks (obj, list): task or list of tasks
    """
    msg = '\n ===== oryxflow Execution Preview ===== \n'
    if not isinstance(tasks, (list,)):
        tasks = [tasks]
    for t in tasks:
        msg += oryxflow.utils.print_tree(t, indent=indent, last=last, show_params=show_params, clip_params=clip_params)
    msg += '\n ===== oryxflow Execution Preview ===== \n'
    if print_it:
        print(msg)
    else:
        return msg




def run(tasks, forced=None, forced_all=False, forced_all_upstream=False, confirm=False, workers=1, abort=True,
        execution_summary=None, main_thread_only=False, **kwargs):
    """
    Run tasks locally. Runs the DAG sequentially in dependency order.

    Args:
        tasks (obj, list): task or list of tasks
        forced (list): list of forced tasks
        forced_all (bool): force all tasks
        forced_all_upstream (bool): force all tasks including upstream
        confirm (list): confirm invalidating tasks
        workers (int): number of workers
        abort (bool): on errors raise exception
        execution_summary (bool): log execution summary
        main_thread_only (bool): if True, only works in main thread of the main interpreter. Default false so it can run in apps and workers.
        kwargs: keywords to pass to core.build

    """

    if not isinstance(tasks, (list,)):
        tasks = [tasks]

    # if forced_all_upstream is true we are going to force run tasks anyway
    # in the second if condition.
    # So in this case we are going to skip running forced tasks.
    if forced_all and not forced_all_upstream:
        forced = tasks
    if forced_all_upstream:
        for t in tasks:
            invalidate_upstream(t, confirm=confirm)
    if forced is not None:
        if not isinstance(forced, (list,)):
            forced = [forced]
        invalidate = []
        for tf in forced:
            for tup in tasks:
                invalidate.append(oryxflow.taskflow_downstream(tf, tup))

        invalidate = set().union(*invalidate)
        invalidate = {t for t in invalidate if t.complete()}
        if len(invalidate) > 0:
            if confirm:
                print('Forced tasks', invalidate)
                c = input('Confirm invalidating forced tasks (y/n)')
            else:
                c = 'y'
            if c == 'y':
                for t in invalidate:
                    logger.info("invalidating forced task: {}", t.task_id)
                    t.invalidate(confirm=False)
            else:
                return None

    execution_summary = execution_summary if execution_summary is not None else oryxflow.settings.execution_summary
    opts = {**{'workers': workers, 'local_scheduler': True}, **kwargs}
    opts['detailed_summary'] = execution_summary       # gates the summary LOG, not a print
    result = core.build(tasks, **opts)
    success = result.scheduling_succeeded
    if abort and not success:
        raise RuntimeError(
            'Exception found running flow, check trace. For more details see https://oryxflow.readthedocs.io/en/latest/run.html#debugging-failures') from result.first_exception
    return result


def accept_code(task=None):
    """
    Acknowledge an output-equivalent code change: re-stamp the stored code records at
    current without rerunning, so neither the auto rerun nor the "code changed but
    code_version didn't" warning fires. The three exits from every code change: bump
    ``code_version`` / let auto rerun (semantic change), ``accept_code`` (equivalent
    refactor -- only if certain; when unsure, recompute), or ``reset()`` (recompute
    regardless).

    Args:
        task (obj, class): task INSTANCE re-stamps that task AND its entire upstream
            dependency tree in its data dir (a shared-helper edit changes the stored
            source hashes of every task importing the file, so acceptance must cover
            the whole band) -- call it on the most-downstream task you judge
            equivalent, typically the flow's default task, or use
            ``Workflow.accept_code()``. A task CLASS re-stamps every record of that
            family in ``settings.dirpath``. With no argument, bulk-accepts: every
            record in ``settings.dirpath`` whose stored hashes differ from the current
            files is re-stamped. Accepting never touches a record's ``output_id``, so
            it never triggers downstream recomputes.

    Returns: list of task_ids re-stamped
    """
    from datetime import datetime, timezone
    from oryxflow import state, codehash

    now = datetime.now(timezone.utc).isoformat()
    accepted = []

    def _restamp(dirpath, task_id, rec, hashes, family, fingerprint=None, dep_state=None):
        rec = dict(rec)   # preserves output_id: accepting must not ripple downstream
        if fingerprint is not None:
            rec['fingerprint'] = fingerprint
        if dep_state is not None:
            rec['dep_state'] = dep_state
        rec['source_hashes'] = hashes
        rec['py'] = codehash.PY_TAG
        rec['v'] = state.RECORD_V
        rec['ts'] = now
        state.put_record(dirpath, task_id, rec)
        events.append('code_accepted',
                      {'task_id': task_id, 'family': family, 'source_hashes': hashes})
        logger.info("accepted code change for {}", task_id)
        accepted.append(task_id)

    if task is not None and not isinstance(task, type):
        # instance: walk the task and its upstream dep tree POST-ORDER (deps first, so
        # dep_state folds re-stamped dep records), re-stamping each existing record's
        # own dimension (hashes) to current
        seen = set()

        def _walk(t):
            if t.task_id in seen:
                return
            seen.add(t.task_id)
            fp = t._code_fingerprint
            if fp is None:
                # None folds upward: the entire upstream subtree is untracked too
                return
            for dep in core.flatten(t.requires()):
                _walk(dep)
            dirpath = t._resolved_dirpath() if hasattr(t, '_resolved_dirpath') \
                else oryxflow.settings.dirpath
            rec = state.get_record(dirpath, t.task_id)
            if rec is not None:
                _restamp(dirpath, t.task_id, rec, codehash.module_hashes(type(t)),
                         t.task_family, fingerprint=fp,
                         dep_state=t._dep_state() if hasattr(t, '_dep_state') else None)

        _walk(task)
    elif task is not None:
        cls = task
        family = cls.task_family
        hashes = codehash.module_hashes(cls)
        dirpath = oryxflow.settings.dirpath
        for tid, rec in state.all_records(dirpath).items():
            if tid.split('_')[0] == family and rec is not None:
                _restamp(dirpath, tid, rec, hashes, family)
    else:
        dirpath = oryxflow.settings.dirpath
        root = codehash._project_root()
        for tid, rec in state.all_records(dirpath).items():
            stored = rec.get('source_hashes') or {}
            current = {}
            changed = False
            for rel, digest in stored.items():
                cur = codehash.file_hash(root / rel)
                current[rel] = cur if cur is not None else digest
                if cur is not None and cur != digest:
                    changed = True
            if changed:
                _restamp(dirpath, tid, rec, current, tid.split('_')[0])
    return accepted


def taskflow_upstream(task, only_complete=False):
    """
    Get all upstream inputs for a task

    Args:
        task (obj): task

    """

    tasks = oryxflow.utils.traverse(task)
    if only_complete:
        tasks = [t for t in tasks if t.complete()]
    return tasks


def taskflow_downstream(task, task_downstream, only_complete=False):
    """
    Get all downstream outputs for a task

    Args:
        task (obj): task
        task_downstream (obj): downstream target task

    """
    tasks = core.find_deps(task_downstream, task.task_family)
    if only_complete:
        tasks = {t for t in tasks if t.complete()}
    return tasks


def invalidate_all(confirm=False):
    """
    Invalidate all tasks by deleting all files in data directory

    Args:
        confirm (bool): confirm operation

    """
    # record all tasks that run and their output vs files present
    raise NotImplementedError()


def invalidate_orphans(confirm=False):
    """
    Invalidate all unused task outputs

    Args:
        confirm (bool): confirm operation

    """
    # record all tasks that run and their output vs files present
    raise NotImplementedError()


def show(task):
    """
    Show task execution status

    Args:
        tasks (obj, list): task or list of tasks
    """
    preview(task)


def _as_families(x):
    """Normalize a single task/family (class or instance) or an iterable of them to a tuple,
    for use with ``isinstance`` / family matching. Shared by invalidate_upstream (``only=``)
    and invalidate_downstream (family list)."""
    return tuple(x) if isinstance(x, (list, tuple, set)) else (x,)


def invalidate_upstream(task, confirm=False, only=None):
    """
    Invalidate all tasks upstream tasks in a flow.

    For example, you have 3 dependant tasks. Normally you run Task3 but you've changed parameters for Task1. By invalidating Task3 it will check the full DAG and realize Task1 needs to be invalidated and therefore Task2 and Task3 also.

    Args:
        task (obj): task to invalidate. This should be an upstream task for which you want to check upstream dependencies for invalidation conditions
        confirm (bool): confirm operation
        only (class, list): if set, only invalidate upstream tasks of these task family/families

    """
    tasks = taskflow_upstream(task, only_complete=False)
    if only is not None:
        tasks = [t for t in tasks if isinstance(t, _as_families(only))]
    if len(tasks) == 0:
        print('no tasks to invalidate')
        return True
    if confirm:
        print('Completed tasks to invalidate:')
        for t in tasks:
            print(t)
        c = input('Confirm invalidating tasks (y/n)')
    else:
        c = 'y'
    if c == 'y':
        for t in tasks:
            logger.info("invalidating upstream task: {}", t.task_id)
            t.invalidate(confirm=False)


def invalidate_downstream(task, task_downstream, confirm=False):
    """
    Invalidate all downstream tasks in a flow.

    For example, you have 3 dependant tasks. Normally you run Task3 but you've changed parameters for Task1. By invalidating Task3 it will check the full DAG and realize Task1 needs to be invalidated and therefore Task2 and Task3 also.

    Args:
        task (obj, class, list): task/family — or list of families — to invalidate downstream of.
            Only the family is used (a class is fine), so a list resets several families and
            everything downstream of each, in one call.
        task_downstream (obj): downstream task target
        confirm (bool): confirm operation

    """
    tasks = set()
    for fam in _as_families(task):
        tasks |= taskflow_downstream(fam, task_downstream, only_complete=True)
    tasks = list(tasks)
    if len(tasks) == 0:
        print('no tasks to invalidate')
        return True
    if confirm:
        print('Completed tasks to invalidate:')
        for t in tasks:
            print(t)
        c = input('Confirm invalidating tasks (y/n)')
    else:
        c = 'y'
    if c == 'y':
        for t in tasks:
            logger.info("invalidating downstream task: {}", t.task_id)
            t.invalidate(confirm=False)
        return True
    else:
        return False


def clone_parent(cls):
    warnings.warn("This is replaced with `@oryxflow.requires()`", DeprecationWarning, stacklevel=2)

    def requires(self):
        return self.clone_parent()

    setattr(cls, 'requires', requires)
    return cls


# Like core.inherits but for handling dictionaries
class dict_inherits:
    def __init__(self, *tasks_to_inherit):
        super(dict_inherits, self).__init__()
        if not tasks_to_inherit:
            raise TypeError("tasks_to_inherit cannot be empty")
        # We know the first arg is a dict.
        self.tasks_to_inherit = tasks_to_inherit[0]

    def __call__(self, task_that_inherits):
        for task_to_inherit in self.tasks_to_inherit:
            for param_name, param_obj in self.tasks_to_inherit[task_to_inherit].get_params():
                # Check if the parameter exists in the inheriting task
                if not hasattr(task_that_inherits, param_name):
                    # If not, add it to the inheriting task
                    setattr(task_that_inherits, param_name, param_obj)

        # adding dictionary functionality
        def clone_parents_dict(_self, **kwargs):
            return {
                task_to_inherit: _self.clone(cls=self.tasks_to_inherit[task_to_inherit], **kwargs)
                for task_to_inherit in self.tasks_to_inherit
            }

        task_that_inherits.clone_parents_dict = clone_parents_dict
        return task_that_inherits


# Like core.requires but for handling dictionaries
class dict_requires:
    def __init__(self, *tasks_to_require):
        super(dict_requires, self).__init__()
        if not tasks_to_require:
            raise TypeError("tasks_to_require cannot be empty")

        self.tasks_to_require = tasks_to_require[0]  # Assign the dictionary

    def __call__(self, task_that_requires):
        task_that_requires = dict_inherits(self.tasks_to_require)(task_that_requires)

        def requires(_self):
            return _self.clone_parents_dict()

        task_that_requires.requires = requires

        return task_that_requires


def inherits(*tasks_to_inherit):
    if isinstance(tasks_to_inherit[0], dict):
        return dict_inherits(*tasks_to_inherit)
    return core.inherits(*tasks_to_inherit)


def requires(*tasks_to_require):
    # Check the type; if a dictionary call our custom requires decorator
    is_dict = isinstance(tasks_to_require[0], dict)
    if is_dict:
        return dict_requires(*tasks_to_require)
    return core.requires(*tasks_to_require)


class Workflow(object):
    """
    The class is used to orchestrate tasks and define a task pipeline
    """

    def __init__(self, task=None, params=None, path=None, env=None):
        # Set Env 
        if path is not None and env is not None:
            path = str(path) + f"/env={env}"
        # Will overide other tasks with this task's main path
        elif env is not None:
            path = getattr(task, 'path', oryxflow.settings.dirpath)
            path = str(path) + f"/env={env}"

        # Set Params
        self.params = {} if params is None else params
        self.params = self.params if path is None else dict(**self.params, **{'path': path})
        # Add flows to params
        self.params = dict(**self.params, **{'flows': {}})
        # Default Task
        self.default_task = task
        # If Task is set, Try to send Flow path to all other tasks
        if task:
            # Attach to tasks
            if not isinstance(task, (list,)):
                task = [task]
            self._attach_to_tasks(task, path=path)

    def preview(self, tasks=None, indent='', last=True, show_params=True, clip_params=False, print_it=True):
        """
        Preview task flows with the workflow parameters

        Args:
            tasks (class, list): task class or list of tasks class
        """
        if not isinstance(tasks, (list,)):
            tasks = [tasks]
        tasks_inst = [self.get_task(x) for x in tasks]
        return preview(tasks=tasks_inst, indent=indent, last=last, show_params=show_params, clip_params=clip_params, print_it=print_it)

    def run(self, tasks=None, forced=None, forced_all=False, forced_all_upstream=False, confirm=False, workers=1,
            abort=True, execution_summary=None, **kwargs):
        """
        Run tasks with the workflow parameters. Runs the DAG sequentially in dependency order.

        Args:
            tasks (class, list): task class or list of tasks class
            forced (list): list of forced tasks
            forced_all (bool): force all tasks
            forced_all_upstream (bool): force all tasks including upstream
            confirm (list): confirm invalidating tasks
            workers (int): number of workers
            abort (bool): on errors raise exception
            execution_summary (bool): log execution summary
            kwargs: keywords to pass to core.build

        """
        if not isinstance(tasks, (list,)):
            tasks = [tasks]
        tasks_inst = [self.get_task(x) for x in tasks]

        # Before Running if Path/Flow Param is set, Set it to all other tasks
        path_param = None
        flow_param = None
        if 'path' in self.params.keys():
            path_param = self.params['path']
        if self.params['flows']:
            flow_param = self.params['flows']

        # Attach to tasks
        self._attach_to_tasks(tasks, flows=flow_param, path=path_param)

        return run(tasks_inst, forced=forced, forced_all=forced_all, forced_all_upstream=forced_all_upstream,
                   confirm=confirm, workers=workers, abort=abort, execution_summary=execution_summary, **kwargs)

    def outputLoad(self, task=None, keys=None, as_dict=False, cached=False):
        """
        Load output from task with the workflow parameters

        Args:
            task (class): task class
            keys (list): list of data to load
            as_dict (bool): cache data in memory
            cached (bool): cache data in memory

        Returns: list or dict of all task output
        """
        return self.get_task(task).outputLoad(keys=keys, as_dict=as_dict, cached=cached)

    def outputPath(self, task=None):
        """
        Ouputs the Path given a task

        Args:
            task (class): task class

        Returns: list or dict of all task paths
        """
        # Get Output
        output = self.get_task(task).output()

        # If Output is Dict, we have multiple outputs
        if type(output) is dict:
            # Get Paths
            for output_name, output_target in output.items():
                output[output_name] = output_target.path
            
            return output
        else:
            return output.path

    def complete(self, task=None, cascade=True):
        return self.get_task(task).complete(cascade=cascade)

    def output(self, task=None):
        return self.get_task(task).output()

    def outputLoadMeta(self, task=None):
        return self.get_task(task).outputLoadMeta()

    def outputLoadMetaJson(self, task=None):
        return self.get_task(task).outputLoadMetaJson()

    def outputLoadAll(self, task=None, keys=None, as_dict=False, cached=False):
        """
        Load all output from task with the workflow parameters

        Args:
            task (class): task class
            keys (list): list of data to load
            as_dict (bool): cache data in memory
            cached (bool): cache data in memory

        Returns: list or dict of all task output
        """
        task_inst = self.get_task(task)
        data_dict = {}
        tasks = taskflow_upstream(task_inst)
        for task in tasks:
            data_dict[type(task).__name__] = task.outputLoad(keys=keys, as_dict=as_dict, cached=cached)
        return data_dict

    def reset(self, task=None, confirm=False):
        task_inst = self.get_task(task)
        return task_inst.reset(confirm)

    def reset_downstream(self, task, task_downstream=None, confirm=False):
        """
        Invalidate all downstream tasks in a flow.

        For example, you have 3 dependant tasks. Normally you run Task3 but you've changed parameters for Task1. By invalidating Task3 it will check the full DAG and realize Task1 needs to be invalidated and therefore Task2 and Task3 also.

        Args:
            task (obj, class, list): task/family — or list of families — to invalidate downstream
                of. Only the family is used (a class is fine — it is not instantiated), so this
                works for tasks whose params are internal to the DAG (e.g. a per-``country`` task
                you can't name from flow params); a list resets several families at once.
            task_downstream (obj): terminal downstream task the walk stops at. Defaults to the
                flow's default task; must be set (here or as the default task) so it knows where
                "down" ends.
            confirm (bool): confirm operation
        """
        # invalidate_downstream only needs task.task_family (available on the class), so don't
        # instantiate `task` — that would fail for families with DAG-internal params.
        task_downstream_inst = self.get_task(task_downstream)
        return invalidate_downstream(task, task_downstream_inst, confirm)

    def reset_upstream(self, task, confirm=False, only=None):
        task_inst = self.get_task(task)
        return invalidate_upstream(task_inst, confirm, only=only)

    def accept_code(self, task=None):
        """
        Accept an output-equivalent code change for a task and its entire upstream
        dependency tree (see :func:`oryxflow.accept_code`). Defaults to the flow's
        default task, so a bare ``flow.accept_code()`` accepts the whole flow.

        Args:
            task (class): task class (defaults to the flow's default task)

        Returns: list of task_ids re-stamped
        """
        return accept_code(self.get_task(task))

    def set_default(self, task):
        """
        Set default task for the workflow object

        Args:
            task(obj) The task to be set as a default task
        """
        self.default_task = task

    def get_task(self, task=None):
        """
        Get task with the workflow parameters

        Args:
            task(class)

        Retuns: An instance of task class with the workflow parameters
        """
        if task is None:
            if self.default_task is None:
                raise RuntimeError('no default tasks set')
            else:
                task = self.default_task
        return task(**self.params)

    # Add a Flow to the Params of the Workflow
    def attach_flow(self, flow=None, flow_name="flow"):
        if self.params['flows']:
            self.params['flows'][flow_name] = flow
        else:
            self.params['flows'] = {flow_name: flow}

    # Attach Flow/Path to the Tasks
    def _attach_to_tasks(self, tasks, flows=None, path=None):
        # If Both not set
        if not flows and not path:
            return

        # Get all paths
        for t_task in tasks:
            task_inst = self.get_task(t_task)
            tasks = taskflow_upstream(task_inst)
            # Overide param of all tasks
            for temp_task in tasks:
                if flows:
                    temp_task.flows = self.params['flows']
                if path:
                    temp_task.path = self.params['path']

class WorkflowMulti(object):
    """
    A multi experiment workflow can be defined with multiple flows and separate parameters for each flow and a default task. It is mandatory to define the flows and parameters for each of the flows.

    """

    def __init__(self, task=None, params=None, path=None, env=None):
        self.params = params
        self._task_name = task.task_family if task else 'WorkflowMulti default task'
        if params is not None and type(params) not in [dict, list]:
            raise Exception("Params has to be a dictionary with key defining the flow name or a list")
        if type(params) == dict:
            if type(list(params.values())[0]) == list:
                # single-key grid (e.g. {'country': [...]}) -> one flow per value;
                # multi-key grid -> cartesian product of the value lists
                if len(params) == 1:
                    self.params = oryxflow.utils.params_generator_single(params)
                else:
                    self.params = oryxflow.utils.generate_exps_for_multi_param(params)
        if type(params) == list:
            params = {i: v for i, v in enumerate(params)}
            self.params = params
        if params is None or len(params.keys()) == 0:
            raise Exception("Need to pass task parameters or use oryxflow.Workflow")
        self.default_task = task
        if params is not None:
            self.workflow_objs = {k: Workflow(task=task, params=v, path=path, env=env) for k, v in self.params.items()}

    def run(self, tasks=None, flow=None, forced=None, forced_all=False, forced_all_upstream=False, confirm=False,
            workers=1, abort=True, execution_summary=None, **kwargs):
        """
        Run tasks with the workflow parameters for a flow. Runs the DAG sequentially in dependency order.

        Args:
            flow (string): The name of the experiment for which the flow is to be run. If nothing is passed, all the flows are run
            tasks (class, list): task class or list of tasks class
            forced (list): list of forced tasks
            forced_all (bool): force all tasks
            forced_all_upstream (bool): force all tasks including upstream
            confirm (list): confirm invalidating tasks
            workers (int): number of workers
            abort (bool): on errors raise exception
            execution_summary (bool): log execution summary
            kwargs: keywords to pass to core.build

        """

        if flow is not None:
            return self.workflow_objs[flow].run(tasks=tasks, forced=forced, forced_all=forced_all,
                                                forced_all_upstream=forced_all_upstream, confirm=confirm,
                                                workers=workers,
                                                abort=abort,
                                                execution_summary=execution_summary,
                                                **{**{'flow': flow}, **kwargs})
        result = MultiRunResult()
        for exp_name in self.params.keys():
            # each per-flow build gets its own run_id and carries the flow name in its
            # event envelopes (kwargs['flow'] rides through run() into core.build)
            result[exp_name] = self.workflow_objs[exp_name].run(tasks, forced, forced_all, forced_all_upstream,
                                                                confirm, workers, abort,
                                                                execution_summary,
                                                                **{**{'flow': exp_name}, **kwargs})
        return result

    def outputLoad(self, task=None, flow=None, keys=None, as_dict=False, cached=False):
        """
        Load output from task with the workflow parameters for a flow

        Args:
            flow (string): The name of the experiment for which the flow is to be run. If nothing is passed, all the flows are run
            task (class): task class
            keys (list): list of data to load
            as_dict (bool): cache data in memory
            cached (bool): cache data in memory

        Returns: list or dict of all task output
        """
        if flow is not None:
            return self.workflow_objs[flow].outputLoad(task, keys, as_dict, cached)
        data = {}
        for exp_name in self.params.keys():
            data[exp_name] = self.workflow_objs[exp_name].outputLoad(task, keys, as_dict, cached)
        return data

    def outputPath(self, task=None, flow=None):
        """
        Ouputs the Path given a task

        Args:
            task (class): task class
            flow (string): The name of the experiment for which the flow is to be run. If nothing is passed, all the flows are run

        Returns: list or dict of all task paths
        """
        if flow is not None:
            return self.workflow_objs[flow].outputPath(task)

        data = {}
        for exp_name in self.params.keys():
            data[exp_name] = self.workflow_objs[exp_name].outputPath(task)

        return data

    def outputLoadMeta(self, task=None, flow=None):
        if flow is not None:
            return self.workflow_objs[flow].outputLoadMeta(task)
        data = {}
        for exp_name in self.params.keys():
            data[exp_name] = self.workflow_objs[exp_name].outputLoadMeta(task)
        return data

    def outputLoadMetaJson(self, task=None, flow=None):
        if flow is not None:
            return self.workflow_objs[flow].outputLoadMetaJson(task)
        data = {}
        for exp_name in self.params.keys():
            data[exp_name] = self.workflow_objs[exp_name].outputLoadMetaJson(task)
        return data

    def outputLoadAll(self, task=None, flow=None, keys=None, as_dict=False, cached=False):
        """
        Load all output from task with the workflow parameters for a flow

        Args:
            flow (string): The name of the experiment for which the flow is to be run. If nothing is passed, all the flows are run
            task (class): task class
            keys (list): list of data to load
            as_dict (bool): cache data in memory
            cached (bool): cache data in memory

        Returns: list or dict of all task output
        """
        if flow is not None:
            return self.workflow_objs[flow].outputLoadAll(task, keys, as_dict, cached)
        data = {}
        for exp_name in self.params.keys():
            data[exp_name] = self.workflow_objs[exp_name].outputLoadAll(task, keys, as_dict, cached)
        return data

    def outputLoadConcat(self, task=None, keys=None, as_dict=False, cached=False,
                         concat_fn=None, tagkeys=None):
        """Load `task` output for every flow and concatenate into one DataFrame,
        tagging each flow's rows with that flow's raw params."""
        per_flow = self.outputLoad(task=task, keys=keys, as_dict=as_dict, cached=cached)
        items = ((flow, self.params[flow], per_flow[flow]) for flow in self.params.keys())
        return oryxflow.utils.concat_iter(items, concat_fn=concat_fn, keys=tagkeys)

    def _confirm_reset(self, confirm, operation_name="reset"):
        """
        Helper method to handle confirmation logic for reset operations
        
        Args:
            confirm (bool): whether to ask for confirmation
            operation_name (str): name of the operation for the confirmation message
            
        Returns:
            bool: True if confirmed, False otherwise
        """
        if confirm:
            c = input(
                'Confirm invalidating task: {} (y/n). PS You can disable this message by passing confirm=False'.format(
                    self._task_name))
        else:
            c = 'y'
        return c == 'y'

    def reset(self, task=None, flow=None, confirm=False):
        if flow is not None:
            return self.workflow_objs[flow].reset(task, confirm)
        
        # For multiple flows, ask for confirmation once if confirm=True
        if not self._confirm_reset(confirm, "reset"):
            return False
            
        return {self.workflow_objs[exp_name].reset(task, confirm=False) for exp_name in self.params.keys()}

    def reset_downstream(self, task=None, task_downstream=None, flow=None, confirm=False):
        if flow is not None:
            return self.workflow_objs[flow].reset_downstream(task, task_downstream, confirm)

        # For multiple flows, ask for confirmation once if confirm=True
        if not self._confirm_reset(confirm, "reset_downstream"):
            return False

        return {self.workflow_objs[exp_name].reset_downstream(task, task_downstream, confirm=False) for exp_name in self.params.keys()}

    def accept_code(self, task=None, flow=None):
        """
        Accept an output-equivalent code change for a task and its upstream tree
        (see :func:`oryxflow.accept_code`), for one flow or all flows.

        Args:
            task (class): task class (defaults to the flow's default task)
            flow (string): flow name; if not passed, accepts across all flows

        Returns: list of task_ids re-stamped (dict of lists when run for all flows)
        """
        if flow is not None:
            return self.workflow_objs[flow].accept_code(task)
        return {exp_name: self.workflow_objs[exp_name].accept_code(task)
                for exp_name in self.params.keys()}

    def reset_upstream(self, task=None, flow=None, confirm=False, only=None):
        if flow is not None:
            return self.workflow_objs[flow].reset_upstream(task, confirm, only=only)

        # For multiple flows, ask for confirmation once if confirm=True
        if not self._confirm_reset(confirm, "reset_upstream"):
            return False

        return {self.workflow_objs[exp_name].reset_upstream(task, confirm=False, only=only) for exp_name in self.params.keys()}

    def preview(self, tasks=None, flow=None, indent='', last=True, show_params=True, clip_params=False, print_it=True):
        """
        Preview task flows with the workflow parameters for a flow

        Args:
            flow (string): The name of the experiment for which the flow is to be run. If nothing is passed, all the flows are run
            tasks (class, list): task class or list of tasks class
        """
        if not isinstance(tasks, (list,)):
            tasks = [tasks]
        if flow is not None:
            return self.workflow_objs[flow].preview(tasks, print_it=print_it)
        data = {}
        for exp_name in self.params.keys():
            data[exp_name] = self.workflow_objs[exp_name].preview(tasks=tasks, indent=indent, last=last,
                                                                  show_params=show_params, clip_params=clip_params, print_it=print_it)
        return data

    def set_default(self, task):
        """
        Set default task for the workflow. The default task is set for all the experiments

        Args:
            task(obj) The task to be set as a default task
        """
        self.default_task = task
        for exp_name in self.params.keys():
            self.workflow_objs[exp_name].set_default(task)

    def get_task(self, task=None, flow=None):
        """
        Get task with the workflow parameters for a flow

        Args:
            flow (string): The name of the experiment for which the flow is to be run. If nothing is passed, all the flows are run
            task(class): task class

        Retuns: An instance of task class with the workflow parameters
        """
        if task is None:
            if self.default_task is None:
                raise RuntimeError('no default tasks set')
            else:
                task = self.default_task
        if flow is None:
            return {exp_name: self.workflow_objs[exp_name].get_task(task) for exp_name in self.params.keys()}
        return self.workflow_objs[flow].get_task(task)

    def get_flow(self, flow):
        """
        Get flow by name

        Args:
            flow (string): The name of the experiment for which the flow is to be run. If nothing is passed, all the flows are run

        Retuns: An instance of Workflow
        """
        return self.workflow_objs[flow]

class FlowExport(object):
    """
    Auto generate task files to quickly share workflows with others.

    Args:
        tasks (obj): task or list of tasks to share
        flows (obj): flow or list of flows to get tasks from.
        save (bool): save to tasks file
        path_export (str): filename for tasks to export.
    """
    def __init__(self, tasks=None, flows=None, save=False, path_export='tasks_export.py'):

        tasks = [] if tasks is None else tasks
        flows = [] if flows is None else flows
        if not isinstance(tasks, (list,)):
            tasks = [tasks]
        if not isinstance(flows, (list,)):
            flows = [flows]
        for flow in flows:
            task_inst = flow.get_task()
            t_tasks = taskflow_upstream(task_inst)
            for task in t_tasks:
                tasks.append(task)

        self.tasks = tasks
        self.save = save
        self.path_export = path_export

        # file templates
        self.tmpl_tasks = '''
import oryxflow
import datetime

{% for task in tasks -%}

class {{task.name}}({{task.class}}):
    external=True
    persists={{task.obj.persist}}
    {% if task.path -%}
    path="{{task.path}}"
    {% endif -%}
    {% if task.obj.task_group -%}
    task_group="{{task.obj.task_group}}"
    {% endif -%}
    {% for param in task.params -%}
    {{param.name}}={{param.class}}(default={{param.value}})
    {% endfor %}
{% endfor %}
'''

    def generate(self):
        """
        Generate output files
        """
        try:
            from jinja2 import Template
        except ModuleNotFoundError:
            print("module 'jinja2' is not installed. Run: pip install Jinja2")

        tasksPrint = []
        for task in self.tasks:
                if getattr(task, 'export', True):
                    class_ = next(c for c in type(task).__mro__ if 'oryxflow.tasks.' in str(c)) # type(task).__mro__[1]

                    # Get Path
                    task_path = getattr(task, 'path', None)
                    task_path = Path(task_path) if task_path else None

                    # Create Dict
                    taskPrint = {'name': task.__class__.__name__, 'class': class_.__module__ + "." + class_.__name__,
                                    'obj': task, 'persist': task.persist, 'path': task_path,
                                        'params': [{'name': param[0],
                                            'class': f'{param[1].__class__.__module__}.{param[1].__class__.__name__}',
                                            'value': repr(getattr(task,param[0]))} for param in task.get_params()]} # param[1]._default
                    tasksPrint.append(taskPrint)

        tasksPrint[-1], tasksPrint[0:-1] = tasksPrint[0], tasksPrint[1:]

        # Print or Save to File
        if not self.save:
            print(Template(self.tmpl_tasks).render(tasks=tasksPrint))
        else:
            # Write Tasks
            with open(self.path_export, 'w') as fh:
                fh.write(Template(self.tmpl_tasks).render(tasks=tasksPrint))

import importlib.util
import inspect
class FlowImport(object):
    """
    Import a specific module from a directory.

    Args:
        path (str): path to the dir to import from
        module (str): the module name to import
        path_data (str): path to the data file; if not absolute will be appended to path
    """
    def __init__(self, path=None, module=None, path_data=None):
        # INIT
        self.path = path
        self.module = module
        self.tasks = {}
        
        # if path_data is an absolute path, use that, else append to path)
        if Path(path_data).is_absolute():
            self.dirpath = path_data
        else:
            self.dirpath = Path(path) / Path(path_data)
        
        # Check if name ends with .py
        if not str(module).endswith(".py"):
            module = str(module) + ".py"
        # Check if exists
        path_to_file = Path(path) / Path(module)
        if (path_to_file).exists():
            path = Path(path) / Path(module)
        else:
            raise ValueError("Path {} not found.".format((Path(path) / Path(module))))

        # Get Module, Tasks, Dirpath
        self.module_obj = self._module_from_file(module, path)
        self._get_tasks()

    def _get_tasks(self):
        tasks = {}
        for name, obj in inspect.getmembers(self.module_obj):
            if inspect.isclass(obj) and issubclass(obj, oryxflow.tasks.TaskData):
                tasks[name] = obj
        # Convert to DotDict so that we can use .
        self.tasks = dotdict(tasks)

    def _module_from_file(self, module_name, file_path):
        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except:
            print("Module {} not found.".format(module_name))
            return None

# Helper Class
class dotdict(dict):
    """dot.notation access to dictionary attributes"""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


def runLoad(task, params=None, load=True, taskLoad=None, reset=False):

    params = dict() if params is None else params
    taskLoad = task if taskLoad is None else taskLoad

    flow = oryxflow.Workflow(task, params)
    if reset:
        flow.reset(task)
    flow.run()

    if load:
        r = flow.outputLoad(taskLoad)
        return r

def runIt(task, params=None, reset=False):
    return runLoad(task, params=params, reset=reset, load=False)

def runIterConcat(task, params, load=True, taskLoad=None, reset=False,
                  concat_fn=None, tagkeys=None):
    """Run `task` across a grid of params (one flow per param set) and return the
    per-flow outputs concatenated into one DataFrame, each flow tagged with its params."""
    taskLoad = task if taskLoad is None else taskLoad
    flow = oryxflow.WorkflowMulti(task, params)
    if reset:
        flow.reset(task)
    flow.run()
    return flow.outputLoadConcat(taskLoad, concat_fn=concat_fn, tagkeys=tagkeys) if load else flow

