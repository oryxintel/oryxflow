"""Export / import follow-ups: deterministic file, literal `built`, bind(), external
tasks are never code-checked here, export problems reach RunResult, Workflow(export=False)."""
import datetime
import os
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
    (tmp_path / 'pyproject.toml').write_text('')   # both projects' files are hashable
    monkeypatch.setattr(oryxflow.settings, 'dirpath', p1 / 'data')
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    oryxflow.state.clear_cache()
    oryxflow.core._code_warned.clear()
    return p1, p2


def _producer(p1, scale=1):
    class Returns(oryxflow.tasks.TaskPqPandas):
        """Returns by sector."""
        export = True
        path = str(p1 / 'data')
        sector = oryxflow.Parameter(default='office')
        refresh = oryxflow.DateParameter(default=D10)
        debug = oryxflow.BoolParameter(default=False)

        def run(self):
            self.save(pd.DataFrame({'sector': [self.sector], 'refresh': [str(self.refresh)],
                                    'v': [scale]}))
    return Returns


def _set_mtime(task, ns):
    for o in oryxflow.core.flatten(task.output()):
        os.utime(o.path, ns=(ns, ns))


class TestExportFile:

    def test_parameters_in_name_order_whatever_the_import_order(self, projects):
        p1, _ = projects

        class Late(oryxflow.tasks.TaskPqPandas):    # declared first: lower creation counter
            path = str(p1 / 'data')
            zeta = oryxflow.Parameter(default='z')

        class Early(oryxflow.tasks.TaskPqPandas):
            path = str(p1 / 'data')
            alpha = oryxflow.Parameter(default='a')

        @oryxflow.requires(Early, Late)
        class Combined(oryxflow.tasks.TaskPqPandas):
            export = True
            path = str(p1 / 'data')

        code = oexport.FlowExport(tasks=[Combined()]).generate()
        assert code.index('alpha = ') < code.index('zeta = ')

    def test_built_holds_literals(self, projects):
        p1, _ = projects
        Returns = _producer(p1)
        oryxflow.Workflow(Returns, {'refresh': D10}).run()
        code = (p1 / 'tasks_export.py').read_text()
        assert ("built = [{'debug': False, 'refresh': datetime.date(2026, 10, 18), "
                "'sector': 'office', 'prod': True}]") in code
        built = oryxflow.FlowImport(p1).Returns.built
        assert built[0]['debug'] is False

    def test_old_string_entries_merge_with_literal_ones(self, projects):
        p1, _ = projects
        Returns = _producer(p1)
        oryxflow.Workflow(Returns, {'refresh': D10}).run()
        path = p1 / 'tasks_export.py'
        path.write_text(path.read_text().replace(
            "{'debug': False, 'refresh': datetime.date(2026, 10, 18), 'sector': 'office', 'prod': True}",
            "{'debug': 'False', 'refresh': '2026-10-18', 'sector': 'office'}"))   # 26.10.22 form
        oryxflow.Workflow(Returns, {'refresh': D10, 'sector': 'retail'}).run()
        built = oryxflow.FlowImport(p1).Returns.built
        assert [b['sector'] for b in built] == ['office', 'retail']
        assert all(b['debug'] is False for b in built)

    def test_module_not_imported_by_this_run_is_named(self, projects):
        p1, _ = projects
        (p1 / 'tasks_other.py').write_text(
            'import oryxflow\n\nclass Other(oryxflow.tasks.TaskPqPandas):\n    export = True\n')
        export = oexport.FlowExport(tasks=[], path_export=p1 / 'tasks_export.py')
        export.generate()
        assert export.unloaded == {'tasks_other.py': ['Other']}

    def test_export_failure_is_in_run_result(self, projects, monkeypatch):
        p1, _ = projects
        Returns = _producer(p1)

        def boom(*a, **k):
            raise OSError('locked')
        monkeypatch.setattr(oexport, '_write_text', boom)
        with pytest.warns(UserWarning, match='tasks_export.py'):
            r = oryxflow.Workflow(Returns, {'refresh': D10}).run()
        assert r.success
        assert any('could not update tasks_export.py' in w for w in r.warnings)

    def test_workflow_export_false(self, projects):
        p1, _ = projects
        Returns = _producer(p1)
        oryxflow.Workflow(Returns, {'refresh': D10}, export=False).run()
        assert not (p1 / 'tasks_export.py').exists()
        oryxflow.WorkflowMulti(Returns, {'refresh': [D10]}, export=False).run()
        assert not (p1 / 'tasks_export.py').exists()


class TestBind:

    def _built(self, p1, scale=1):
        Returns = _producer(p1, scale)
        oryxflow.Workflow(Returns, {'refresh': D7}, export=False).run()
        oryxflow.Workflow(Returns).run()
        return Returns

    def test_bound_parameters_stay_off_the_consumer(self, projects):
        p1, p2 = projects
        self._built(p1)
        exported = oryxflow.FlowImport(p1)

        @oryxflow.requires(exported.Returns.bind(refresh='2026-07-18'))
        class Uses(oryxflow.tasks.TaskPqPandas):
            path = str(p2 / 'data')

            def run(self):
                self.save(self.inputLoad())

        assert Uses.get_param_names() == []
        flow = oryxflow.Workflow(Uses)
        flow.run()
        assert flow.outputLoad().refresh.tolist() == ['2026-07-18']
        assert Uses().requires().task_id == exported.Returns(refresh=D7).task_id
        assert 'refresh=2026-07-18' in flow.preview(print_it=False)

    def test_bound_value_wins_over_a_same_named_consumer_parameter(self, projects):
        p1, p2 = projects
        self._built(p1)
        exported = oryxflow.FlowImport(p1)

        @oryxflow.requires(exported.Returns.bind(refresh='2026-07-18'))
        class Uses(oryxflow.tasks.TaskPqPandas):
            path = str(p2 / 'data')
            refresh = oryxflow.Parameter(default='my own meaning')

        assert str(Uses().requires().refresh) == '2026-07-18'

    def test_bind_unknown_parameter(self, projects):
        p1, _ = projects
        self._built(p1)
        with pytest.raises(TypeError, match='no parameter nope'):
            oryxflow.FlowImport(p1).Returns.bind(nope=1)

    def test_follows_a_producer_without_code_records(self, projects):
        p1, p2 = projects
        Returns = self._built(p1)
        os.remove(p1 / 'data' / oryxflow.settings.state_filename)
        oryxflow.state.clear_cache()
        exported = oryxflow.FlowImport(p1)

        @oryxflow.requires(exported.Returns)
        class Uses(oryxflow.tasks.TaskPqPandas):
            path = str(p2 / 'data')

            def run(self):
                self.save(self.inputLoad())
        oryxflow.Workflow(Uses).run()
        assert oryxflow.Workflow(Uses).run().ran == []
        # the producer rewrites the same build: no record there, the file time says so
        _set_mtime(Returns(), 3_000_000_000_000_000_000)
        assert oryxflow.Workflow(Uses).run().did_run(Uses)


class TestImportedTasksAreNotCodeChecked:

    def test_no_staleness_warning_and_no_record_written_for_the_producer(self, projects):
        p1, p2 = projects
        Returns = _producer(p1)
        oryxflow.Workflow(Returns, {'refresh': D10}).run()
        status = p1 / 'data' / oryxflow.settings.state_filename
        before = status.read_text()
        exported = oryxflow.FlowImport(p1)

        @oryxflow.requires(exported.Returns)
        class Uses(oryxflow.tasks.TaskPqPandas):
            path = str(p2 / 'data')

            def run(self):
                self.save(self.inputLoad())

        flow = oryxflow.Workflow(Uses)
        with warnings.catch_warnings():
            warnings.simplefilter('error', oryxflow.StalenessWarning)
            flow.run()
            r = flow.run()
        assert r.ran == [] and r.warnings == []
        oryxflow.accept_code(Uses())
        assert status.read_text() == before


def test_output_load_takes_an_instance(projects):
    p1, _ = projects
    Returns = _producer(p1)
    flow = oryxflow.Workflow(Returns, {'refresh': D10})
    flow.run()
    oryxflow.Workflow(Returns, {'refresh': D10, 'sector': 'retail'}).run()
    df = flow.outputLoad(Returns(refresh=D10, sector='retail'))
    assert df.sector.tolist() == ['retail']


def test_rerun_reason_names_the_rebound_import(projects):
    # a consumer pinned to another build of an imported task says which one, not just
    # "upstream output changed"
    p1, p2 = projects
    Returns = _producer(p1)
    oryxflow.Workflow(Returns, {'refresh': D7}).run()
    oryxflow.Workflow(Returns, {'refresh': D10}).run()

    def consumer(value):
        exported = oryxflow.FlowImport(p1)

        @oryxflow.requires(exported.Returns.bind(refresh=value))
        class Uses(oryxflow.tasks.TaskPqPandas):
            path = str(p2 / 'data')

            def run(self):
                self.save(self.inputLoad())
        return Uses

    oryxflow.Workflow(consumer('2026-07-18')).run()
    Uses = consumer('2026-10-18')
    r = oryxflow.Workflow(Uses).run()
    reason = r.reasons[Uses().task_id]
    assert reason.startswith('upstream output changed since last run: now Returns_')
    assert Returns(refresh=D10).task_id in reason and Returns(refresh=D7).task_id in reason
