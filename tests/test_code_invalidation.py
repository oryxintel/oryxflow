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

    def test_transparency(self, env, monkeypatch):
        # transparency (no code_version -> feature fully inert) is the code_version_auto=False
        # contract; with auto on (the default) unversioned tasks get fingerprints by design
        monkeypatch.setattr(oryxflow.settings, 'code_version_auto', False)

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

        C.code_version = '1'          # pinning with unchanged code: free, no rerun
        r3 = oryxflow.run(C())
        assert r3.ran == []

        C.code_version = '2'          # bump only downstream -> only it reruns
        r4 = oryxflow.run(C())
        assert r4.did_run(C)
        assert not r4.did_run(A) and not r4.did_run(B)

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

    def test_grandfathering(self, env, monkeypatch):
        # first-time-add grandfathering as specified pre-auto; with auto on, an unversioned
        # run already stamps records, so adding code_version is a token change and reruns
        monkeypatch.setattr(oryxflow.settings, 'code_version_auto', False)

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
        # with auto on the unversioned run writes a record, so grandfathering (record
        # missing) never happens; the guard remains the pre-auto / auto-off safety net
        monkeypatch.setattr(oryxflow.settings, 'code_version_auto', False)
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


AUTO_V1 = '''
import oryxflow

FACTOR = 1

class TaskAuto(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'value': FACTOR})
'''

# comment/docstring-only edit: no hash change, no rerun
AUTO_COSMETIC = '''
import oryxflow

# a new comment
FACTOR = 1

class TaskAuto(oryxflow.tasks.TaskPickle):
    """A docstring that wasn't here before."""
    def run(self):
        # another comment
        self.save({'value': FACTOR})
'''

# real logic edit, no code_version anywhere: auto must rerun
AUTO_EDITED = '''
import oryxflow

FACTOR = 2

class TaskAuto(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'value': FACTOR})
'''

# A (explicit code_version) in its own module, B (auto) importing it
CHAIN_A = '''
import oryxflow

class TaskA(oryxflow.tasks.TaskPickle):
    code_version = '1'
    def run(self):
        self.save({'a': 1})
'''

CHAIN_B = '''
import oryxflow
import codemod_chain_a

class TaskB(oryxflow.tasks.TaskPickle):
    def requires(self):
        return codemod_chain_a.TaskA()
    def run(self):
        self.save(self.inputLoad())
'''

# chain over a shared helper, everything auto (for accept_code)
CHAIN_HELPER = '''
import oryxflow
import helper_accept

class TaskA2(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'value': helper_accept.FACTOR})

class TaskB2(oryxflow.tasks.TaskPickle):
    def requires(self):
        return TaskA2()
    def run(self):
        self.save(self.inputLoad())
'''

# A and B auto, in separate modules, B importing A (for mode-flip ripple checks)
FLIP_A = '''
import oryxflow

class TaskFA(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'a': 1})
'''

FLIP_B = '''
import oryxflow
import codemod_flip_a

class TaskFB(oryxflow.tasks.TaskPickle):
    def requires(self):
        return codemod_flip_a.TaskFA()
    def run(self):
        self.save(self.inputLoad())
'''

AUTO_USES_HELPER = '''
import oryxflow
import helper_auto

class TaskUses(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'value': helper_auto.FACTOR})
'''

AUTO_PLAIN = '''
import oryxflow

class TaskPlain(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'value': 0})
'''


class TestAutoInvalidation:
    """settings.code_version_auto (the default): the AST hash IS the code identity for
    tasks without an explicit code_version, so logic edits rerun with no attribute."""

    def test_auto_rerun_and_reason(self, env, monkeypatch):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_auto1', AUTO_V1)
        t = mod.TaskAuto()
        assert t._code_fingerprint is not None       # auto token applies
        r1 = oryxflow.run(t)
        assert r1.did_run(mod.TaskAuto)
        assert oryxflow.run(t).ran == []             # untouched -> cache trusted

        path.write_text(AUTO_EDITED)
        _bump_mtime(path)
        r2 = oryxflow.run(t)                         # logic edit -> rerun, no bump needed
        assert r2.did_run(mod.TaskAuto)
        assert r2.reasons[t.task_id] == 'code change (auto: codemod_auto1.py)'
        assert oryxflow.run(t).ran == []             # re-baselined
        assert any(e['type'] == 'task_ran' and e.get('auto')
                   for e in oryxflow.events.iter_events())

    def test_auto_cosmetic_silent(self, env, monkeypatch, recwarn):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_auto2', AUTO_V1)
        t = mod.TaskAuto()
        oryxflow.run(t)
        path.write_text(AUTO_COSMETIC)
        _bump_mtime(path)
        r = oryxflow.run(t)
        assert r.ran == [] and r.warnings == []
        assert not any(isinstance(w.message, oryxflow.StalenessWarning)
                       for w in recwarn.list)

    def test_auto_helper_transitive(self, env, monkeypatch):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        _make_module(env, 'helper_auto', 'FACTOR = 1\n')
        mod_u, _ = _make_module(env, 'codemod_uses', AUTO_USES_HELPER)
        mod_p, _ = _make_module(env, 'codemod_plain', AUTO_PLAIN)
        tu, tp = mod_u.TaskUses(), mod_p.TaskPlain()
        oryxflow.run([tu, tp])

        helper_path = env / 'helper_auto.py'
        helper_path.write_text('FACTOR = 2\n')
        _bump_mtime(helper_path)
        r = oryxflow.run([tu, tp])
        assert r.did_run(mod_u.TaskUses)             # imports the helper -> reruns
        assert not r.did_run(mod_p.TaskPlain)        # doesn't -> untouched
        assert 'helper_auto.py' in r.reasons[tu.task_id]

    def test_auto_cross_build_folding(self, env, monkeypatch):
        # upstream reruns in its own build; a FRESH build of downstream must still
        # rerun it -- the stored folded fingerprint, not the in-build cascade
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod_a, _ = _make_module(env, 'codemod_chain_a', CHAIN_A)
        mod_b, _ = _make_module(env, 'codemod_chain_b', CHAIN_B)
        b = mod_b.TaskB()
        oryxflow.run(b)

        mod_a.TaskA.code_version = '2'
        r1 = oryxflow.run(mod_a.TaskA())             # run A alone; it stamps its record
        assert r1.did_run(mod_a.TaskA)
        r2 = oryxflow.run(b)
        assert r2.did_run(mod_b.TaskB)
        assert not r2.did_run(mod_a.TaskA)
        assert r2.reasons[b.task_id] == 'upstream rerun'

    def test_auto_precedence_explicit_wins_then_resumes(self, env, monkeypatch):
        # code_version present -> it stays the authority (edit warns, doesn't rerun);
        # removed -> auto resumes; the edit masked while pinned reruns once, then silent
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_prec', MODULE_V1)
        t = mod.TaskHash()
        oryxflow.run(t)

        path.write_text(MODULE_V1_EDITED)
        _bump_mtime(path)
        with pytest.warns(oryxflow.StalenessWarning, match='changed since cached run'):
            r = oryxflow.run(t)
        assert r.ran == []                           # pinned by the explicit token

        mod.TaskHash.code_version = None
        r2 = oryxflow.run(t)
        assert r2.did_run(mod.TaskHash)
        assert r2.reasons[t.task_id] == 'code change (1 -> auto)'
        assert oryxflow.run(t).ran == []

    def test_auto_mode_flip_free_and_no_ripple(self, env, monkeypatch):
        # pin an unchanged auto task -> no rerun (free opt-in); unpin -> no rerun
        # ("just resumes"); neither flip ripples downstream (output_id folding).
        # Records converge to the current mode on the next run.
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod_a, _ = _make_module(env, 'codemod_flip_a', FLIP_A)
        mod_b, _ = _make_module(env, 'codemod_flip_b', FLIP_B)
        b = mod_b.TaskFB()
        oryxflow.run(b)
        a_tid = mod_a.TaskFA().task_id

        mod_a.TaskFA.code_version = '1'      # pin: code unchanged -> free
        assert oryxflow.run(b).ran == []
        rec = oryxflow.state.get_record(oryxflow.settings.dirpath, a_tid)
        assert rec['code_version'] == '1'    # record converged to the pin

        mod_a.TaskFA.code_version = None     # unpin: just resumes
        assert oryxflow.run(b).ran == []
        rec = oryxflow.state.get_record(oryxflow.settings.dirpath, a_tid)
        assert rec['code_version'] is None

    def test_auto_runtime_redefinition_inert(self, env):
        # redefining a class in-process doesn't touch the module file, so auto
        # (deliberately) cannot see it -- no spurious reruns from test-style redefinition
        class TR(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({'a': 1})

        assert TR()._code_fingerprint is not None    # this test file is hashable
        r1 = oryxflow.run(TR())
        assert r1.did_run(TR)

        class TR(oryxflow.tasks.TaskPickle):         # same family, new logic, same file
            def run(self):
                self.save({'a': 2})

        assert oryxflow.run(TR()).ran == []

    def test_auto_record_migration(self, env, monkeypatch):
        # pre-auto (v1-schema) record or interpreter change: silent re-stamp, never a rerun
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, _ = _make_module(env, 'codemod_mig', AUTO_V1)
        t = mod.TaskAuto()
        oryxflow.run(t)

        rec = dict(oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id))
        rec.pop('v')
        rec['fingerprint'] = 'stale-formula'         # old-formula fingerprint
        oryxflow.state.put_record(oryxflow.settings.dirpath, t.task_id, rec)
        r = oryxflow.run(t)
        assert r.ran == []
        rec2 = oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id)
        assert rec2['v'] == oryxflow.state.RECORD_V
        assert rec2['fingerprint'] == t._code_fingerprint

        rec3 = dict(rec2)
        rec3['py'] = '0.0'                           # interpreter changed
        oryxflow.state.put_record(oryxflow.settings.dirpath, t.task_id, rec3)
        assert oryxflow.run(t).ran == []
        assert oryxflow.state.get_record(
            oryxflow.settings.dirpath, t.task_id)['py'] == oryxflow.codehash.PY_TAG

    def test_auto_blind_spot_inert(self, env, monkeypatch):
        # module not under the project root -> unhashable -> auto degrades to inert
        # (pre-auto behavior), never a false rerun or a false green record
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)

        class TB(oryxflow.tasks.TaskPickle):         # test file lives outside env
            def run(self):
                self.save({'a': 1})

        assert TB()._code_fingerprint is None
        oryxflow.run(TB())
        assert not (oryxflow.settings.dirpath / oryxflow.settings.state_filename).exists()

    def test_auto_accept_code_upstream(self, env, monkeypatch):
        # accept_code(instance) re-stamps the whole upstream band (fingerprints fold
        # deps), so an output-equivalent helper edit skips the recompute
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        _make_module(env, 'helper_accept', 'FACTOR = 1\n')
        mod, _ = _make_module(env, 'codemod_accept_up', CHAIN_HELPER)
        b = mod.TaskB2()
        oryxflow.run(b)

        helper_path = env / 'helper_accept.py'
        helper_path.write_text('FACTOR = 1  # equivalent refactor\nUNUSED = 2\n')
        _bump_mtime(helper_path)
        assert not b.complete()

        accepted = oryxflow.accept_code(b)
        assert set(accepted) == {b.task_id, mod.TaskA2().task_id}
        r = oryxflow.run(b)
        assert r.ran == []
        assert any(e['type'] == 'code_accepted'
                   for e in oryxflow.events.iter_events())

    def test_auto_accept_code_workflow(self, env, monkeypatch):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        _make_module(env, 'helper_accept2', 'FACTOR = 1\n')
        body = CHAIN_HELPER.replace('helper_accept', 'helper_accept2')
        mod, _ = _make_module(env, 'codemod_accept_wf', body)
        flow = oryxflow.Workflow(task=mod.TaskB2)
        flow.run()

        helper_path = env / 'helper_accept2.py'
        helper_path.write_text('FACTOR = 1\nUNUSED = 3\n')
        _bump_mtime(helper_path)
        accepted = flow.accept_code()                # defaults to the flow's default task
        assert len(accepted) == 2
        assert flow.run().ran == []

    def test_auto_expensive_guard(self, env, monkeypatch):
        # a code change on a task whose last run was expensive is a decision, not a
        # side effect: held complete + warned with the exits (reset/accept/pin)
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_exp', AUTO_V1)
        t = mod.TaskAuto()
        oryxflow.run(t)
        rec = dict(oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id))
        rec['duration_s'] = 9999                 # pretend the run was expensive
        oryxflow.state.put_record(oryxflow.settings.dirpath, t.task_id, rec)

        path.write_text(AUTO_EDITED)
        _bump_mtime(path)
        with pytest.warns(oryxflow.StalenessWarning,
                          match='expensive-recompute guard'):
            r = oryxflow.run(t)
        assert r.ran == []                       # not silently recomputed

        t.reset(confirm=False)                   # reset exit: recompute proceeds
        assert oryxflow.run(t).did_run(mod.TaskAuto)
        assert oryxflow.run(t).ran == []

        rec = dict(oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id))
        rec['duration_s'] = 9999
        oryxflow.state.put_record(oryxflow.settings.dirpath, t.task_id, rec)
        path.write_text(AUTO_V1)
        _bump_mtime(path, 20)
        monkeypatch.setattr(oryxflow.settings, 'code_version_auto_expensive_s', None)
        assert oryxflow.run(t).did_run(mod.TaskAuto)   # guard off -> normal auto rerun

    def test_auto_flag_off(self, env, monkeypatch):
        monkeypatch.setattr(oryxflow.settings, 'code_version_auto', False)
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_off', AUTO_V1)
        t = mod.TaskAuto()
        assert t._code_fingerprint is None
        oryxflow.run(t)
        path.write_text(AUTO_EDITED)
        _bump_mtime(path)
        assert oryxflow.run(t).ran == []             # pre-auto behavior


PKG_TASK = '''
import oryxflow
from mypkg import helper

class TaskPkg(oryxflow.tasks.TaskPickle):
    def run(self):
        import lazyhelper
        self.save({'v': helper() + lazyhelper.X})
'''


class TestCodehash:
    """Import-resolution completeness + normalization determinism."""

    def test_crlf_invariant(self, tmp_path):
        src = 'def f(x):\n    return x + 1\n'
        a = tmp_path / 'a.py'
        b = tmp_path / 'b.py'
        a.write_bytes(src.encode())
        b.write_bytes(src.replace('\n', '\r\n').encode())
        assert oryxflow.codehash.file_hash(a) == oryxflow.codehash.file_hash(b)

    def test_package_reexport_and_lazy_import_walked(self, env, monkeypatch):
        # blind spots are only as narrow as the walk is complete: __init__ re-exports
        # and function-local (lazy) imports must be in the hash set
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        monkeypatch.syspath_prepend(str(env))
        pkg = env / 'mypkg'
        pkg.mkdir()
        (pkg / 'impl.py').write_text('def helper():\n    return 1\n')
        (pkg / '__init__.py').write_text('from .impl import helper\n')
        (env / 'lazyhelper.py').write_text('X = 1\n')
        mod, _ = _make_module(env, 'codemod_pkg', PKG_TASK)
        hashes = oryxflow.codehash.module_hashes(mod.TaskPkg)
        assert 'mypkg/__init__.py' in hashes
        assert 'mypkg/impl.py' in hashes          # reached via the __init__ re-export
        assert 'lazyhelper.py' in hashes          # function-local import still walked
