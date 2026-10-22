import datetime
import enum
import html
import logging

import numpy as np
import pandas as pd
import pytest

pm = pytest.importorskip('papermill')
pytest.importorskip('nbconvert')
nbformat = pytest.importorskip('nbformat')

import oryxflow
import oryxflow.state
from oryxflow.tasks import TaskCache, TaskPqPandas
from oryxflow.tasks.notebook import NotebookCellError, TaskNotebook

DATA_SRC = ("import oryxflow.report as rpt\n"
            "df = rpt.inputLoad(task='up')\n"
            "print('SUM=' + str(df['a'].sum() * x))")


class NbUp(TaskPqPandas):
    def run(self):
        self.save(pd.DataFrame({'a': [1, 2]}))


class NbUpCache(TaskCache):
    def run(self):
        self.save(pd.DataFrame({'a': [1, 2]}))


class NbReport(TaskNotebook):
    notebook = 'template.ipynb'
    x = oryxflow.IntParameter(default=1)

    def requires(self):
        return {'up': NbUp()}


class NbReportCacheDep(TaskNotebook):
    notebook = 'template.ipynb'
    x = oryxflow.IntParameter(default=1)

    def requires(self):
        return {'up': NbUpCache()}


class NbReportBadParam(TaskNotebook):
    notebook = 'template.ipynb'
    oryxflow_x = oryxflow.IntParameter(default=1)

    def requires(self):
        return {'up': NbUp()}


class NbReportBadHook(NbReport):
    def notebook_parameters(self):
        return {'oryxflow_label': 'clash'}


class NbColour(enum.Enum):
    red = 1
    blue = 2


class NbReportTypes(TaskNotebook):
    notebook = 'types.ipynb'
    d = oryxflow.DateParameter(default=datetime.date(2026, 1, 2))
    l = oryxflow.ListParameter(default=[1, 2])
    e = oryxflow.EnumParameter(enum=NbColour, default=NbColour.blue)


class NbReportHookTypes(NbReport):
    notebook = 'hook.ipynb'

    def notebook_parameters(self):
        return {'label': np.int64(7), 'when': pd.Timestamp('2026-01-02')}


@oryxflow.requires({'up': NbUp})
class NbReportDec(TaskNotebook):
    notebook = 'template.ipynb'
    x = oryxflow.IntParameter(default=1)


class NbReportUndeclared(NbReport):
    def notebook_parameters(self):
        return {'zzz': 1}


class NbReportPersists(NbReport):
    persists = ['report']


class NbReportMissing(NbReport):
    notebook = 'nope.ipynb'


def _template(path, params_tag=True, data_src=DATA_SRC, extra_cells=()):
    nb = nbformat.v4.new_notebook()
    p = nbformat.v4.new_code_cell('x = 1\noryxflow_inputs = None')
    if params_tag:
        p.metadata['tags'] = ['parameters']
    nb.cells = [p, nbformat.v4.new_code_cell(data_src),
                nbformat.v4.new_markdown_cell('# Report')] + list(extra_cells)
    nbformat.write(nb, str(path))
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old_dir, old_path = oryxflow.settings.dir, oryxflow.settings.dirpath
    oryxflow.set_dir(str(tmp_path / 'data'))
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    oryxflow.state.clear_cache()
    oryxflow.core._code_warned.clear()
    _template(tmp_path / 'template.ipynb')
    try:
        yield tmp_path
    finally:
        oryxflow.settings.dir = old_dir
        oryxflow.settings.dirpath = old_path
        oryxflow.state.clear_cache()


def _html(task):
    return task.output().html_path.read_text(encoding='utf-8')


def test_runs_and_exports(env):
    flow = oryxflow.Workflow(NbReport)
    flow.run()
    task = flow.get_task()
    target = task.output()
    assert target.path.exists() and target.html_path.exists()
    text = _html(task)
    assert 'SUM=3' in text
    assert 'rpt.inputLoad' not in text
    assert flow.outputLoad() == target.html_path


def test_params_injected(env):
    flow1 = oryxflow.Workflow(NbReport)
    flow2 = oryxflow.Workflow(NbReport, params={'x': 2})
    flow2.run()
    assert 'SUM=6' in _html(flow2.get_task())
    assert flow1.get_task().task_id != flow2.get_task().task_id


def test_cached_second_run(env):
    flow = oryxflow.Workflow(NbReport)
    assert flow.run().did_run(NbReport)
    assert not flow.run().did_run(NbReport)


def test_template_edit_reruns(env):
    flow = oryxflow.Workflow(NbReport)
    flow.run()
    _template(env / 'template.ipynb', extra_cells=[nbformat.v4.new_markdown_cell('More notes')])
    assert flow.run().did_run(NbReport)


def test_output_only_edit_does_not_rerun(env):
    flow = oryxflow.Workflow(NbReport)
    flow.run()
    path = env / 'template.ipynb'
    nb = nbformat.read(str(path), as_version=4)
    for i, cell in enumerate(c for c in nb.cells if c.cell_type == 'code'):
        cell.execution_count = i + 1
        cell.outputs = [nbformat.v4.new_output('stream', name='stdout', text='SUM=3\n')]
    nb.metadata['kernelspec'] = {'name': 'someone-elses-kernel', 'display_name': 'x',
                                 'language': 'python'}
    nbformat.write(nb, str(path))
    assert not flow.run().did_run(NbReport)


def test_upstream_change_reruns(env):
    flow = oryxflow.Workflow(NbReport)
    flow.run()
    flow.reset(NbUp)
    result = flow.run()
    assert result.did_run(NbUp)
    assert result.did_run(NbReport)


def test_failure_is_atomic(env):
    _template(env / 'template.ipynb', extra_cells=[nbformat.v4.new_code_cell("raise ValueError('boom')")])
    task = NbReport()
    with pytest.raises(RuntimeError) as e:
        oryxflow.run(task)
    assert isinstance(e.value.__cause__, pm.PapermillExecutionError)
    assert 'boom' in str(e.value.__cause__)
    target = task.output()
    assert not target.path.exists()
    assert not target.html_path.exists()
    assert target.path.with_name(target.path.stem + '.failed.ipynb').exists()
    assert not target.path.with_name(target.path.stem + '.running.ipynb').exists()


def test_guards(env):
    _template(env / 'template.ipynb', params_tag=False)
    with pytest.raises(RuntimeError) as e:
        oryxflow.run(NbReport())
    assert isinstance(e.value.__cause__, ValueError)
    assert 'parameters' in str(e.value.__cause__)

    _template(env / 'template.ipynb')
    with pytest.raises(RuntimeError) as e:
        oryxflow.run(NbReportCacheDep())
    assert isinstance(e.value.__cause__, TypeError)
    assert "'up'" in str(e.value.__cause__)

    with pytest.raises(RuntimeError) as e:
        oryxflow.run(NbReportBadParam())
    assert isinstance(e.value.__cause__, ValueError)
    assert 'oryxflow_x' in str(e.value.__cause__)

    with pytest.raises(RuntimeError) as e:
        oryxflow.run(NbReportBadHook())
    assert isinstance(e.value.__cause__, ValueError)
    assert 'oryxflow_label' in str(e.value.__cause__)


def test_check_inputs_quiet(env):
    flow = oryxflow.Workflow(NbReportDec)
    findings = flow.check_inputs(print_it=False, include_clean=True)
    assert not [f for f in findings if f.task_family == 'NbReportDec' and f.verdict == 'unused']


def test_notebook_parameters_hook_values_are_json_safe(env):
    nb = nbformat.v4.new_notebook()
    p = nbformat.v4.new_code_cell('label = None\nwhen = None\noryxflow_inputs = None')
    p.metadata['tags'] = ['parameters']
    nb.cells = [p, nbformat.v4.new_code_cell('print("L=" + str(label) + " W=" + str(when))')]
    nbformat.write(nb, str(env / 'hook.ipynb'))
    task = NbReportHookTypes()
    oryxflow.run(task)
    text = html.unescape(_html(task))
    assert 'L=7' in text
    assert '2026-01-02' in text


def test_template_deleted_after_run(env):
    flow = oryxflow.Workflow(NbReport)
    flow.run()
    assert flow.complete()
    (env / 'template.ipynb').rename(env / 'moved.ipynb')
    assert flow.complete() is False
    flow.preview(print_it=False)
    with pytest.raises(RuntimeError) as e:
        oryxflow.run(NbReport())
    assert isinstance(e.value.__cause__, FileNotFoundError)
    assert 'template.ipynb' in str(e.value.__cause__)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_no_unknown_parameter_warning_for_oryxflow_names(env):
    capture = _Capture()
    logger = logging.getLogger('papermill')
    logger.addHandler(capture)
    try:
        oryxflow.run(NbReport())
        unknown = [m for m in capture.messages if 'Passed unknown parameter' in m]
        assert not [m for m in unknown if 'oryxflow_' in m]
        class_filters = list(logger.filters)
    finally:
        logger.removeHandler(capture)
    assert not [f for f in class_filters if type(f).__name__ == '_DropOryxflowUnknown']


def test_unknown_user_parameter_still_warns(env):
    capture = _Capture()
    logger = logging.getLogger('papermill')
    logger.addHandler(capture)
    try:
        oryxflow.run(NbReportUndeclared())
    finally:
        logger.removeHandler(capture)
    unknown = [m for m in capture.messages if 'Passed unknown parameter' in m]
    assert any('zzz' in m for m in unknown)
    assert not [m for m in unknown if 'oryxflow_' in m]


def test_parameter_types_reach_notebook(env):
    nb = nbformat.v4.new_notebook()
    p = nbformat.v4.new_code_cell('d = None\nl = None\ne = None\noryxflow_inputs = None')
    p.metadata['tags'] = ['parameters']
    nb.cells = [p, nbformat.v4.new_code_cell('print(repr(d), repr(l), repr(e))')]
    nbformat.write(nb, str(env / 'types.ipynb'))
    task = NbReportTypes()
    oryxflow.run(task)
    text = html.unescape(_html(task))
    assert "'2026-01-02'" in text
    assert '[1, 2]' in text
    assert "'blue'" in text


def test_persists_rejected(env):
    with pytest.raises(RuntimeError) as e:
        oryxflow.run(NbReportPersists())
    assert isinstance(e.value.__cause__, ValueError)
    assert 'exactly one output' in str(e.value.__cause__)


def test_missing_template(env):
    with pytest.raises(RuntimeError) as e:
        oryxflow.run(NbReportMissing())
    assert isinstance(e.value.__cause__, FileNotFoundError)
    assert 'nope.ipynb' in str(e.value.__cause__)


# -- code identity: modules the template imports -------------------------------------------

IMPORT_SRC = ("import oryxflow.report as rpt\n"
              "%matplotlib inline\n"
              "from viz import helper\n"
              "print('LABEL=' + helper.label())")


class NbReportImports(TaskNotebook):
    notebook = 'imports.ipynb'


class NbReportDepsGlob(TaskNotebook):
    notebook = 'imports.ipynb'
    template_deps = ['notes/*.md']


class NbReportDepsNone(TaskNotebook):
    notebook = 'imports.ipynb'
    template_deps = None


def _viz(env, label='A', common='X = 1'):
    (env / 'viz').mkdir(exist_ok=True)
    (env / 'viz' / '__init__.py').write_text('', encoding='utf-8')
    (env / 'viz' / 'common.py').write_text(common + '\n', encoding='utf-8')
    (env / 'viz' / 'helper.py').write_text(
        '"""Helpers."""\nfrom . import common\n\n\ndef label():\n    return {!r}\n'.format(label),
        encoding='utf-8')


@pytest.fixture
def imports_env(env):
    (env / 'pyproject.toml').write_text('', encoding='utf-8')   # the project root
    _viz(env)
    _template(env / 'imports.ipynb', data_src=IMPORT_SRC)
    return env


def test_imported_module_edit_reruns(imports_env):
    env = imports_env
    flow = oryxflow.Workflow(NbReportImports)
    assert flow.run().did_run(NbReportImports)
    assert 'LABEL=A' in _html(flow.get_task())
    assert not flow.run().did_run(NbReportImports)

    _viz(env, label='B')                                   # edit the imported helper
    assert flow.run().did_run(NbReportImports)
    assert 'LABEL=B' in _html(flow.get_task())

    _viz(env, label='B', common='X = 2')                   # a module the helper imports
    assert flow.run().did_run(NbReportImports)


def test_imported_module_docstring_edit_does_not_rerun(imports_env):
    env = imports_env
    flow = oryxflow.Workflow(NbReportImports)
    flow.run()
    path = env / 'viz' / 'helper.py'
    path.write_text(path.read_text(encoding='utf-8').replace('Helpers.', 'Plot helpers.'),
                    encoding='utf-8')
    assert not flow.run().did_run(NbReportImports)


def test_task_modules_are_not_tracked(imports_env):
    # a template imports the project's task module to build its inputs by hand; a task edit
    # reaches the report through the data, so it must not rerun every report
    env = imports_env
    task_src = 'import oryxflow\n\n\nclass Up(oryxflow.tasks.TaskPqPandas):\n    x = {}\n'
    (env / 'mytasks.py').write_text(task_src.format(1), encoding='utf-8')
    _template(env / 'imports.ipynb', data_src='import mytasks\n' + IMPORT_SRC)
    before = NbReportImports().code_version()
    assert 'imports' in before                             # viz/helper.py is still watched
    (env / 'mytasks.py').write_text(task_src.format(2), encoding='utf-8')
    assert NbReportImports().code_version() == before


def test_installed_imports_are_not_tracked(env):
    # the fixture template imports only oryxflow, which is not part of the project
    assert set(NbReport().code_version()) == {'template', 'python'}


def test_template_deps_globs_and_none(imports_env):
    env = imports_env
    (env / 'notes').mkdir()
    (env / 'notes' / 'a.md').write_text('one', encoding='utf-8')
    flow = oryxflow.Workflow(NbReportDepsGlob)
    flow.run()
    assert 'imports' not in NbReportDepsGlob().code_version()
    (env / 'notes' / 'a.md').write_text('two', encoding='utf-8')
    assert flow.run().did_run(NbReportDepsGlob)
    _viz(env, label='C')                                   # not watched: the list replaces auto
    assert not flow.run().did_run(NbReportDepsGlob)

    assert set(NbReportDepsNone().code_version()) == {'template', 'python'}


# -- published copies and title ---------------------------------------------------------------

class NbReportPublished(NbReport):
    publish = ['out/report-x{x}.html', 'out/report-x{x}.ipynb']


class NbReportTitled(NbReport):
    report_title = 'Sales report, x={x}'


class NbReportPublishFixed(NbReport):
    publish = 'out/same.html'


class NbReportPublishBad(NbReport):
    publish = 'out/report-{region}.html'


def test_publish_copies_and_title(env):
    flow = oryxflow.Workflow(NbReportPublished, params={'x': 2})
    flow.run()
    html_copy = env / 'out' / 'report-x2.html'
    assert html_copy.read_text(encoding='utf-8') == _html(flow.get_task())
    assert '<title>report-x2</title>' in html_copy.read_text(encoding='utf-8')
    assert nbformat.read(str(env / 'out' / 'report-x2.ipynb'), as_version=4).cells
    assert not list((env / 'out').glob('*.tmp'))

    html_copy.unlink()                                     # a deleted deliverable is rebuilt
    assert flow.run().did_run(NbReportPublished)
    assert html_copy.exists()


def test_title(env):
    oryxflow.run(NbReport())
    assert '<title>NbReport</title>' in _html(NbReport())
    oryxflow.run(NbReportTitled())
    assert '<title>Sales report, x=1</title>' in _html(NbReportTitled())


def test_publish_guards(env):
    # one build, two reports, one file: refused
    with pytest.raises(RuntimeError) as e:
        oryxflow.run([NbReportPublishFixed(x=1), NbReportPublishFixed(x=2)])
    assert 'same.html' in str(e.value.__cause__) and 'publish pattern' in str(e.value.__cause__)
    # a later build replacing it (new parameters, say a new quarter) is fine
    oryxflow.run(NbReportPublishFixed(x=3))
    assert (env / 'out' / 'same.html').exists()

    with pytest.raises(ValueError) as e:
        NbReportPublishBad().output()
    assert 'region' in str(e.value) and 'x' in str(e.value)


# -- failures and template authoring ----------------------------------------------------------

def test_failure_message_is_clean(env):
    _template(env / 'template.ipynb', extra_cells=[
        nbformat.v4.new_code_cell("y = 1\nraise KeyError('pulled_at')")])
    task = NbReport()
    with pytest.raises(RuntimeError) as e:
        oryxflow.run(task)
    err = e.value.__cause__
    assert isinstance(err, NotebookCellError)
    msg = str(err)
    assert '\x1b' not in msg and '\x1b' not in str(e.value)
    assert "raise KeyError('pulled_at')" in msg
    assert "KeyError: 'pulled_at'" in msg
    assert 'failed.ipynb' in msg and err.failed_path.exists()
    assert len(msg.splitlines()) == 2
    assert '\x1b' not in str(err.__cause__) and 'pulled_at' in str(err.__cause__)


def test_parameters_comment_instead_of_tag(env):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell('# parameters\nx = 1\noryxflow_inputs = None'),
                nbformat.v4.new_code_cell(DATA_SRC)]
    nbformat.write(nb, str(env / 'template.ipynb'))
    flow = oryxflow.Workflow(NbReport, params={'x': 2})
    flow.run()
    assert 'SUM=6' in _html(flow.get_task())
    saved = nbformat.read(str(env / 'template.ipynb'), as_version=4)
    assert 'tags' not in saved.cells[0].metadata               # the file itself is left alone


def test_template_without_kernelspec_runs(env):
    assert 'kernelspec' not in nbformat.read(str(env / 'template.ipynb'), as_version=4).metadata
    oryxflow.run(NbReport())
    assert 'SUM=3' in _html(NbReport())


class NbReportInherited(NbReport):
    region_code = oryxflow.Parameter(default='r1')     # inherited, not declared by the template


def test_no_unknown_parameter_warning_for_task_parameters(env, monkeypatch):
    monkeypatch.delenv('PYDEVD_DISABLE_FILE_VALIDATION', raising=False)
    capture = _Capture()
    logger = logging.getLogger('papermill')
    logger.addHandler(capture)
    try:
        oryxflow.run(NbReportInherited())
    finally:
        logger.removeHandler(capture)
    assert not [m for m in capture.messages if 'Passed unknown parameter' in m]
    import os
    assert 'PYDEVD_DISABLE_FILE_VALIDATION' not in os.environ     # set for the kernel only
