"""TaskNotebook: a task whose output is an executed, parameterized report notebook.

Needs the ``notebook`` extra (papermill, nbconvert, nbformat, ipykernel, jinja2). Not imported by
``oryxflow.tasks``, so the core install never imports papermill.
"""
import hashlib
import json
import logging
import os
import pathlib

try:
    import papermill as pm
    import nbformat
    import nbconvert  # noqa: F401  (needed by the target's export)
except ImportError as e:
    raise ImportError("oryxflow.tasks.notebook needs the 'notebook' extra: "
                      "pip install oryxflow[notebook]") from e

import oryxflow.report
from oryxflow import codehash, settings
from oryxflow.parameter import EnumParameter
from oryxflow.tasks import TaskData
from oryxflow.targets.notebook import NotebookTarget

_RESERVED_PREFIX = 'oryxflow_'


def _template_digest(path):
    """Digest of what in a template changes the result: each cell's type, source and tags, in
    order. Outputs, execution counts and kernel metadata are left out, so running and saving the
    template in Jupyter does not count as an edit."""
    with open(path, encoding='utf-8') as f:
        nb = json.load(f)
    cells = []
    for c in nb.get('cells', []):
        src = c.get('source', '')
        if isinstance(src, list):
            src = ''.join(src)
        tags = sorted((c.get('metadata') or {}).get('tags') or [])
        cells.append([c.get('cell_type'), src, tags])
    blob = json.dumps(cells, sort_keys=True)
    return hashlib.md5(blob.encode('utf-8')).hexdigest()[:16]


def _json_default(v):
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    if hasattr(v, 'item'):
        return v.item()
    return str(v)


class _DropOryxflowUnknown(logging.Filter):
    """Drops papermill's "Passed unknown parameter" warning for oryxflow's own injected names,
    which a template need not declare. Warnings about the user's names stay."""
    def filter(self, record):
        msg = record.getMessage()
        return not (msg.startswith('Passed unknown parameter') and _RESERVED_PREFIX in msg)


def _has_parameters_cell(path):
    nb = nbformat.read(str(path), as_version=4)
    return any('parameters' in (c.get('metadata', {}).get('tags') or []) for c in nb.cells)


class TaskNotebook(TaskData):
    """Task whose output is an executed, parameterized notebook and its HTML report.

    Set ``notebook`` to a template path (relative to the working directory). The template needs a
    cell tagged ``parameters``. ``run()`` injects every task parameter under its own name, the
    keys of ``notebook_parameters()``, and ``oryxflow_inputs`` / ``oryxflow_task_id`` /
    ``oryxflow_task_family``; the notebook reads its inputs with
    ``oryxflow.report.inputLoad(task=...)``. The output is the executed ``.ipynb`` plus a
    code-free ``.html``; ``outputLoad()`` returns the html path.

    The task reruns when its parameters, its upstream data, its Python code or the template's
    cells change, and not when only the template's saved outputs change.
    """
    target_class = NotebookTarget
    target_ext = 'ipynb'
    # persist stays TaskData's default ['data']: exactly one output

    notebook = None
    kernel_name = 'python3'
    html_template = 'classic'
    html_include_code = False

    def notebook_parameters(self):
        """Extra values to inject into the template, merged over the task parameters."""
        return {}

    def _template_file(self):
        if self.notebook is None:
            raise TypeError('{} must set notebook = "<template>.ipynb"'.format(type(self).__name__))
        path = pathlib.Path(self.notebook)
        if not path.is_absolute():
            path = pathlib.Path.cwd() / path
        return path

    def _template_path(self):
        path = self._template_file()
        if not path.exists():
            raise FileNotFoundError('template notebook not found: {}'.format(path))
        return path

    def code_version(self):
        """Code identity: the template's cells plus the task's own Python (when auto hashing is on).

        A template that cannot be found counts as a change, so the task reports incomplete and
        ``run()`` then raises the error naming the path."""
        path = self._template_file()
        return {'template': _template_digest(path) if path.exists() else 'missing',
                'python': codehash.task_code_hash(self) if settings.code_version_auto else None}

    def _parameters(self):
        params = {}
        for name, param in self.get_params():
            v = getattr(self, name)
            if isinstance(param, EnumParameter) and v is not None:
                v = v.name
            params[name] = json.loads(json.dumps(v, default=_json_default))
        extra = json.loads(json.dumps(self.notebook_parameters() or {}, default=_json_default))
        params.update(extra)
        clash = sorted(k for k in params if k.startswith(_RESERVED_PREFIX))
        if clash:
            raise ValueError('{}: parameter names starting with {!r} are reserved for oryxflow: {}'.format(
                type(self).__name__, _RESERVED_PREFIX, ', '.join(clash)))
        params['oryxflow_inputs'] = oryxflow.report.inputs_of(self)
        params['oryxflow_task_id'] = self.task_id
        params['oryxflow_task_family'] = self.task_family
        return params

    def run(self):
        """Execute the template with papermill and save the notebook and its HTML report."""
        if self.persist != ['data']:
            raise ValueError('TaskNotebook has exactly one output; remove persists from {}'.format(
                type(self).__name__))
        template = self._template_path()
        if not _has_parameters_cell(template):
            raise ValueError('template {} has no cell tagged "parameters"'.format(template))
        parameters = self._parameters()

        target = self.output()
        target.path.parent.mkdir(parents=True, exist_ok=True)
        running = target.path.with_name(target.path.stem + '.running.ipynb')
        failed = target.path.with_name(target.path.stem + '.failed.ipynb')
        for p in (failed, running):
            if p.exists():
                p.unlink()

        self.logger.info("notebook {} -> {}", template, target.html_path)
        self.logger.debug("notebook parameters: {}", sorted(parameters))
        pm_logger = logging.getLogger('papermill')
        drop = _DropOryxflowUnknown()
        pm_logger.addFilter(drop)
        try:
            pm.execute_notebook(str(template), str(running), parameters=parameters,
                                cwd=os.getcwd(), kernel_name=self.kernel_name, language='python',
                                progress_bar=False, log_output=False,
                                request_save_on_cell_execute=False)
        except Exception:
            if running.exists():
                running.replace(failed)
            raise
        finally:
            pm_logger.removeFilter(drop)
        self.save(running, html_template=self.html_template, include_code=self.html_include_code)
