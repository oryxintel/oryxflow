import pickle
import pathlib
import json
import hashlib

from oryxflow import core
from oryxflow.log import logger

import oryxflow.targets
import oryxflow.settings as settings
from oryxflow.cache import data as cache
import oryxflow.cache


class TaskData(core.Task):
    """
    Task which has data as input and output

    Args:
        target_class (obj): target data format
        target_ext (str): file extension
        persists (list): list of strings naming the outputs this task saves.
            Declare it on your task class, e.g. ``persists = ['x', 'y']``.
            ``persist`` (singular) is a backwards-compatible alias for the same
            thing; prefer ``persists``.
        data (dict): data container for all outputs

    """
    target_class = oryxflow.targets.DataTarget
    target_ext = 'ext'
    # canonical internal attribute; users declare it as `persists` (see __init__)
    persist = ['data']
    metadata = None
    # keep outputs of previous code_versions at readable paths (.../Task/v1/...) instead
    # of overwriting in place; only takes effect when code_version is set
    keep_versions = False

    def __init__(self, *args, path=None, flows=None, **kwargs):
        kwargs_ = {k: v for k, v in kwargs.items(
        ) if k in self.get_param_names(include_significant=True)}
        super().__init__(*args, **kwargs_)

        # Check if Child Has Path Var
        self.path = getattr(self, 'path', path)

        # `persists` is the user-facing name; fold it into the internal `persist`.
        # (engine code reads `self.persist` throughout)
        self.persist = getattr(self, 'persists', self.persist)
        
        # Flow
        self.flows = flows

    @classmethod
    def get_param_values(cls, params, args, kwargs):
        kwargs_ = {k: v for k, v in kwargs.items(
        ) if k in cls.get_param_names(include_significant=True)}
        return super(TaskData, cls).get_param_values(params, args, kwargs_)

    def reset(self, confirm=False):
        """
        Reset a task, eg by deleting output file
        """
        return self.invalidate(confirm)

    def invalidate(self, confirm=False):
        """
        Reset a task, eg by deleting output file
        """
        if confirm:
            c = input(
                'Confirm invalidating task: {} (y/n). PS You can disable this message by passing confirm=False'.format(
                    self.__class__.__qualname__))
        else:
            c = 'y'
        if c == 'y':  # and self.complete():
            if self.persist == ['data']:  # 1 data shortcut
                self.output().invalidate()
            else:
                [t.invalidate() for t in self.output().values()]
            self._invalidate_meta()
            logger.debug("invalidated {}", self.task_id)
        return True

    def _invalidate_meta(self):
        # Metadata (saveMeta/saveMetaJson) lives outside output(), so delete it here too.
        meta_base = self._getpath('meta')
        for ext in ('.pickle', '.json'):
            path = self._make_path_cloud_compatible(meta_base.with_suffix(ext))
            try:
                path.unlink()
                logger.debug("invalidated meta {}", path)
            except FileNotFoundError:
                pass  # no metadata was saved for this format


    def complete(self, cascade=True):
        """
        Check if a task is complete: output exists AND the stored code fingerprint
        matches the current one (``_code_ok`` -- a ``code_version`` bump makes the
        task incomplete and forces a rerun; authoritative, unlike the warn-only AST
        source-hash advisory). With ``check_dependencies``, cascades upstream.
        """
        complete = super().complete()
        if complete and not getattr(self, 'external', False):
            complete = self._code_ok()
        if oryxflow.settings.check_dependencies and cascade and not getattr(self, 'external', False):
            complete = complete and all(
                [t.complete() for t in core.flatten(self.requires())])
        return complete

    def _code_ok(self):
        # record-based, two-dimensional completeness: outputs count as complete only while
        # (a) the task's OWN code identity is unchanged -- the explicit code_version when
        # pinned, the stored source hashes when auto (mode-aware, see _own_code_ok) -- and
        # (b) no dependency has rematerialized since this record (_dep_state folds dep
        # output_ids). Both dimensions live in every record, so pinning/unpinning a task
        # with genuinely unchanged code "just resumes" instead of recomputing, and never
        # ripples downstream. Inert (True) when no code identity applies here or upstream,
        # and grandfathering (no record yet) also passes -- build() stamps the baseline.
        fp = self._code_fingerprint
        if fp is None:
            return True
        from oryxflow import state, codehash
        rec = state.get_record(self._resolved_dirpath(), self.task_id)
        if rec is None:
            return True          # grandfathered; build() stamps it
        if rec.get('v') != state.RECORD_V or rec.get('py') != codehash.PY_TAG:
            # record schema or interpreter changed -> not comparable; treat as complete
            # (grandfather trust level), build()'s advisory sweep re-stamps
            return True
        if not self._own_code_ok(rec):
            return False
        return rec.get('dep_state') == self._dep_state()

    def _own_code_ok(self, rec):
        # own dimension, mode-aware: a pin (code_version) is THE version while present;
        # auto compares the source hashes as of the last materialization. Because the
        # hashes are stored on every stamp regardless of mode, removing a pin "just
        # resumes" (no recompute when the code is genuinely unchanged) yet an edit made
        # while pinned-and-unbumped is caught the moment the pin comes off -- and pinning
        # a task in the same change that edits its code forces a rerun instead of
        # blessing the stale output.
        from oryxflow import codehash
        if self.code_version is not None:
            if rec.get('code_version') == self.code_version:
                return True
            if rec.get('code_version') is None and settings.code_version_auto:
                # opting in: free iff the code really is what produced the output
                return rec.get('source_hashes') == codehash.task_hashes(self)
            return False         # pin bump (or unverifiable flip) -> recompute
        if not settings.code_version_auto:
            return True          # own logic untracked in explicit-only mode
        if rec.get('source_hashes') == codehash.task_hashes(self):
            return True
        # expensive-recompute guard: don't silently burn a long run on a code change --
        # stay complete; build()'s advisory warns with the exits (reset/accept/pin)
        thresh = getattr(settings, 'code_version_auto_expensive_s', None)
        if thresh and (rec.get('duration_s') or 0) > thresh:
            return True
        return False

    def _dep_state(self):
        # folded output identity of the direct deps: each dep's record output_id, a fresh
        # id per actual materialization that re-stamps and accept_code PRESERVE. Downstream
        # therefore reruns when an upstream actually recomputed -- and only then: mode
        # toggles and accepts upstream don't ripple, while a reset+rerun upstream
        # propagates even across separate builds.
        from oryxflow import state
        ids = []
        for d in self.deps():
            dirpath = d._resolved_dirpath() if hasattr(d, '_resolved_dirpath') \
                else settings.dirpath
            rec = state.get_record(dirpath, d.task_id)
            ids.append((rec or {}).get('output_id') or '')
        return hashlib.md5('|'.join(sorted(ids)).encode('utf-8')).hexdigest()[:16]

    def _resolved_dirpath(self):
        # the data directory this task's artifacts (and code-invalidation records) live in
        if self.path is not None:
            return pathlib.Path(self.path)
        return settings.dirpath

    # Private Get Path Function
    def _getpath(self, k, subdir=True):
        # Get Output dir
        dirpath = self._resolved_dirpath()

        # Add Group
        if hasattr(self, 'task_group'):
            dirpath = dirpath / f"/group={getattr(self, 'task_group')}"

        # Get Path
        tidroot = getattr(self, 'target_dir', self.task_id.split('_')[0])
        if getattr(self, 'keep_versions', False) and self.code_version is not None:
            tidroot = '{}/v{}'.format(
                tidroot, core.TASK_ID_INVALID_CHAR_REGEX.sub('_', str(self.code_version)))
        fname = '{}-{}'.format(self.task_id, k) if (settings.save_with_param and getattr(
            self, 'save_attrib', True)) else '{}'.format(k)
        fname += '.{}'.format(self.target_ext)
        if subdir:
            path = dirpath / tidroot / fname
        else:
            path = dirpath / fname

        # use cloud storage
        if settings.cloud_fs_enabled:
            from pathlib import PurePosixPath  # needed on windows
            path = f'{settings.cloud_fs_prefix}/{PurePosixPath(path)}'

        return path

    def output(self):
        """
        Output target(s) this task produces
        """
        save_ = getattr(self, 'persist', [])
        output = dict([(k, self.target_class(self._getpath(k)))
                       for k in save_])
        if self.persist == ['data']:  # 1 data shortcut
            output = output['data']
        return output

    def inputLoad(self, keys=None, task=None, cached=False, as_dict=False):
        """
        Load all or several outputs from task

        Args:
            keys (list): list of data to load
            task (str): if requires multiple tasks load that task 'input1' for eg `def requires: {'input1':Task1(), 'input2':Task2()}`
            cached (bool): cache data in memory
            as_dict (bool): if the inputs were saved as a dictionary. use this to return them as dictionary. 
        Returns: list or dict of all task output
        """

        if task is not None:
            input = self.input()[task]
        else:
            input = self.input()

        requires = self.requires()
        type_of_requires = type(requires)

        if isinstance(input, dict):
            keys = input.keys() if keys is None else keys
            data = {}
            for k, v in input.items():
                if k in keys:
                    if type(v) == dict:
                        if as_dict:
                            data[k] = {k: v.load(cached) for k, v in v.items()}
                        else:
                            data[k] = [v.load(cached) for k, v in v.items()]
                    else:
                        data[k] = v.load(cached)
            # Return DF if Single Key
            if isinstance(keys, str) and not as_dict:
                return data[keys]
            # Convert to list if dependecy is Single
            if (type_of_requires != dict or task is not None) and not as_dict:
                data = list(data.values())
        elif isinstance(input, list):
            data = []
            for _target in input:
                if isinstance(_target, dict):
                    if as_dict:
                        data.append({k: v.load(cached)
                                     for k, v in _target.items()})
                    else:
                        data.append([v.load(cached)
                                     for _, v in _target.items()])
                else:
                    data.append(_target.load(cached))
        else:
            data = input.load()

        logger.debug("loaded input for {} keys={}", self.task_id,
                     list(keys) if keys is not None else None)
        return data

    def inputLoadConcat(self, keys=None, tag=True, tagkeys=None, as_dict=False,
                        concat_fn=None, cached=False):
        """Load every dependency and concatenate into one DataFrame. Works for the dict form of
        requires() ({key: Task(...)}) and the list/positional form. By default each dependency's
        significant params are added as columns. concat_fn(identifier, params, df)->df overrides."""
        requires = self.requires()
        if isinstance(requires, dict):
            items = list(requires.items())        # (key, task)
        elif isinstance(requires, (list, tuple)):
            items = list(enumerate(requires))     # (index, task)
        else:
            items = [(None, requires)]            # single dep
        def _gen():
            for ident, dep in items:
                data = self.inputLoad(keys=keys, task=ident, as_dict=as_dict, cached=cached)
                params = {n: getattr(dep, n) for n in dep.get_param_names()} if tag else {}
                yield ident, params, data
        import oryxflow.utils
        return oryxflow.utils.concat_iter(_gen(), concat_fn=concat_fn, keys=tagkeys)

    def outputLoad(self, keys=None, as_dict=False, cached=False):
        """
        Load all or several outputs from task

        Args:
            keys (list): list of data to load
            as_dict (bool): cache data in memory
            cached (bool): cache data in memory

        Returns: list or dict of all task output
        """
        if not self.complete(cascade=False):
            raise RuntimeError(
                f'Cannot load {self.__class__}, task not complete, run flow first')

        # Check Keys is not empty
        keys = self.persist if keys is None else keys
        # Not List
        if type(keys) is not list:
            if not keys in self.persist:
                raise IndexError('Key name does not match')
        else:
            for key in keys:
                if not key in self.persist:
                    raise IndexError('Key name does not match')

        logger.debug("loaded output for {} keys={}", self.task_id,
                     keys if isinstance(keys, list) else [keys])

        if self.persist == ['data']:  # 1 data shortcut
            persist_data = self.output().load()
            return persist_data

        # Get Data
        data = {k: v.load(cached)
                for k, v in self.output().items() if k in keys}
        
        # Return As List
        if not as_dict:
            data = list(data.values())
        # If Keys is not a list
        if type(keys) is not list:
            data = data[0]
        
        # Return
        return data

    def save(self, data, from_list=False, **kwargs):
        """
        Persist data to target

        Args:
            data (dict): data to save. keys are the self.persist keys and values is data

        """

        if self.persist == ['data']:  # 1 data shortcut
            self.output().save(data, **kwargs)
        else:
            targets = self.output()
            if from_list:
                data = dict(zip(self.persist, data))
            if not set(data.keys()) == set(targets.keys()):
                raise ValueError(
                    'Save dictionary needs to consistent with Task.persist')
            for k, v in data.items():
                targets[k].save(v, **kwargs)
        logger.debug("saved {} keys={}", self.task_id, list(self.persist))

    def _get_meta_path_with_format(self, task, format='pickle'):
        """Get metadata path for a given task and format"""
        if format == 'pickle':
            return self._get_meta_path(task)
        else:  # json
            return task._getpath('meta').with_suffix('.json')

    def _make_path_cloud_compatible(self, path):
        """Convert path to cloud-compatible path if cloud storage is enabled"""
        if settings.cloud_fs_enabled:
            import upath
            return upath.UPath(path)
        return pathlib.Path(path)

    def _save_meta_internal(self, data, format='pickle'):
        """Internal method to save metadata in specified format"""
        self.metadata = data
        if format == 'pickle':
            path = self._get_meta_path(self)
            path = self._make_path_cloud_compatible(path)
            with path.open("wb") as fh:
                pickle.dump(data, fh)
        else:  # json
            path = self._getpath('meta').with_suffix('.json')
            path = self._make_path_cloud_compatible(path)
            path.parent.mkdir(exist_ok=True, parents=True)
            with path.open("w") as fh:
                json.dump(data, fh)

    def _load_meta_from_task(self, task, format='pickle'):
        """Load metadata from a single task"""
        if format == 'pickle':
            path = self._get_meta_path(task)
            path = self._make_path_cloud_compatible(path)
            with path.open("rb") as fh:
                return pickle.load(fh)
        else:  # json
            path = task._getpath('meta').with_suffix('.json')
            path = self._make_path_cloud_compatible(path)
            with path.open("r") as fh:
                return json.load(fh)

    def _input_load_meta_internal(self, key=None, format='pickle'):
        """Internal method to load metadata from input tasks"""
        inputs = self.requires()

        if key is not None:
            return self._load_meta_from_task(inputs[key], format)
        elif isinstance(inputs, dict):
            return {k: self._load_meta_from_task(v, format) for k, v in inputs.items()}
        elif isinstance(inputs, list):
            return [self._load_meta_from_task(task, format) for task in inputs]
        else:
            return self._load_meta_from_task(inputs, format)

    def metaSave(self, data):
        self._save_meta_internal(data, format='pickle')

    def saveMeta(self, data):
        self.metaSave(data)

    def saveMetaJson(self, data):
        self._save_meta_internal(data, format='json')

    def metaLoad(self, key=None):
        return self._input_load_meta_internal(key, format='pickle')

    def inputLoadMetaJson(self, key=None):
        return self._input_load_meta_internal(key, format='json')

    def outputLoadMeta(self):
        if not self.complete(cascade=False):
            raise RuntimeError(
                'Cannot load, task not complete, run flow first')
        try:
            return self._load_meta_from_task(self, format='pickle')
        except FileNotFoundError:
            raise RuntimeError(
                f"No metadata to load for task {self.task_family}")

    def outputLoadMetaJson(self):
        if not self.complete(cascade=False):
            raise RuntimeError(
                'Cannot load, task not complete, run flow first')
        try:
            return self._load_meta_from_task(self, format='json')
        except FileNotFoundError:
            raise RuntimeError(
                f"No metadata to load for task {self.task_family}")

    def outputLoadAllMeta(self):
        if not self.complete(cascade=False):
            raise RuntimeError(
                'Cannot load, task not complete, run flow first')
        tasks = oryxflow.taskflow_upstream(self, only_complete=True)
        meta = []
        for task in tasks:
            try:
                meta.append(task.outputLoadMeta())
            except:
                tasks.remove(task)
        tasks = [task.task_family for task in tasks]
        return dict(zip(tasks, meta))

    def _get_meta_path(self, task):
        # Get Meta Path
        meta_path = task._getpath('meta').with_suffix('.pickle')
        meta_path.parent.mkdir(exist_ok=True, parents=True)
        return meta_path


class TaskCache(TaskData):
    """
    Task which saves to cache
    """
    target_class = oryxflow.targets.CacheTarget
    target_ext = 'cache'


class TaskCachePandas(TaskData):
    """
    Task which saves to cache pandas dataframes
    """
    target_class = oryxflow.targets.PdCacheTarget
    target_ext = 'cache'


class TaskJson(TaskData):
    """
    Task which saves to json
    """
    target_class = oryxflow.targets.JsonTarget
    target_ext = 'json'


class TaskPickle(TaskData):
    """
    Task which saves to pickle
    """
    target_class = oryxflow.targets.PickleTarget
    target_ext = 'pkl'


class TaskCSVPandas(TaskData):
    """
    Task which saves to CSV
    """
    target_class = oryxflow.targets.CSVPandasTarget
    target_ext = 'csv'


class TaskCSVGZPandas(TaskData):
    """
    Task which saves to CSV
    """
    target_class = oryxflow.targets.CSVGZPandasTarget
    target_ext = 'csv.gz'


class TaskExcelPandasSingle(TaskData):
    """
    Task which saves each persist key as a separate Excel file
    """
    target_class = oryxflow.targets.ExcelPandasTarget
    target_ext = 'xlsx'


class TaskExcelPandas(TaskData):
    """
    Task which saves multiple dataframes as sheets in a single Excel file
    """
    target_class = oryxflow.targets.ExcelPandasSheetsTarget
    target_ext = 'xlsx'

    def output(self):
        return self.target_class(self._getpath('data'))

    def save(self, data, from_list=False, **kwargs):
        if self.persist == ['data']:
            data = {'data': data}
        else:
            if from_list:
                data = dict(zip(self.persist, data))
            if not set(data.keys()) == set(self.persist):
                raise ValueError(
                    'Save dictionary needs to be consistent with Task.persist')
        self.output().save(data, **kwargs)

    def outputLoad(self, keys=None, as_dict=False, cached=False):
        if not self.complete(cascade=False):
            raise RuntimeError(
                f'Cannot load {self.__class__}, task not complete, run flow first')

        if self.persist == ['data']:
            return self.output().load(keys='data', cached=cached)

        if keys is not None:
            # Validate keys
            check_keys = [keys] if isinstance(keys, str) else keys
            for key in check_keys:
                if key not in self.persist:
                    raise IndexError('Key name does not match')

        data = self.output().load(keys=keys, cached=cached)

        # keys=str: target returns single df directly
        if isinstance(keys, str):
            return data

        # keys=None or keys=list: target returns dict
        if as_dict:
            return data
        return list(data.values())

    def invalidate(self, confirm=False):
        if confirm:
            c = input(
                'Confirm invalidating task: {} (y/n). PS You can disable this message by passing confirm=False'.format(
                    self.__class__.__qualname__))
        else:
            c = 'y'
        if c == 'y':
            self.output().invalidate()
        return True


class TaskPqPandas(TaskData):
    """
    Task which saves to parquet
    """
    target_class = oryxflow.targets.PqPandasTarget
    target_ext = 'parquet'


class TaskMarkdown(TaskData):
    """
    Task which saves to markdown and HTML
    """
    target_class = oryxflow.targets.MarkdownTarget
    target_ext = 'md'


class TaskAggregator(core.Task):
    """
    Task which yields other tasks

    NB: Use this function by implementing `run()` which should do nothing but yield other tasks

    example::

        class TaskCollector(oryxflow.tasks.TaskAggregator):
            def run(self):
                yield Task1()
                yield Task2()

    """

    def reset(self, confirm=False):
        return self.invalidate(confirm=confirm)

    def deps(self):
        # aggregator contract: run() only yields tasks. Folding them into deps() lets
        # code_version bumps propagate through the aggregator's fingerprint.
        return core.flatten([t for t in self.run()])

    def invalidate(self, confirm=False):
        [t.invalidate(confirm) for t in self.run()]

    def complete(self, cascade=True):
        return all([t.complete(cascade) for t in self.run()])

    def output(self):
        return [t.output() for t in self.run()]

    def outputLoad(self, keys=None, as_dict=False, cached=False):
        return [t.outputLoad(keys, as_dict, cached) for t in self.run()]
