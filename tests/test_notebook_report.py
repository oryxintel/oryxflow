import datetime
import enum
import html

import pandas as pd
import pytest

pm = pytest.importorskip('papermill')
pytest.importorskip('nbconvert')
nbformat = pytest.importorskip('nbformat')

import oryxflow
import oryxflow.state
from oryxflow.tasks import TaskCache, TaskPqPandas
from oryxflow.tasks.notebook import TaskNotebook

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
    flow = oryxflow.Workflow(NbReport)
    findings = flow.check_inputs(print_it=False)
    assert not [f for f in findings if f.task_family == 'NbReport' and f.verdict == 'unused']


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
