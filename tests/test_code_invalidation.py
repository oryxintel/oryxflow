import os
import sys
import pathlib
import importlib.util

import pytest

import oryxflow
import oryxflow.state
import oryxflow.codehash
from oryxflow.log import logger


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated data dir + event dir per test; state/codehash caches reset."""
    datadir = tmp_path / 'data'
    datadir.mkdir()
    monkeypatch.setattr(oryxflow.settings, 'dir', str(datadir))
    monkeypatch.setattr(oryxflow.settings, 'dirpath', datadir)
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    oryxflow.state.clear_cache()
    yield tmp_path


def _make_module(tmp_path, name, body):
    """Write a task module under tmp_path and import it (tmp_path acts as project root)."""
    path = tmp_path / (name + '.py')
    path.write_text(body)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod, path


def _bump_mtime(path, seconds=10):
    st = os.stat(path)
    os.utime(path, (st.st_atime + seconds, st.st_mtime + seconds))


MODULE_V1 = '''
import oryxflow

FACTOR = 1

class TaskHash(oryxflow.tasks.TaskPickle):
    code_version = '1'
    def run(self):
        self.save({'value': FACTOR})
'''

# comment/docstring-only edit: must produce no hash change and no warning
MODULE_V1_COSMETIC = '''
import oryxflow

# a new comment
FACTOR = 1

class TaskHash(oryxflow.tasks.TaskPickle):
    """A docstring that wasn't here before."""
    code_version = '1'
    def run(self):
        # another comment
        self.save({'value': FACTOR})
'''

# real logic edit, same code_version: must warn
MODULE_V1_EDITED = '''
import oryxflow

FACTOR = 2

class TaskHash(oryxflow.tasks.TaskPickle):
    code_version = '1'
    def run(self):
        self.save({'value': FACTOR})
'''


class TestInvalidation:

    def test_transparency(self, env):
        class T1(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({'a': 1})

        class T2(oryxflow.tasks.TaskPickle):
            def requires(self):
                return T1()
            def run(self):
                self.save(self.inputLoad())

        t = T2()
        assert t._code_fingerprint is None
        path_before = t.output().path
        r1 = oryxflow.run(t)
        assert r1.did_run(T2)
        r2 = oryxflow.run(t)
        assert r2.ran == [] and r2.complete == [t]
        assert t.output().path == path_before
        # no code_version anywhere -> no record store created
        assert not (oryxflow.settings.dirpath / oryxflow.settings.state_filename).exists()

    def test_bump_reruns_same_path(self, env):
        class T(oryxflow.tasks.TaskPickle):
            code_version = '1'
            def run(self):
                self.save({'a': 1})

        t = T()
        path1 = t.output().path
        r1 = oryxflow.run(t)
        assert r1.did_run(T)
        assert oryxflow.run(t).ran == []
        rec1 = oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id)
        assert rec1['code_version'] == '1'

        T.code_version = '2'
        assert not t.complete()
        r3 = oryxflow.run(t)
        assert r3.did_run(T)
        assert t.output().path == path1        # overwrite in place
        rec2 = oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id)
        assert rec2['code_version'] == '2'
        assert rec2['fingerprint'] != rec1['fingerprint']

    def test_propagation_chain(self, env):
        class A(oryxflow.tasks.TaskPickle):
            code_version = '1'
            def run(self):
                self.save({'a': 1})

        class B(oryxflow.tasks.TaskPickle):
            def requires(self):
                return A()
            def run(self):
                self.save(self.inputLoad())

        class C(oryxflow.tasks.TaskPickle):
            def requires(self):
                return B()
            def run(self):
                self.save(self.inputLoad())

        r1 = oryxflow.run(C())
        assert len(r1.ran) == 3

        A.code_version = '2'          # bump upstream -> whole band reruns
        r2 = oryxflow.run(C())
        assert r2.did_run(A) and r2.did_run(B) and r2.did_run(C)

        C.code_version = '1'          # bump only downstream -> only it reruns
        r3 = oryxflow.run(C())
        assert r3.did_run(C)
        assert not r3.did_run(A) and not r3.did_run(B)

    def test_propagation_diamond(self, env):
        class A(oryxflow.tasks.TaskPickle):
            code_version = '1'
            def run(self):
                self.save({'a': 1})

        class B(oryxflow.tasks.TaskPickle):
            def requires(self):
                return A()
            def run(self):
                self.save(self.inputLoad())

        class C(oryxflow.tasks.TaskPickle):
            def requires(self):
                return A()
            def run(self):
                self.save(self.inputLoad())

        class D(oryxflow.tasks.TaskPickle):
            def requires(self):
                return {'b': B(), 'c': C()}
            def run(self):
                self.save({'d': 1})

        assert len(oryxflow.run(D()).ran) == 4
        A.code_version = '2'
        r = oryxflow.run(D())
        assert sorted(t.task_family for t in r.ran) == ['A', 'B', 'C', 'D']
        assert r.reasons[D().task_id] == 'upstream rerun'

    def test_grandfathering(self, env):
        class A(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({'a': 1})

        class B(oryxflow.tasks.TaskPickle):
            def requires(self):
                return A()
            def run(self):
                self.save(self.inputLoad())

        oryxflow.run(B())              # unversioned: no records
        assert not (oryxflow.settings.dirpath / oryxflow.settings.state_filename).exists()

        A.code_version = '1'           # first-time add -> existing output grandfathered
        r = oryxflow.run(B())
        assert r.ran == []
        rec = oryxflow.state.get_record(oryxflow.settings.dirpath, A().task_id)
        assert rec is not None and rec['code_version'] == '1'

        A.code_version = '2'           # now the baseline exists, bumps bite
        r2 = oryxflow.run(B())
        assert r2.did_run(A) and r2.did_run(B)
        assert r2.reasons[A().task_id] == 'code change (1 -> 2)'

    def test_identity_stable_across_bumps(self, env):
        class T(oryxflow.tasks.TaskPickle):
            code_version = '1'
            def run(self):
                self.save({'a': 1})

        t = T()
        tid, rep, h = t.task_id, repr(t), hash(t)
        T.code_version = '9'
        assert t.task_id == tid and repr(t) == rep and hash(t) == h
        assert T() == t

    def test_normalization_and_warning(self, env, monkeypatch, recwarn):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_norm', MODULE_V1)
        t = mod.TaskHash()
        oryxflow.run(t)

        # cosmetic edit: comments + docstring only -> no hash change, no warning
        path.write_text(MODULE_V1_COSMETIC)
        _bump_mtime(path)
        r = oryxflow.run(t)
        assert r.ran == [] and r.warnings == []
        assert not any(isinstance(w.message, oryxflow.StalenessWarning)
                       for w in recwarn.list)

        # real edit without a bump: cached output reused, warning on every channel
        path.write_text(MODULE_V1_EDITED)
        _bump_mtime(path, 20)
        sink = []
        handler = oryxflow.enable_logging(level='WARNING', sink=sink.append,
                                          colorize=False)
        try:
            with pytest.warns(oryxflow.StalenessWarning,
                              match='changed since cached run'):
                r2 = oryxflow.run(t)
        finally:
            oryxflow.disable_logging()
            logger.remove(handler)
        assert r2.ran == []                      # advisory only, no rerun
        assert any('changed since cached run' in w for w in r2.warnings)
        assert any('codemod_norm.py' in w for w in r2.warnings)
        assert any('changed since cached run' in str(m) for m in sink)

    def test_accept_code(self, env, monkeypatch, recwarn):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_accept', MODULE_V1)
        t = mod.TaskHash()
        oryxflow.run(t)
        path.write_text(MODULE_V1_EDITED)
        _bump_mtime(path)
        with pytest.warns(oryxflow.StalenessWarning):
            oryxflow.run(t)
        assert oryxflow.events.status()['pending_warnings'] != []

        accepted = oryxflow.accept_code(t)
        assert t.task_id in accepted
        recwarn.clear()
        r = oryxflow.run(t)                      # silent, no rerun
        assert r.ran == [] and r.warnings == []
        assert not any(isinstance(w.message, oryxflow.StalenessWarning)
                       for w in recwarn.list)
        assert any(e['type'] == 'code_accepted'
                   for e in oryxflow.events.iter_events())
        assert oryxflow.events.status()['pending_warnings'] == []

    def test_grandfather_mtime_guard(self, env, monkeypatch):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        body = MODULE_V1.replace("code_version = '1'", 'code_version = None')
        mod, path = _make_module(env, 'codemod_guard', body)
        t = mod.TaskHash()
        oryxflow.run(t)                          # unversioned output on disk

        _bump_mtime(path, 3600)                  # source now newer than the output
        mod.TaskHash.code_version = '1'          # first-time add after an edit
        with pytest.warns(oryxflow.StalenessWarning,
                          match='output predates current code'):
            oryxflow.run(t)
        # not silently stamped as current
        assert oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id) is None

    def test_keep_versions(self, env):
        class T(oryxflow.tasks.TaskPickle):
            code_version = '1'
            keep_versions = True
            def run(self):
                self.save({'a': 1})

        t = T()
        path_v1 = t.output().path
        assert 'v1' in [p.name for p in pathlib.Path(path_v1).parents]
        oryxflow.run(t)
        assert pathlib.Path(path_v1).exists()

        T.code_version = '2'
        path_v2 = t.output().path
        assert path_v2 != path_v1
        r = oryxflow.run(t)
        assert r.did_run(T)
        assert pathlib.Path(path_v2).exists()
        assert pathlib.Path(path_v1).exists()    # old version intact

    def test_external(self, env):
        class E(oryxflow.tasks.TaskPickle):
            external = True

        class B(oryxflow.tasks.TaskPickle):
            code_version = '1'
            def requires(self):
                return E()
            def run(self):
                self.save(self.inputLoad())

        E().output().save({'e': 1})              # produced elsewhere
        r1 = oryxflow.run(B())
        assert r1.did_run(B)
        B.code_version = '2'                     # bump propagates around the external
        r2 = oryxflow.run(B())
        assert r2.did_run(B)
        assert not r2.did_run(E)

    def test_aggregator_propagation(self, env):
        class T1(oryxflow.tasks.TaskPickle):
            code_version = '1'
            def run(self):
                self.save({'a': 1})

        class T2(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({'b': 2})

        class Agg(oryxflow.tasks.TaskAggregator):
            def run(self):
                yield T1()
                yield T2()

        class D(oryxflow.tasks.TaskPickle):
            def requires(self):
                return Agg()
            def run(self):
                self.save({'d': 1})

        oryxflow.run(D())
        T1.code_version = '2'
        r = oryxflow.run(D())
        assert r.did_run(T1) and r.did_run(D)
        assert not r.did_run(T2)
