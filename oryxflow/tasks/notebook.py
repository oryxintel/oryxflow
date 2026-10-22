"""TaskNotebook: a task whose output is an executed, parameterized report notebook.

Needs the ``notebook`` extra (papermill, nbconvert, nbformat, ipykernel, jinja2). Not imported by
``oryxflow.tasks``, so the core install never imports papermill.
"""
import ast
import hashlib
import json
import logging
import os
import pathlib
import re

try:
    import papermill as pm
    import nbformat
    import nbconvert  # noqa: F401  (needed by the target's export)
except ImportError as e:
    raise ImportError("oryxflow.tasks.notebook needs the 'notebook' extra: "
                      "pip install oryxflow[notebook]") from e

import oryxflow.report
import oryxflow.utils
from oryxflow import codehash, core, settings
from oryxflow.parameter import EnumParameter
from oryxflow.tasks import TaskData
from oryxflow.targets.notebook import NotebookTarget

_RESERVED_PREFIX = 'oryxflow_'
_ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
_PARAMETERS_COMMENT = re.compile(r'^#\s*parameters\s*$', re.IGNORECASE)
_MAGIC = re.compile(r'^\s*[%!]')
_CELL_LINE = re.compile(r'Cell In\[(\d+)\], line (\d+)')

# published path -> task_id within one build: two reports in one run writing one deliverable
# is a mistake (a fan-out whose publish pattern misses the fanned parameter); a later build
# overwriting it with new parameters (a new vintage) is not. The build is told apart by the
# identity of the engine's per-traversal memo, which lives exactly as long as the build.
_published = {'build': None, 'paths': {}}


def strip_ansi(text):
    """Text without terminal color codes (a Jupyter traceback is full of them)."""
    return _ANSI.sub('', text)


def _read_cells(path):
    with open(path, encoding='utf-8') as f:
        nb = json.load(f)
    cells = []
    for c in nb.get('cells', []):
        src = c.get('source', '')
        if isinstance(src, list):
            src = ''.join(src)
        cells.append((c.get('cell_type'), src, sorted((c.get('metadata') or {}).get('tags') or [])))
    return cells


def _template_digest(cells):
    """Digest of what in a template changes the result: each cell's type, source and tags, in
    order. Outputs, execution counts and kernel metadata are left out, so running and saving the
    template in Jupyter does not count as an edit."""
    blob = json.dumps([list(c) for c in cells], sort_keys=True)
    return hashlib.md5(blob.encode('utf-8')).hexdigest()[:16]


def _cell_imports(src):
    """(module, level, names) for every import statement in one code cell. IPython magics and
    shell lines are skipped; a cell that still does not parse is read line by line."""
    lines = [l for l in src.splitlines() if not _MAGIC.match(l)]
    try:
        trees = [ast.parse('\n'.join(lines))]
    except SyntaxError:
        trees = []
        for l in lines:
            try:
                trees.append(ast.parse(l.strip()))
            except SyntaxError:
                pass
    out = []
    for tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out.extend((a.name, 0, ()) for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                out.append((node.module or '', node.level, tuple(a.name for a in node.names)))
    return out


def _module_files(modname, bases):
    """Source files a module import executes, found under ``bases`` without importing anything:
    each package's ``__init__.py`` on the way, then the module itself."""
    files = []
    for base in bases:
        parts = modname.split('.') if modname else []
        here = pathlib.Path(base)
        found = []
        for i, part in enumerate(parts):
            here = here / part
            if (here / '__init__.py').is_file():
                found.append(here / '__init__.py')
            elif i == len(parts) - 1 and here.with_suffix('.py').is_file():
                found.append(here.with_suffix('.py'))
            elif not here.is_dir():
                found = []
                break
        if found:
            files.extend(found)
            break
    return files


_wiring_cache = {}


def _is_wiring(path):
    """True for a module that defines tasks or a flow (``tasks.py``, ``flow.py``): a template
    imports it to build its inputs by hand, and its effect on the report already arrives through
    the parameters and the upstream data. Watching it would rerun every report on any task edit."""
    try:
        key = (str(path), os.stat(path).st_mtime_ns)
    except OSError:
        return False
    if key not in _wiring_cache:
        try:
            tree = ast.parse(pathlib.Path(path).read_bytes())
        except (SyntaxError, ValueError):
            tree = ast.Module(body=[], type_ignores=[])
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                heads = [ast.unparse(b) for b in node.bases + node.decorator_list]
                found = any('oryxflow' in h or h.split('.')[-1].startswith('Task') for h in heads)
            elif isinstance(node, ast.Call):
                found = ast.unparse(node.func).split('.')[-1] in ('Workflow', 'WorkflowMulti')
            if found:
                break
        _wiring_cache[key] = found
    return _wiring_cache[key]


def _local_imports(imports, bases, root):
    """``{relpath: digest}`` of the project-local modules named by ``imports``, and of the local
    modules those import in turn. Installed packages are outside ``root`` and never counted, nor
    are modules that define tasks or a flow (``_is_wiring``), nor what only those import."""
    root = pathlib.Path(root).resolve()
    queue = [(m, lvl, names, None) for m, lvl, names in imports]
    seen, out = set(), {}
    while queue:
        mod, level, names, importer = queue.pop()
        if level:
            if importer is None:
                continue                         # a relative import in a notebook cannot run
            base = importer.parent
            for _ in range(level - 1):
                base = base.parent
            search = [base]
        else:
            search = bases
        files = _module_files(mod, search)
        for n in names:                          # `from pkg import mod`, mod a submodule
            files += _module_files('{}.{}'.format(mod, n) if mod else n, search)
        for f in files:
            f = f.resolve()
            if f in seen or not codehash._is_local(f, root):
                continue
            seen.add(f)
            if _is_wiring(f):
                continue
            digest = codehash.file_hash(f)
            if digest is None:
                continue
            out[f.relative_to(root).as_posix()] = digest
            queue.extend((m, lvl, ns, f) for m, lvl, ns in codehash._imports_of(f))
    return out


def _digest(hashes):
    blob = '|'.join('{}={}'.format(k, hashes[k]) for k in sorted(hashes))
    return hashlib.md5(blob.encode('utf-8')).hexdigest()[:16]


def _json_default(v):
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    if hasattr(v, 'item'):
        return v.item()
    return str(v)


class _DropOryxflowUnknown(logging.Filter):
    """Drops papermill's "Passed unknown parameter" warning for oryxflow's own injected names
    and for the task's parameters: a report inherits every parameter of its upstream band and
    the template need not declare the ones it does not use. Warnings about the names
    ``notebook_parameters()`` adds stay, since those are passed for the template."""
    def __init__(self, quiet=()):
        super().__init__()
        self.quiet = set(quiet)

    def filter(self, record):
        msg = record.getMessage()
        if not msg.startswith('Passed unknown parameter'):
            return True
        name = msg.rpartition(':')[2].strip()
        return not (name.startswith(_RESERVED_PREFIX) or name in self.quiet)


def _has_parameters_tag(nb):
    return any('parameters' in (c.get('metadata', {}).get('tags') or []) for c in nb.cells)


def _parameters_cell(nb):
    """The cell tagged ``parameters``, else the first code cell whose first line is
    ``# parameters`` (tagged here, in memory: papermill only knows the tag). None if neither."""
    for c in nb.cells:
        if 'parameters' in (c.get('metadata', {}).get('tags') or []):
            return c
    for c in nb.cells:
        lines = [l for l in c.source.splitlines() if l.strip()]
        if c.cell_type == 'code' and lines and _PARAMETERS_COMMENT.match(lines[0].strip()):
            c.metadata.setdefault('tags', []).append('parameters')
            return c
    return None


class NotebookCellError(pm.PapermillExecutionError):
    """A template cell raised. The message names the cell, its failing line, the error and the
    saved ``.failed.ipynb``, without terminal color codes; the kernel's full traceback (also
    without color codes) is in ``traceback`` and in the chained cause."""

    def __init__(self, err, template, failed_path):
        tb = [strip_ansi(t) for t in (err.traceback or [])]
        super().__init__(err.cell_index, err.exec_count, err.source, err.ename,
                         strip_ansi(str(err.evalue)), tb)
        self.template = pathlib.Path(template)
        self.failed_path = pathlib.Path(failed_path)

    def failing_line(self):
        """The source line of the template cell that raised, or None."""
        lines = (self.source or '').splitlines()
        for m in _CELL_LINE.finditer('\n'.join(self.traceback)):
            if m.group(1) == str(self.exec_count) and 0 < int(m.group(2)) <= len(lines):
                return lines[int(m.group(2)) - 1].strip()
        return None

    def __str__(self):
        line = self.failing_line()
        return '{} failed in cell In[{}]{}: {}: {}\n  executed notebook up to the failure: {}'.format(
            self.template.name, self.exec_count,
            ', at `{}`'.format(line) if line else '', self.ename, self.evalue, self.failed_path)


class _KernelTraceback(Exception):
    """The kernel's traceback as plain text, chained under a NotebookCellError."""


class TaskNotebook(TaskData):
    """Task whose output is an executed, parameterized notebook and its HTML report.

    Set ``notebook`` to a template path (relative to the working directory). The template needs a
    cell tagged ``parameters``, or a code cell whose first line is ``# parameters``. ``run()``
    injects every task parameter under its own name, the keys of ``notebook_parameters()``, and
    ``oryxflow_inputs`` / ``oryxflow_task_id`` / ``oryxflow_task_family``; the notebook reads its
    inputs with ``oryxflow.report.inputLoad(task=...)``. The output is the executed ``.ipynb``
    plus a code-free ``.html``; ``outputLoad()`` returns the html path.

    ``publish`` keeps a copy under a readable name, a path pattern filled with the parameters:
    ``publish = 'reports/render/viz-{market}-performance.html'`` (a list for several; a
    ``.ipynb`` pattern gets the executed notebook). ``report_title`` (same pattern form) sets the
    html page title; it defaults to the published file's name, else the task family.

    The task reruns when its parameters, its upstream data, its Python code (with auto
    invalidation on), the template's cells, or the project modules the template imports change,
    and not when only the template's saved outputs change. ``template_deps`` lists file globs to
    watch instead of the imports (include ``'auto'`` to keep them too); ``None`` watches only the
    template.
    """
    target_class = NotebookTarget
    target_ext = 'ipynb'
    # persist stays TaskData's default ['data']: exactly one output

    notebook = None
    kernel_name = 'python3'
    html_template = 'classic'
    html_include_code = False
    template_deps = 'auto'
    publish = None
    report_title = None

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

    def _deps_version(self, path, cells):
        """``{'imports': ..., 'deps': ...}``, each key present only when it watches something,
        so a template without local imports keeps the code identity it had before."""
        deps = self.template_deps
        if deps is None:
            return {}
        deps = [deps] if isinstance(deps, str) else list(deps)
        out = {}
        if 'auto' in deps:
            imports = [i for kind, src, _ in cells if kind == 'code' for i in _cell_imports(src)]
            bases = list(dict.fromkeys([pathlib.Path.cwd(), path.parent]))
            hashes = _local_imports(imports, bases, codehash._project_root(path))
            if hashes:
                out['imports'] = _digest(hashes)
        globs = [d for d in deps if d != 'auto']
        if globs:
            out['deps'] = oryxflow.utils.hash_files(*globs)
        return out

    def code_version(self):
        """Code identity: the template's cells, the project modules it imports (or
        ``template_deps``) and the task's own Python (when auto hashing is on).

        A template that cannot be found counts as a change, so the task reports incomplete and
        ``run()`` then raises the error naming the path."""
        path = self._template_file()
        if not path.exists():
            return {'template': 'missing',
                    'python': codehash.task_code_hash(self) if settings.code_version_auto else None}
        cells = _read_cells(path)
        return {'template': _template_digest(cells),
                'python': codehash.task_code_hash(self) if settings.code_version_auto else None,
                **self._deps_version(path, cells)}

    def _param_values(self):
        values = {name: getattr(self, name) for name, _ in self.get_params()}
        values.update(self.notebook_parameters() or {})
        return values

    def _fill(self, pattern, what):
        try:
            return pattern.format(**self._param_values())
        except (KeyError, IndexError) as e:
            raise ValueError('{}.{} = {!r} names {} but the parameters are: {}'.format(
                type(self).__name__, what, pattern, e,
                ', '.join(sorted(self._param_values())) or 'none')) from None

    def _publish_paths(self):
        """The published copies for these parameters (relative to the working directory)."""
        patterns = self.publish or []
        patterns = [patterns] if isinstance(patterns, (str, pathlib.PurePath)) else list(patterns)
        paths = []
        for p in patterns:
            path = pathlib.Path(self._fill(str(p), 'publish'))
            if path.suffix.lower() not in ('.html', '.ipynb'):
                raise ValueError('{}.publish: {} must end in .html or .ipynb'.format(
                    type(self).__name__, path))
            paths.append(path if path.is_absolute() else pathlib.Path.cwd() / path)
        return paths

    def _report_title(self):
        """The html page title."""
        if self.report_title:
            return self._fill(self.report_title, 'report_title')
        html = [p for p in self._publish_paths() if p.suffix.lower() == '.html']
        return html[0].stem if html else self.task_family

    def output(self):
        target = super().output()
        if isinstance(target, NotebookTarget):     # persists is rejected in run()
            target.publish = self._publish_paths()
            target.title = self._report_title()
        return target

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

    def _claim_publish(self, target):
        build = core._traversal.requires
        if build is None:
            return
        if _published['build'] is not build:
            _published.update(build=build, paths={})
        for p in target.publish:
            owner = _published['paths'].setdefault(str(p.resolve()), self.task_id)
            if owner != self.task_id:
                raise ValueError(
                    '{}: {} and {} both publish to {}; put the parameter that tells them apart '
                    'in the publish pattern ({!r})'.format(
                        type(self).__name__, owner, self.task_id, p, self.publish))

    def run(self):
        """Execute the template with papermill and save the notebook and its HTML report."""
        if self.persist != ['data']:
            raise ValueError('TaskNotebook has exactly one output; remove persists from {}'.format(
                type(self).__name__))
        template = self._template_path()
        nb = nbformat.read(str(template), as_version=4)
        # papermill needs the parameters tag and a kernelspec; a template written with nbformat
        # or tagged by a '# parameters' comment gets them in memory, the file is left alone
        as_is = _has_parameters_tag(nb) and 'kernelspec' in nb.metadata
        if 'kernelspec' not in nb.metadata:
            nb.metadata['kernelspec'] = {'name': self.kernel_name, 'language': 'python',
                                         'display_name': self.kernel_name}
        if _parameters_cell(nb) is None:
            raise ValueError('template {} has no parameters cell: tag a code cell "parameters", '
                             'or start it with the line "# parameters"'.format(template))
        parameters = self._parameters()

        target = self.output()
        self._claim_publish(target)
        target.path.parent.mkdir(parents=True, exist_ok=True)
        running = target.path.with_name(target.path.stem + '.running.ipynb')
        failed = target.path.with_name(target.path.stem + '.failed.ipynb')
        for p in (failed, running):
            if p.exists():
                p.unlink()

        self.logger.info("notebook {} -> {}", template, target.html_path)
        self.logger.debug("notebook parameters: {}", sorted(parameters))
        pm_logger = logging.getLogger('papermill')
        drop = _DropOryxflowUnknown(name for name, _ in self.get_params())
        pm_logger.addFilter(drop)
        # the kernel inherits this environment: without it, every run prints the debugger's
        # "frozen modules" note twice
        pydevd = 'PYDEVD_DISABLE_FILE_VALIDATION' not in os.environ
        if pydevd:
            os.environ['PYDEVD_DISABLE_FILE_VALIDATION'] = '1'
        try:
            pm.execute_notebook(str(template) if as_is else nb, str(running), parameters=parameters,
                                cwd=os.getcwd(), kernel_name=self.kernel_name, language='python',
                                progress_bar=False, log_output=False,
                                request_save_on_cell_execute=False)
        except pm.PapermillExecutionError as e:
            if running.exists():
                running.replace(failed)
            err = NotebookCellError(e, template, failed)
            raise err from _KernelTraceback('\n'.join(err.traceback))
        except Exception:
            if running.exists():
                running.replace(failed)
            raise
        finally:
            pm_logger.removeFilter(drop)
            if pydevd:
                os.environ.pop('PYDEVD_DISABLE_FILE_VALIDATION', None)
        self.save(running, html_template=self.html_template, include_code=self.html_include_code)
