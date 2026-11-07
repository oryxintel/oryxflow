"""`built` lists only builds the current class can bind (entries for a removed, renamed or added
parameter are dropped, with a warning in RunResult.warnings), and FlowImport leaves a parameter
without a default (an export file from before defaults were required) unbound, with a warning."""
import datetime
import re
import warnings

import pandas as pd
import pytest

import oryxflow
from oryxflow import export as oexport

D7, D10 = datetime.date(2026, 7, 18), datetime.date(2026, 10, 18)


@pytest.fixture
def projects(tmp_path, monkeypatch):
    p1, p2 = tmp_path / 'project1', tmp_path / 'project2'
    p1.mkdir(), p2.mkdir()
    (tmp_path / 'pyproject.toml').write_text('')
    monkeypatch.setattr(oryxflow.settings, 'dirpath', p1 / 'data')
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    oryxflow.state.clear_cache()
    oryxflow.core._code_warned.clear()
    oexport._warned.clear()
    return p1, p2


def _save(task):
    task.save(pd.DataFrame({k: [str(v)] for k, v in task.param_kwargs.items()}))


def _v1(p1):
    class ReturnsMarket(oryxflow.tasks.TaskPqPandas):
        export = True
        path = str(p1 / 'data')
        source_run = oryxflow.Parameter(default='m1')
        refresh = oryxflow.DateParameter(default=D10)

        def run(self):
            _save(self)
    return ReturnsMarket


def _built(p1):
    return oryxflow.FlowImport(p1).ReturnsMarket.built


def _run_v1(p1):
    v1 = _v1(p1)
    oryxflow.Workflow(v1, {'refresh': D7}).run()
    oryxflow.Workflow(v1).run()
    assert len(_built(p1)) == 2


def _check_bindable(p1):
    imported = oryxflow.FlowImport(p1).ReturnsMarket
    for entry in imported.built:
        imported.bind(**entry)      # never raises for a listed entry
    return imported.built


class TestStaleBuiltDropped:

    def test_removed_parameter(self, projects):
        p1, _ = projects
        _run_v1(p1)

        class ReturnsMarket(oryxflow.tasks.TaskPqPandas):
            export = True
            path = str(p1 / 'data')
            refresh = oryxflow.DateParameter(default=D10)

            def run(self):
                _save(self)

        with pytest.warns(UserWarning, match='2 built entries dropped for ReturnsMarket: '
                                             'parameters no longer on the class: source_run'):
            r = oryxflow.Workflow(ReturnsMarket).run()
        assert any('built entries dropped for ReturnsMarket' in w for w in r.warnings)
        assert _check_bindable(p1) == [{'refresh': D10, 'prod': True}]
        # told once: the next run's file no longer holds them
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            r = oryxflow.Workflow(ReturnsMarket).run()
        assert r.warnings == []

    def test_renamed_parameter(self, projects):
        p1, _ = projects
        _run_v1(p1)

        class ReturnsMarket(oryxflow.tasks.TaskPqPandas):
            export = True
            path = str(p1 / 'data')
            source_run = oryxflow.Parameter(default='m1')
            asof = oryxflow.DateParameter(default=D10)

            def run(self):
                _save(self)

        with pytest.warns(UserWarning) as rec:
            r = oryxflow.Workflow(ReturnsMarket).run()
        msg = [str(w.message) for w in rec if 'dropped' in str(w.message)]
        assert len(msg) == 1
        assert 'parameters no longer on the class: refresh' in msg[0]
        assert 'parameters they lack: asof' in msg[0]
        assert any('dropped' in w for w in r.warnings)
        assert _check_bindable(p1) == [{'asof': D10, 'source_run': 'm1', 'prod': True}]

    def test_added_parameter(self, projects):
        # an old build lacks the new parameter: its task id differs, binding it would not
        # find that file
        p1, _ = projects
        _run_v1(p1)

        class ReturnsMarket(oryxflow.tasks.TaskPqPandas):
            export = True
            path = str(p1 / 'data')
            source_run = oryxflow.Parameter(default='m1')
            refresh = oryxflow.DateParameter(default=D10)
            sector = oryxflow.Parameter(default='office')

            def run(self):
                _save(self)

        with pytest.warns(UserWarning, match='parameters they lack: sector'):
            oryxflow.Workflow(ReturnsMarket).run()
        built = _check_bindable(p1)
        assert built == [{'source_run': 'm1', 'refresh': D10, 'sector': 'office', 'prod': True}]

    def test_unchanged_class_keeps_every_entry(self, projects):
        p1, _ = projects
        _run_v1(p1)
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            r = oryxflow.Workflow(_v1(p1)).run()
        assert r.warnings == []
        assert len(_check_bindable(p1)) == 2


def _undefaulted_export(p1):
    class Returns(oryxflow.tasks.TaskPqPandas):
        export = True
        path = str(p1 / 'data')
        period = oryxflow.Parameter(default='2026Q3')
        sector = oryxflow.Parameter(default='office')

        def run(self):
            _save(self)
    oryxflow.Workflow(Returns).run()
    oryxflow.Workflow(Returns, {'period': '2026Q2', 'sector': 'retail'}).run()
    path = p1 / 'tasks_export.py'
    text = path.read_text()
    old = re.sub(r"period = oryxflow\.parameter\.Parameter\(default='2026Q3'\)",
                 'period = oryxflow.parameter.Parameter()', text)   # an older oryxflow's file
    assert old != text
    path.write_text(old)


class TestImportUndefaulted:

    def test_warns_and_leaves_the_parameter_unbound(self, projects):
        p1, p2 = projects
        _undefaulted_export(p1)
        with pytest.warns(UserWarning, match=r'Returns: parameter period has no default.*'
                                             r're-run the producer on oryxflow >= 26\.11\.6'):
            exported = oryxflow.FlowImport(p1)

        @oryxflow.requires(exported.Returns.bind(sector='retail'))
        class Uses(oryxflow.tasks.TaskPqPandas):
            path = str(p2 / 'data')

            def run(self):
                self.save(self.inputLoad())

        assert Uses.get_param_names() == ['period']      # copied through, as before
        flow = oryxflow.Workflow(Uses, {'period': '2026Q2'})
        flow.run()
        assert flow.outputLoad()[['period', 'sector']].values.tolist() == [['2026Q2', 'retail']]

    def test_defaulted_parameters_stay_bound(self, projects):
        p1, _ = projects
        _undefaulted_export(p1)
        with pytest.warns(UserWarning, match='period'):
            exported = oryxflow.FlowImport(p1)
        assert exported.Returns._oryxflow_bound == frozenset({'sector'})

    def test_current_file_imports_silently(self, projects):
        p1, _ = projects

        class Returns(oryxflow.tasks.TaskPqPandas):
            export = True
            path = str(p1 / 'data')
            period = oryxflow.Parameter(default='2026Q3')

            def run(self):
                _save(self)
        oryxflow.Workflow(Returns).run()
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            exported = oryxflow.FlowImport(p1)
        assert exported.Returns._oryxflow_bound == frozenset({'period'})
