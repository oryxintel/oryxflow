"""Exported defaults are prod: FlowImport binds every imported parameter to its exported default,
generation requires defaults, `built` marks the prod entry, and an experiment written to the
prod data folder warns."""
import datetime
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


def _producer(p1, prod_refresh=D10):
    class Returns(oryxflow.tasks.TaskPqPandas):
        """Returns by sector."""
        export = True
        path = str(p1 / 'data')
        sector = oryxflow.Parameter(default='office')
        refresh = oryxflow.DateParameter(default=prod_refresh)

        def run(self):
            self.save(pd.DataFrame({'sector': [self.sector], 'refresh': [str(self.refresh)]}))
    return Returns


def _consumer(p1, p2, dep=None):
    exported = oryxflow.FlowImport(p1)

    @oryxflow.requires(dep(exported) if dep else exported.Returns)
    class Uses(oryxflow.tasks.TaskPqPandas):
        path = str(p2 / 'data')

        def run(self):
            self.save(self.inputLoad())
    return Uses


def _ran(r):
    return [t.task_family for t in r.ran]


class TestImportBindsDefaults:

    def test_bare_requires_copies_no_parameters(self, projects):
        p1, p2 = projects
        oryxflow.Workflow(_producer(p1)).run()
        Uses = _consumer(p1, p2)
        assert Uses.get_param_names() == []
        flow = oryxflow.Workflow(Uses)
        flow.run()
        assert flow.outputLoad()[['sector', 'refresh']].values.tolist() == [['office', '2026-10-18']]

    def test_bind_overrides_and_the_rest_keep_defaults(self, projects):
        p1, p2 = projects
        Returns = _producer(p1)
        oryxflow.Workflow(Returns, {'sector': 'retail'}, env=None).run()
        Uses = _consumer(p1, p2, lambda e: e.Returns.bind(sector='retail'))
        assert Uses.get_param_names() == []
        flow = oryxflow.Workflow(Uses)
        flow.run()
        assert flow.outputLoad()[['sector', 'refresh']].values.tolist() == [['retail', '2026-10-18']]

    def test_consumer_parameter_of_the_same_name_is_not_passed(self, projects):
        p1, p2 = projects
        oryxflow.Workflow(_producer(p1)).run()
        exported = oryxflow.FlowImport(p1)

        @oryxflow.requires(exported.Returns)
        class Uses(oryxflow.tasks.TaskPqPandas):
            path = str(p2 / 'data')
            sector = oryxflow.Parameter(default='hotel')

        assert Uses().requires().sector == 'office'

    def test_per_instance_value_in_requires(self, projects):
        p1, p2 = projects
        Returns = _producer(p1)
        for s in ['office', 'retail']:
            oryxflow.Workflow(Returns, {'sector': s}).run()
        exported = oryxflow.FlowImport(p1)

        class Uses(oryxflow.tasks.TaskPqPandas):
            path = str(p2 / 'data')
            sector = oryxflow.Parameter(default='office')

            def requires(self):
                return exported.Returns(sector=self.sector)

            def run(self):
                self.save(self.inputLoad())

        flow = oryxflow.Workflow(Uses, {'sector': 'retail'})
        flow.run()
        assert flow.outputLoad().sector.tolist() == ['retail']
        assert str(Uses(sector='retail').requires().refresh) == '2026-10-18'

    def test_fan_out_over_an_imported_parameter(self, projects):
        p1, p2 = projects
        Returns = _producer(p1)
        for s in ['office', 'retail']:
            oryxflow.Workflow(Returns, {'sector': s}).run()
        exported = oryxflow.FlowImport(p1)

        @oryxflow.requires_each(exported.Returns, sector=['office', 'retail'])
        class Both(oryxflow.tasks.TaskPqPandas):
            path = str(p2 / 'data')

            def run(self):
                self.save(self.inputLoadConcat(tag=False))

        flow = oryxflow.Workflow(Both)
        flow.run()
        assert sorted(flow.outputLoad().sector) == ['office', 'retail']

    def test_producer_changes_a_default(self, projects):
        p1, p2 = projects
        oryxflow.Workflow(_producer(p1, prod_refresh=D7)).run()
        Uses = _consumer(p1, p2)
        tid = Uses().task_id
        assert _ran(oryxflow.Workflow(Uses).run()) == ['Uses']
        assert _ran(oryxflow.Workflow(Uses).run()) == []

        oryxflow.Workflow(_producer(p1, prod_refresh=D10)).run()   # next quarter: a new default
        Uses = _consumer(p1, p2)
        assert Uses().task_id == tid
        flow = oryxflow.Workflow(Uses)
        assert _ran(flow.run()) == ['Uses']
        assert flow.outputLoad().refresh.tolist() == ['2026-10-18']

    def test_latest_is_gone(self, projects):
        p1, _ = projects
        oryxflow.Workflow(_producer(p1)).run()
        exported = oryxflow.FlowImport(p1)
        assert not hasattr(exported.Returns, 'latest')
        assert not hasattr(oexport, 'latest')


class TestDefaultsRequired:

    def _no_default(self, p1):
        class NoDefault(oryxflow.tasks.TaskPqPandas):
            export = True
            path = str(p1 / 'data')
            period = oryxflow.Parameter()

            def run(self):
                self.save(pd.DataFrame({'p': [self.period]}))
        return NoDefault

    def test_generation_error(self, projects):
        p1, _ = projects
        NoDefault = self._no_default(p1)
        with pytest.raises(ValueError, match=r'NoDefault\.period: exported parameters need a '
                                             r'default -- the value consumers should get \(prod\)'):
            oryxflow.FlowExport(tasks=[NoDefault(period='a')]).generate()

    def test_run_fails_before_building(self, projects):
        p1, _ = projects
        NoDefault = self._no_default(p1)
        with pytest.raises(ValueError, match='exported parameters need a default'):
            oryxflow.Workflow(NoDefault, {'period': 'a'}).run()
        assert not NoDefault(period='a').complete()
        oryxflow.Workflow(NoDefault, {'period': 'a'}).run(export=False)   # nothing published

    def test_prod_is_a_reserved_name(self, projects):
        p1, _ = projects

        class HasProd(oryxflow.tasks.TaskPqPandas):
            export = True
            path = str(p1 / 'data')
            prod = oryxflow.BoolParameter(default=True)

        with pytest.raises(ValueError, match="'prod'"):
            oryxflow.FlowExport(tasks=[HasProd()]).generate()


class TestBuiltMarksProd:

    def test_prod_entry(self, projects):
        p1, _ = projects
        Returns = _producer(p1)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            oryxflow.Workflow(Returns, {'sector': 'retail'}).run()
        oryxflow.Workflow(Returns).run()
        code = (p1 / 'tasks_export.py').read_text()
        assert ("{'refresh': datetime.date(2026, 10, 18), 'sector': 'office', 'prod': True},"
                in code)
        assert "{'refresh': datetime.date(2026, 10, 18), 'sector': 'retail'}," in code
        imported = oryxflow.FlowImport(p1).Returns
        prod = [b for b in imported.built if b.get('prod')]
        assert len(prod) == 1
        assert imported.bind(**prod[0])().task_id == Returns().task_id
        assert oexport._from_values(imported, prod[0]).task_id == Returns().task_id

    def test_new_default_moves_the_mark(self, projects):
        p1, _ = projects
        oryxflow.Workflow(_producer(p1, prod_refresh=D7)).run()
        oryxflow.Workflow(_producer(p1, prod_refresh=D10)).run()
        built = oryxflow.FlowImport(p1).Returns.built
        assert [(str(b['refresh']), b.get('prod', False)) for b in built] == \
            [('2026-07-18', False), ('2026-10-18', True)]


class TestExperimentGuard:

    def test_non_default_run_in_the_prod_folder_warns(self, projects):
        p1, _ = projects
        Returns = _producer(p1)
        with pytest.warns(UserWarning, match=r"Returns: experiment written to the prod data folder "
                                             r"\(non-default sector=retail\); run experiments with "
                                             r"Workflow\(\.\.\., env='dev'\)"):
            oryxflow.Workflow(Returns, {'sector': 'retail'}).run()

    def test_default_run_and_dev_env_are_quiet(self, projects):
        p1, _ = projects
        Returns = _producer(p1)
        oryxflow.Workflow(Returns).run()
        with warnings.catch_warnings():
            warnings.simplefilter('error', UserWarning)
            oryxflow.Workflow(Returns).run()
            oryxflow.Workflow(Returns, {'sector': 'retail'}, env='dev').run()
        built = oryxflow.FlowImport(p1).Returns.built
        assert [b['sector'] for b in built] == ['office']

    def test_an_aggregator_building_every_value_is_quiet(self, projects):
        # one prod build per exported task: the aggregator holds every sector
        p1, _ = projects
        Returns = _producer(p1)

        @oryxflow.requires_each(Returns, sector=['office', 'retail'])
        class AllSectors(oryxflow.tasks.TaskPqPandas):
            export = True
            path = str(p1 / 'data')

            def run(self):
                self.save(self.inputLoadConcat(tag=False))

        with warnings.catch_warnings():
            warnings.simplefilter('error', UserWarning)
            oryxflow.Workflow(AllSectors).run()
        exported = oryxflow.FlowImport(p1)
        assert sorted(b['sector'] for b in exported.Returns.built) == ['office', 'retail']
