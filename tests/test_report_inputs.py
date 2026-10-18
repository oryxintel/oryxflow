import json
import sys

import pandas as pd
import pytest

import oryxflow
import oryxflow.report as rpt
from oryxflow.tasks import TaskCache, TaskPqPandas


class RiA(TaskPqPandas):
    def run(self):
        self.save(pd.DataFrame({'a': [1, 2]}))


class RiB(TaskPqPandas):
    persists = ['train', 'test']

    def run(self):
        self.save({'train': pd.DataFrame({'t': [1]}), 'test': pd.DataFrame({'s': [2, 3]})})


class RiC(TaskCache):
    def run(self):
        self.save(pd.DataFrame({'c': [1]}))


class RiNamed(TaskPqPandas):
    def requires(self):
        return {'a': RiA(), 'b': RiB()}

    def run(self):
        self.save(pd.DataFrame({'x': [1]}))


class RiList(TaskPqPandas):
    def requires(self):
        return [RiA(), RiB()]

    def run(self):
        self.save(pd.DataFrame({'x': [1]}))


class RiCacheDep(TaskPqPandas):
    def requires(self):
        return {'c': RiC()}

    def run(self):
        self.save(pd.DataFrame({'x': [1]}))


@pytest.fixture
def datadir(tmp_path):
    old_dir, old_path = oryxflow.settings.dir, oryxflow.settings.dirpath
    oryxflow.set_dir(str(tmp_path / 'data'))
    try:
        yield
    finally:
        oryxflow.settings.dir = old_dir
        oryxflow.settings.dirpath = old_path


@pytest.fixture
def named(datadir):
    t = RiNamed()
    oryxflow.run(t, abort=True)
    return rpt.inputs_of(t)


@pytest.fixture
def listed(datadir):
    t = RiList()
    oryxflow.run(t, abort=True)
    return rpt.inputs_of(t)


def a_frame():
    return pd.DataFrame({'a': [1, 2]})


def test_inputs_of_named(named):
    spec = named
    assert json.loads(json.dumps(spec)) == spec
    assert spec['a']['target'] == 'oryxflow.targets.PqPandasTarget'
    assert set(spec['b']['persists']) == {'train', 'test'}


def test_inputs_of_list_keys_are_strings(listed):
    assert set(listed) == {'0', '1'}


def test_inputLoad_selectors(named, listed):
    pd.testing.assert_frame_equal(rpt.inputLoad(named, task='a'), a_frame())
    pd.testing.assert_frame_equal(rpt.inputLoad(listed, task=0), a_frame())
    assert isinstance(rpt.inputLoad(named, task='b', keys='train'), pd.DataFrame)
    got = rpt.inputLoad(named, task='b', keys=['test', 'train'])
    assert isinstance(got, list) and list(got[0].columns) == ['s'] and list(got[1].columns) == ['t']
    allin = rpt.inputLoad(named)
    assert set(allin) == {'a', 'b'}
    pd.testing.assert_frame_equal(allin['a'], a_frame())
    assert set(allin['b']) == {'train', 'test'}


def test_inputLoad_uses_injected_inputs(named, monkeypatch):
    monkeypatch.setattr(sys.modules['__main__'], 'oryxflow_inputs', named, raising=False)
    pd.testing.assert_frame_equal(rpt.inputLoad(task='a'), a_frame())


def test_inputLoad_none_message(monkeypatch):
    monkeypatch.delattr(sys.modules['__main__'], 'oryxflow_inputs', raising=False)
    with pytest.raises(ValueError, match='inputs_of'):
        rpt.inputLoad()


def test_inputs_of_cache_dep_raises(datadir):
    with pytest.raises(TypeError, match='c') as e:
        rpt.inputs_of(RiCacheDep())
    assert 'TaskPqPandas' in str(e.value)


def test_inputs_of_cloud_raises(datadir, monkeypatch):
    monkeypatch.setattr(oryxflow.settings, 'cloud_fs_enabled', True)
    with pytest.raises(NotImplementedError):
        rpt.inputs_of(RiNamed())
