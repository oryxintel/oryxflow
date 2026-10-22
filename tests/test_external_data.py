"""Reading another project's outputs: `export = True` tasks, tasks_export.py, FlowImport."""
import datetime
import enum
import os
from pathlib import Path

import pandas as pd
import pytest

import oryxflow
from oryxflow import export as oexport


@pytest.fixture
def projects(tmp_path, monkeypatch):
    """project1 (producer) and project2 (consumer) side by side; cwd-style data dir = project1/data."""
    p1, p2 = tmp_path / 'project1', tmp_path / 'project2'
    p1.mkdir(), p2.mkdir()
    monkeypatch.setattr(oryxflow.settings, 'dirpath', p1 / 'data')
    return p1, p2


def _producer(p1, scale=1):
    class SectorReturns(oryxflow.tasks.TaskPqPandas):
        """Quarterly total returns by sector. Columns: sector, ret_pct."""
        export = True
        path = str(p1 / 'data')
        sector = oryxflow.Parameter(default='office')

        def run(self):
            self.save(pd.DataFrame({'sector': [self.sector], 'ret_pct': [0.01 * scale]}))

    class Internal(oryxflow.tasks.TaskPqPandas):
        path = str(p1 / 'data')

        def run(self):
            self.save(pd.DataFrame({'x': [1]}))
    return SectorReturns, Internal


def _export_file(p1):
    return p1 / 'tasks_export.py'


class TestProducer:

    def test_export_defaults_off(self):
        assert oryxflow.tasks.TaskData.export is False

    def test_run_writes_export_file(self, projects):
        p1, _ = projects
        SectorReturns, _ = _producer(p1)
        oryxflow.Workflow(SectorReturns, {'sector': 'retail'}).run()
        code = _export_file(p1).read_text()
        compile(code, 'tasks_export.py', 'exec')
        assert 'class SectorReturns(oryxflow.tasks.TaskPqPandas):' in code
        assert 'Quarterly total returns by sector' in code
        assert 'external = True' in code
        assert "sector = oryxflow.parameter.Parameter(default='office')" in code
        assert "built = [{'sector': 'retail'}]" in code
        assert 'Internal' not in code

    def test_built_values_accumulate_and_prune(self, projects):
        p1, _ = projects
        SectorReturns, _ = _producer(p1)
        oryxflow.Workflow(SectorReturns, {'sector': 'office'}).run()
        oryxflow.Workflow(SectorReturns, {'sector': 'retail'}).run()
        assert ("    built = [\n        {'sector': 'office'},\n        {'sector': 'retail'},\n    ]"
                in _export_file(p1).read_text())

        os.remove(SectorReturns(sector='retail').output().path)
        oryxflow.Workflow(SectorReturns, {'sector': 'office'}).run()
        assert "built = [{'sector': 'office'}]" in _export_file(p1).read_text()

    def test_flow_without_exported_tasks_writes_nothing(self, projects):
        p1, _ = projects
        _, Internal = _producer(p1)
        oryxflow.Workflow(Internal).run()
        assert not _export_file(p1).exists()

    def test_run_export_false(self, projects):
        p1, _ = projects
        SectorReturns, _ = _producer(p1)
        oryxflow.Workflow(SectorReturns).run(export=False)
        assert not _export_file(p1).exists()

    def test_unchanged_export_is_not_rewritten(self, projects, monkeypatch):
        p1, _ = projects
        SectorReturns, _ = _producer(p1)
        oryxflow.Workflow(SectorReturns).run()
        writes = []
        monkeypatch.setattr(oexport, '_write_text', lambda path, text: writes.append(path))
        oryxflow.Workflow(SectorReturns).run()
        assert writes == []

    def test_export_failure_does_not_fail_the_run(self, projects, monkeypatch):
        p1, _ = projects
        SectorReturns, _ = _producer(p1)

        def boom(*a, **k):
            raise OSError('locked')
        monkeypatch.setattr(oexport, '_write_text', boom)
        with pytest.warns(UserWarning, match='tasks_export.py'):
            r = oryxflow.Workflow(SectorReturns).run()
        assert r.success

    def test_duplicate_class_names_raise(self, projects):
        p1, _ = projects
        A, _ = _producer(p1)

        class SectorReturns(oryxflow.tasks.TaskPqPandas):   # a different task, same name
            export = True
            path = str(p1 / 'data')
        B = SectorReturns
        with pytest.raises(ValueError, match='SectorReturns'):
            oryxflow.FlowExport(tasks=[A(), B()]).generate()


class Color(enum.Enum):
    red = 1
    blue = 2


class TestParameterTypes:

    def test_imported_task_ids_match_the_producer(self, projects):
        p1, _ = projects

        class Typed(oryxflow.tasks.TaskPqPandas):
            export = True
            path = str(p1 / 'data')
            year = oryxflow.IntParameter(default=2026)
            rate = oryxflow.FloatParameter(default=0.5)
            asof = oryxflow.DateParameter(default=datetime.date(2026, 6, 30))
            items = oryxflow.ListParameter(default=['a', 'b'])
            opts = oryxflow.DictParameter(default={'k': 1})
            flag = oryxflow.BoolParameter(default=True)
            model = oryxflow.ChoiceParameter(choices=['rf', 'lgbm'], default='rf')
            color = oryxflow.EnumParameter(enum=Color, default=Color.red)
            note = oryxflow.Parameter(default='x', significant=False)

            def run(self):
                self.save(pd.DataFrame({'v': [1]}))

        oryxflow.Workflow(Typed).run()
        imported = oryxflow.FlowImport(p1).Typed
        assert imported().task_id == Typed().task_id
        assert imported(year=2025, color='blue').task_id == Typed(year=2025, color=Color.blue).task_id
        assert imported().output().path == Typed().output().path


class TestConsumer:

    def _consumer(self, p1, p2):
        exported = oryxflow.FlowImport(p1)

        @oryxflow.requires(exported.SectorReturns)
        class UsesReturns(oryxflow.tasks.TaskPqPandas):
            path = str(p2 / 'data')

            def run(self):
                df = self.inputLoad()
                self.save(df.assign(ret_bp=df.ret_pct * 10000))
        return UsesReturns

    def test_import_by_project_dir_or_file(self, projects):
        p1, _ = projects
        SectorReturns, _ = _producer(p1)
        oryxflow.Workflow(SectorReturns).run()
        assert oryxflow.FlowImport(p1).SectorReturns.external is True
        assert oryxflow.FlowImport(_export_file(p1)).SectorReturns.external is True
        assert set(oryxflow.FlowImport(p1).tasks) == {'SectorReturns'}

    def test_params_pass_through_the_flow(self, projects):
        p1, p2 = projects
        SectorReturns, _ = _producer(p1)
        oryxflow.Workflow(SectorReturns, {'sector': 'office'}).run()
        oryxflow.Workflow(SectorReturns, {'sector': 'retail'}).run()

        UsesReturns = self._consumer(p1, p2)
        for sector in ['office', 'retail']:
            flow = oryxflow.Workflow(UsesReturns, {'sector': sector})
            flow.run()
            assert flow.outputLoad().sector.tolist() == [sector]

    def test_follows_upstream_rerun(self, projects):
        p1, p2 = projects
        SectorReturns, _ = _producer(p1)
        oryxflow.Workflow(SectorReturns).run()
        UsesReturns = self._consumer(p1, p2)
        assert [t.task_family for t in oryxflow.Workflow(UsesReturns).run().ran] == ['UsesReturns']
        assert oryxflow.Workflow(UsesReturns).run().ran == []

        SectorReturns2, _ = _producer(p1, scale=5)          # project1 recomputes
        flow1 = oryxflow.Workflow(SectorReturns2)
        flow1.reset(SectorReturns2)
        flow1.run()
        flow2 = oryxflow.Workflow(UsesReturns)
        assert [t.task_family for t in flow2.run().ran] == ['UsesReturns']
        assert flow2.outputLoad().ret_bp.round(6).tolist() == [500.0]

    def test_missing_output_names_path_and_built_values(self, projects):
        p1, p2 = projects
        SectorReturns, _ = _producer(p1)
        oryxflow.Workflow(SectorReturns, {'sector': 'office'}).run()
        UsesReturns = self._consumer(p1, p2)
        with pytest.raises(RuntimeError) as e:
            oryxflow.Workflow(UsesReturns, {'sector': 'hotel'}).run()
        msg = str(e.value)
        assert 'external missing output' in msg
        assert 'SectorReturns' in msg and 'hotel' in msg
        assert "built: sector=office" in msg
        assert 'produced outside this flow' in msg

    def test_two_projects_with_same_file_name_do_not_collide(self, tmp_path, monkeypatch):
        mods = []
        for name in ['a', 'b']:
            p = tmp_path / name
            p.mkdir()
            monkeypatch.setattr(oryxflow.settings, 'dirpath', p / 'data')
            SectorReturns, _ = _producer(p)
            oryxflow.Workflow(SectorReturns).run()
            mods.append(oryxflow.FlowImport(p))
        assert mods[0].SectorReturns is not mods[1].SectorReturns
        assert mods[0].SectorReturns().output().path != mods[1].SectorReturns().output().path


class TestFlowImportErrors:

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError, match='tasks_export.py'):
            oryxflow.FlowImport(tmp_path)

    def test_load_error_raises(self, tmp_path):
        (tmp_path / 'tasks_export.py').write_text('# Generated by oryxflow\nimport module_that_does_not_exist_xyz\n')
        with pytest.raises(ImportError):
            oryxflow.FlowImport(tmp_path)

    def test_unknown_task_lists_available(self, projects):
        p1, _ = projects
        SectorReturns, _ = _producer(p1)
        oryxflow.Workflow(SectorReturns).run()
        with pytest.raises(AttributeError, match='SectorReturns'):
            oryxflow.FlowImport(p1).Missing

    def test_hand_written_module_warns(self, tmp_path):
        (tmp_path / 'tasks_export.py').write_text('import oryxflow\n')
        with pytest.warns(UserWarning, match='not generated by oryxflow'):
            oryxflow.FlowImport(tmp_path)
