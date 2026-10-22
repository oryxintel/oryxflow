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


class RiXl(oryxflow.tasks.TaskExcelPandas):
    persists = ['s1', 's2']

    def run(self):
        self.save({'s1': pd.DataFrame({'p': [1]}), 's2': pd.DataFrame({'q': [2, 3]})})


class RiXlUser(TaskPqPandas):
    def requires(self):
        return {'xl': RiXl()}

    def run(self):
        self.save(pd.DataFrame({'x': [1]}))


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
    assert set(allin['b'].keys()) == {'train', 'test'}


def test_inputLoad_uses_injected_inputs(named, monkeypatch):
    monkeypatch.setattr(sys.modules['__main__'], 'oryxflow_inputs', named, raising=False)
    pd.testing.assert_frame_equal(rpt.inputLoad(task='a'), a_frame())


def test_inputLoad_none_message(monkeypatch):
    monkeypatch.delattr(sys.modules['__main__'], 'oryxflow_inputs', raising=False)
    with pytest.raises(ValueError, match='default_inputs'):
        rpt.inputLoad()


def test_inputs_of_cache_dep_raises(datadir):
    with pytest.raises(TypeError, match="'c'") as e:
        rpt.inputs_of(RiCacheDep())
    assert 'TaskPqPandas' in str(e.value)


def test_inputs_of_cloud_raises(datadir, monkeypatch):
    monkeypatch.setattr(oryxflow.settings, 'cloud_fs_enabled', True)
    with pytest.raises(NotImplementedError):
        rpt.inputs_of(RiNamed())


def test_inputs_of_paths_are_absolute(tmp_path, monkeypatch):
    import os
    old_dir, old_path = oryxflow.settings.dir, oryxflow.settings.dirpath
    monkeypatch.chdir(tmp_path)
    oryxflow.set_dir('reldata')
    try:
        spec = rpt.inputs_of(RiNamed())
        assert os.path.isabs(spec['a']['path'])
        assert all(os.path.isabs(v['path']) for v in spec['b']['persists'].values())
    finally:
        oryxflow.settings.dir = old_dir
        oryxflow.settings.dirpath = old_path


def test_inputLoad_keys_for_excel_sheets(datadir):
    t = RiXlUser()
    oryxflow.run(t, abort=True)
    spec = rpt.inputs_of(t)
    one = rpt.inputLoad(spec, task='xl', keys='s2')
    assert list(one.columns) == ['q']
    many = rpt.inputLoad(spec, task='xl', keys=['s2'])
    assert list(many) == ['s2']
    assert set(rpt.inputLoad(spec, task='xl')) == {'s1', 's2'}


def test_inputLoad_multi_output_unpacks_like_self_inputLoad(named):
    train, test = rpt.inputLoad(named, task='b')           # data, not the output names
    assert list(train.columns) == ['t'] and list(test.columns) == ['s']
    both = rpt.inputLoad(named, task='b')
    assert list(both['test'].columns) == ['s']
    assert [k for k, _ in both.items()] == ['train', 'test']
    with pytest.raises(KeyError, match='train, test'):
        both['valid']
    expect = RiNamed().inputLoad(task='b')
    assert [list(d.columns) for d in expect] == [list(d.columns) for d in both]


def test_default_inputs_by_hand(datadir, monkeypatch):
    monkeypatch.delattr(sys.modules['__main__'], 'oryxflow_inputs', raising=False)
    with pytest.raises(FileNotFoundError, match='run the flow first'):
        rpt.default_inputs(RiNamed)
    oryxflow.run(RiNamed(), abort=True)
    spec = rpt.default_inputs(RiNamed)
    assert spec == rpt.inputs_of(RiNamed())
    assert rpt.default_inputs(RiNamed()) == spec
    with pytest.raises(TypeError):
        rpt.default_inputs(RiNamed(), x=1)


def test_default_inputs_keeps_injected(monkeypatch):
    injected = {'a': {'target': 'x.Y', 'path': 'p'}}
    monkeypatch.setattr(sys.modules['__main__'], 'oryxflow_inputs', injected, raising=False)
    assert rpt.default_inputs(RiNamed) is injected        # the task is not even built
