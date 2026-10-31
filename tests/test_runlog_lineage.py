"""Lineage in the run log, in OpenLineage vocabulary but flat: task_ran / task_failed carry
project (the job namespace) and inputs; task_ran also outputs. Each entry is one dataset
{namespace, name, datasetVersion}; inputs add the dep's task_id and family. An input's
datasetVersion equals the dep's own task_ran output datasetVersion, across projects too."""
import pandas as pd
import pytest

import oryxflow
import oryxflow.state
from oryxflow import events


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


def _ran():
    events.flush()
    return {e['family']: e for e in events.iter_events(types=('task_ran',))}


class TestLineage:

    def test_datasets_and_version_join(self, projects):
        class Source(oryxflow.tasks.TaskPqPandas):
            code_version = '1'
            def run(self):
                self.save(pd.DataFrame({'v': [1]}))

        @oryxflow.requires(Source)
        class Derived(oryxflow.tasks.TaskPqPandas):
            code_version = '1'
            def run(self):
                self.save(self.inputLoad())

        oryxflow.run(Derived())
        ran = _ran()
        src, der = ran['Source'], ran['Derived']
        assert src['project'] == der['project'] == 'project1'
        (out,) = src['outputs']
        assert out['namespace'] == 'project1'
        assert out['name'] == 'data/Source/{}-data.parquet'.format(Source().task_id)
        assert out['datasetVersion']
        assert der['outputs'][0]['datasetVersion'] not in (None, out['datasetVersion'])
        assert src['inputs'] == []
        (inp,) = der['inputs']
        assert inp == {'namespace': 'project1', 'name': out['name'],
                       'datasetVersion': out['datasetVersion'],   # the join key across events
                       'task_id': Source().task_id, 'family': 'Source'}
        for e in (src, der):                      # one name per thing: no legacy duplicates
            assert 'deps' not in e and 'output_id' not in e and 'paths' not in str(e['inputs'])

    def test_multi_output_dep_gives_one_dataset_per_file(self, projects):
        class Split(oryxflow.tasks.TaskPqPandas):
            code_version = '1'
            persists = ['train', 'test']
            def run(self):
                self.save({'train': pd.DataFrame({'v': [1]}), 'test': pd.DataFrame({'v': [2]})})

        @oryxflow.requires(Split)
        class Fit(oryxflow.tasks.TaskPqPandas):
            code_version = '1'
            def run(self):
                self.save(self.inputLoad(keys='train'))

        oryxflow.run(Fit())
        ran = _ran()
        assert len(ran['Split']['outputs']) == 2
        assert sorted(i['name'] for i in ran['Fit']['inputs']) == \
            sorted(o['name'] for o in ran['Split']['outputs'])
        assert {i['datasetVersion'] for i in ran['Fit']['inputs']} == \
            {ran['Split']['outputs'][0]['datasetVersion']}

    def test_untracked_task_has_no_version(self, projects, monkeypatch):
        monkeypatch.setattr(oryxflow.settings, 'code_version_auto', False)

        class Plain(oryxflow.tasks.TaskPqPandas):
            def run(self):
                self.save(pd.DataFrame({'v': [1]}))

        oryxflow.run(Plain())
        (out,) = _ran()['Plain']['outputs']
        assert out['datasetVersion'] is None and out['name']

    def test_imported_input_points_at_producer(self, projects, monkeypatch):
        p1, p2 = projects

        class Returns(oryxflow.tasks.TaskPqPandas):
            export = True
            code_version = '1'
            path = str(p1 / 'data')
            sector = oryxflow.Parameter(default='office')
            def run(self):
                self.save(pd.DataFrame({'sector': [self.sector]}))

        oryxflow.Workflow(Returns).run()
        (produced,) = _ran()['Returns']['outputs']

        monkeypatch.setattr(oryxflow.settings, 'dirpath', p2 / 'data')
        imported = oryxflow.FlowImport(p1)

        @oryxflow.requires(imported.Returns)
        class Report(oryxflow.tasks.TaskPqPandas):
            code_version = '1'
            def run(self):
                self.save(self.inputLoad())

        oryxflow.run(Report())
        rep = _ran()['Report']
        assert rep['project'] == 'project2'
        assert rep['outputs'][0]['namespace'] == 'project2'
        assert rep['outputs'][0]['name'].startswith('data/Report/')
        (inp,) = rep['inputs']
        assert inp['namespace'] == 'project1'               # the producer, not the consumer
        assert inp['name'] == produced['name']              # relative to the producer's root
        assert inp['datasetVersion'] == produced['datasetVersion']

    def test_failed_task_has_inputs_not_outputs(self, projects):
        class Up(oryxflow.tasks.TaskPqPandas):
            def run(self):
                self.save(pd.DataFrame({'v': [1]}))

        @oryxflow.requires(Up)
        class Boom(oryxflow.tasks.TaskPqPandas):
            def run(self):
                raise ValueError('boom')

        oryxflow.run(Boom(), abort=False)
        events.flush()
        (failed,) = list(events.iter_events(types=('task_failed',)))
        assert failed['project'] == 'project1'
        assert 'outputs' not in failed
        assert [i['family'] for i in failed['inputs']] == ['Up']

    def test_lineage_failure_never_fails_the_build(self, projects, monkeypatch):
        def bad(task, root):
            raise OSError('unreadable')
        monkeypatch.setattr(oryxflow.core, '_output_paths', bad)

        class T(oryxflow.tasks.TaskPqPandas):
            def run(self):
                self.save(pd.DataFrame({'v': [1]}))

        assert oryxflow.run(T()).success
        assert 'outputs' not in _ran()['T']
