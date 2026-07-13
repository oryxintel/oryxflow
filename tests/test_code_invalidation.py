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
    oryxflow.core._code_warned.clear()   # process-level warning dedupe
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
        # the reason names the changed SYMBOL, not just the file
        assert r2.reasons[t.task_id] == 'code change (auto: codemod_auto1.py::FACTOR)'
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
        # the edit must touch the referenced symbol's own statement: unrelated
        # additions and comments are invisible at symbol granularity
        helper_path.write_text('FACTOR = 2 - 1\n')
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

    def test_auto_opt_in_pin_line_is_free(self, env, monkeypatch, recwarn):
        # the opt-in is itself a file edit (`code_version = '1'` gets typed into the
        # module) -- the pin line must be invisible to the hash or opting in could
        # never be free. Field-tested sequence: pin (free) -> bump (rerun) ->
        # unpin (free)
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_optin', AUTO_V1)
        t = mod.TaskAuto()
        oryxflow.run(t)

        pinned = AUTO_V1.replace('    def run', "    code_version = '1'\n    def run")
        path.write_text(pinned)                      # the on-disk edit of opting in
        _bump_mtime(path)
        mod.TaskAuto.code_version = '1'              # the in-memory effect of it
        r = oryxflow.run(t)
        assert r.ran == [] and r.warnings == []      # free opt-in, as documented

        path.write_text(pinned.replace("'1'", "'2'"))
        _bump_mtime(path, 20)
        mod.TaskAuto.code_version = '2'
        r2 = oryxflow.run(t)                         # a bump is a real token change
        assert r2.did_run(mod.TaskAuto)
        assert r2.reasons[t.task_id] == 'code change (1 -> 2)'

        path.write_text(AUTO_V1)                     # drop the pin line again
        _bump_mtime(path, 30)
        mod.TaskAuto.code_version = None
        r3 = oryxflow.run(t)
        assert r3.ran == [] and r3.warnings == []    # unpin just resumes

    def test_accept_code_clears_predates_guard(self, env, monkeypatch, capsys):
        # the "output predates current code" state (output exists, no record) must be
        # clearable without recomputing: accept_code(instance) stamps a baseline record
        monkeypatch.setattr(oryxflow.settings, 'code_version_auto', False)
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        body = MODULE_V1.replace("code_version = '1'", 'code_version = None')
        mod, path = _make_module(env, 'codemod_guard_accept', body)
        t = mod.TaskHash()
        oryxflow.run(t)                              # unversioned output, no record

        _bump_mtime(path, 3600)                      # source newer than the output
        mod.TaskHash.code_version = '1'
        with pytest.warns(oryxflow.StalenessWarning,
                          match='output predates current code'):
            oryxflow.run(t)
        assert oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id) is None

        accepted = oryxflow.accept_code(t)           # the blessing the warning points at
        assert t.task_id in accepted
        assert 're-stamped' in capsys.readouterr().out
        rec = oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id)
        assert rec is not None and rec['code_version'] == '1'
        r = oryxflow.run(t)                          # guard satisfied, no recompute
        assert r.ran == [] and r.warnings == []
        assert oryxflow.events.status()['pending_warnings'] == []

    def test_accept_code_empty_reports(self, env, capsys):
        assert oryxflow.accept_code() == []
        assert 'nothing accepted' in capsys.readouterr().out

    def test_warning_dedupe_per_process(self, env, monkeypatch, recwarn):
        # an unacknowledged warning repeats per build (WorkflowMulti = one build per
        # flow) -- the print/log channels emit the SAME message once per process, while
        # RunResult.warnings still reports it every build; a CHANGED condition re-warns
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_dedupe', MODULE_V1)
        t = mod.TaskHash()
        oryxflow.run(t)
        path.write_text(MODULE_V1_EDITED)
        _bump_mtime(path)

        def _staleness_count():
            return sum(isinstance(w.message, oryxflow.StalenessWarning)
                       for w in recwarn.list)

        r1 = oryxflow.run(t)
        assert _staleness_count() == 1 and r1.warnings != []
        r2 = oryxflow.run(t)                         # same condition -> no new emission
        assert _staleness_count() == 1
        assert r2.warnings != []                     # but the build still reports it

        # different condition (another file changed too) -> a NEW message re-warns
        edited_more = MODULE_V1_EDITED.replace('FACTOR = 2', 'FACTOR = 3')
        path.write_text(edited_more)
        _bump_mtime(path, 20)
        oryxflow.run(t)
        # same message text (same changed-file list) stays deduped; accept then a fresh
        # edit re-arms the emission
        oryxflow.accept_code(t)
        path.write_text(MODULE_V1_EDITED)
        _bump_mtime(path, 30)
        with pytest.warns(oryxflow.StalenessWarning,
                          match='changed since cached run'):
            oryxflow.run(t)

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

    def test_pin_line_hash_neutral(self, tmp_path):
        base = 'class T:\n    def run(self):\n        return 1\n'
        pinned = "class T:\n    code_version = '3'\n    def run(self):\n        return 1\n"
        annotated = "class T:\n    code_version: str = '3'\n    def run(self):\n        return 1\n"
        a, b, c = tmp_path / 'a.py', tmp_path / 'b.py', tmp_path / 'c.py'
        a.write_text(base)
        b.write_text(pinned)
        c.write_text(annotated)
        h = oryxflow.codehash.file_hash(a)
        assert oryxflow.codehash.file_hash(b) == h
        assert oryxflow.codehash.file_hash(c) == h
        # but a module-level `code_version` (not a class pin) is normal code
        d = tmp_path / 'd.py'
        d.write_text("code_version = '3'\n" + base)
        assert oryxflow.codehash.file_hash(d) != h

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
        # symbol level keeps the same coverage: the re-exported helper resolves to its
        # defining file, the lazy import narrows to the attribute used
        sym = oryxflow.codehash.task_hashes(mod.TaskPkg)
        assert 'mypkg/impl.py::helper' in sym
        assert 'lazyhelper.py::X' in sym


SIBLINGS = '''
import oryxflow

def helper_a():
    return 1

class TaskSibA(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'value': helper_a()})

class TaskSibB(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'value': 2})
'''

PIN_CHAIN = '''
import oryxflow

class TaskUp(oryxflow.tasks.TaskPickle):
    code_version = '1'
    def run(self):
        self.save({'a': 1})

class TaskDn(oryxflow.tasks.TaskPickle):
    def requires(self):
        return TaskUp()
    def run(self):
        self.save(self.inputLoad())
'''

PARAM_PINNED = '''
import oryxflow

class TaskPar(oryxflow.tasks.TaskPickle):
    idx = oryxflow.IntParameter(default=0)
    code_version = '1'
    def run(self):
        self.save({'value': self.idx})
'''


# the def is later rebound (`helper = _wrap(helper)`, the decorate-after-def idiom):
# BOTH binding statements are part of the symbol's hash
REBIND = '''
import oryxflow

def _wrap(f):
    return f

def helper():
    return 1

helper = _wrap(helper)

class TaskRebind(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'value': helper()})
'''


class TestPerTaskGranularity:
    """The hash unit is the task's own class + the symbols it references -- editing one
    task (or an unused helper) must never invalidate unrelated siblings in the same
    file. This is the sibling-isolation property the file-level v2 hashing lacked."""

    def test_sibling_edit_isolated(self, env, monkeypatch):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_sib1', SIBLINGS)
        ta, tb = mod.TaskSibA(), mod.TaskSibB()
        oryxflow.run([ta, tb])

        path.write_text(SIBLINGS.replace("'value': 2", "'value': 3"))
        _bump_mtime(path)
        r = oryxflow.run([ta, tb])
        assert r.did_run(mod.TaskSibB)
        assert not r.did_run(mod.TaskSibA)           # sibling untouched
        assert r.reasons[tb.task_id] == 'code change (auto: codemod_sib1.py::TaskSibB)'

    def test_helper_edit_hits_only_referencing_task(self, env, monkeypatch):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_sib2', SIBLINGS)
        ta, tb = mod.TaskSibA(), mod.TaskSibB()
        oryxflow.run([ta, tb])

        path.write_text(SIBLINGS.replace('return 1', 'return 10'))
        _bump_mtime(path)
        r = oryxflow.run([ta, tb])
        assert r.did_run(mod.TaskSibA)               # references helper_a
        assert not r.did_run(mod.TaskSibB)           # doesn't
        assert '::helper_a' in r.reasons[ta.task_id]

    def test_pinned_upstream_edit_does_not_ripple_via_reference(self, env, monkeypatch):
        # TaskDn's requires() names TaskUp, but Task references are wiring, not code:
        # a pinned-unbumped edit to TaskUp holds TaskUp (warn) AND must not rerun
        # TaskDn -- under file-level hashing the shared module would have dragged
        # TaskDn along
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_pinchain', PIN_CHAIN)
        dn = mod.TaskDn()
        oryxflow.run(dn)

        path.write_text(PIN_CHAIN.replace("{'a': 1}", "{'a': 2}"))
        _bump_mtime(path)
        with pytest.warns(oryxflow.StalenessWarning, match='TaskUp'):
            r = oryxflow.run(dn)
        assert r.ran == []                           # neither TaskUp nor TaskDn

    def test_v2_record_migrates_silently(self, env, monkeypatch):
        # a stored file-level (v2) record isn't comparable to symbol keys: it must
        # converge silently at grandfather trust (no rerun, output_id preserved)
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_v2mig', AUTO_V1)
        t = mod.TaskAuto()
        oryxflow.run(t)
        rec = dict(oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id))
        oid = rec['output_id']
        rec['v'] = 2
        rec['source_hashes'] = {'codemod_v2mig.py': oryxflow.codehash.file_hash(path)}
        oryxflow.state.put_record(oryxflow.settings.dirpath, t.task_id, rec)

        r = oryxflow.run(t)
        assert r.ran == [] and r.warnings == []
        rec2 = oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id)
        assert rec2['v'] == oryxflow.state.RECORD_V
        assert rec2['output_id'] == oid
        assert all('::' in k for k in rec2['source_hashes'])

    def test_warning_dedupe_family_level(self, env, monkeypatch, recwarn):
        # parameterized instances of one pinned family produce IDENTICAL warning text
        # (it names only the family): one printed warning, and RunResult.warnings
        # carries the deduped message set -- len() is "how many pending conditions",
        # it must not scale with the instance count
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_parpin', PARAM_PINNED)
        tasks = [mod.TaskPar(idx=0), mod.TaskPar(idx=1)]
        oryxflow.run(tasks)

        path.write_text(PARAM_PINNED.replace('self.idx', 'self.idx + 0'))
        _bump_mtime(path)
        r = oryxflow.run(tasks)
        assert len(r.warnings) == 1                  # deduped per build
        printed = [w for w in recwarn.list
                   if isinstance(w.message, oryxflow.StalenessWarning)]
        assert len(printed) == 1                     # deduped on the message

    def test_accept_walk_fault_isolated(self, env, monkeypatch, capsys):
        # a broken requires() (e.g. it needs inputs to enumerate) poisons the
        # recursive fingerprint too -- the node must STILL get blessed (secondary
        # record fields keep their stored values) instead of aborting the walk
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, _ = _make_module(env, 'codemod_acceptfault', PIN_CHAIN)
        dn = mod.TaskDn()
        oryxflow.run(dn)

        def _boom(self):
            raise RuntimeError('requires needs inputs')
        monkeypatch.setattr(mod.TaskDn, 'requires', _boom)
        accepted = oryxflow.accept_code(dn)
        assert dn.task_id in accepted                # blessed despite broken requires
        out = capsys.readouterr().out
        assert 're-stamped' in out

    def test_rebound_symbol_edit_detected(self, env, monkeypatch):
        # a name bound by several top-level statements (def + rebind) hashes ALL of
        # them: editing only the rebind line must rerun the referencing task
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_rebind', REBIND)
        t = mod.TaskRebind()
        oryxflow.run(t)

        path.write_text(REBIND.replace('helper = _wrap(helper)',
                                       'helper = _wrap(_wrap(helper))'))
        _bump_mtime(path)
        r = oryxflow.run(t)
        assert r.did_run(mod.TaskRebind)
        assert '::helper' in r.reasons[t.task_id]


# two finals sharing one root: a pipeline where the flow's configured default task
# does NOT reach everything (field-reported bulk-accept gap)
MULTI_FINAL = '''
import oryxflow

class TaskRoot(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'value': 1})

class TaskFinalA(oryxflow.tasks.TaskPickle):
    def requires(self):
        return TaskRoot()
    def run(self):
        self.save(self.inputLoad())

class TaskFinalB(oryxflow.tasks.TaskPickle):
    def requires(self):
        return TaskRoot()
    def run(self):
        self.save({'value': 2})
'''


class TestHardening:
    """Failure-mode behavior: advisory warnings under hostile warning filters, and
    bulk accept_code facing records whose keys no longer resolve."""

    def test_flow_accept_covers_all_run_finals(self, env, monkeypatch, recwarn):
        # field-reported gap: flow configured with ONE default final but driven as
        # flow.run([finals...]) -- a bare flow.accept_code() must persist baseline
        # records for EVERY final's subtree (creating records for grandfathered
        # outputs), so a fresh process warns zero and recomputes nothing. The
        # bless call itself runs on a FRESH Workflow with no run history (the
        # one-shot post-upgrade bless script), so coverage must come from the
        # imported task families, not from what this process happened to run.
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_multifinal', MULTI_FINAL)
        finals = [mod.TaskFinalA, mod.TaskFinalB]
        oryxflow.Workflow(task=mod.TaskFinalA).run(finals)

        # simulate the post-upgrade state: outputs on disk, no records, source newer
        (oryxflow.settings.dirpath / oryxflow.settings.state_filename).unlink()
        oryxflow.state.clear_cache()
        oryxflow.core._code_warned.clear()
        _bump_mtime(path, 3600)
        with pytest.warns(oryxflow.StalenessWarning, match='predates current code'):
            r = oryxflow.Workflow(task=mod.TaskFinalA).run(finals)
        assert r.ran == []

        flow = oryxflow.Workflow(task=mod.TaskFinalA)   # the bless script's flow
        accepted = flow.accept_code()                   # bare bulk form, no args
        want = {flow.get_task(c).task_id
                for c in (mod.TaskRoot, mod.TaskFinalA, mod.TaskFinalB)}
        assert want <= set(accepted)
        # persisted, not in-memory suppression: with state cache and warning dedupe
        # cleared (a fresh process), the full finals set is silent
        oryxflow.state.clear_cache()
        oryxflow.core._code_warned.clear()
        recwarn.clear()
        r2 = oryxflow.Workflow(task=mod.TaskFinalA).run(finals)
        assert r2.ran == [] and r2.warnings == []
        assert not any(isinstance(w.message, oryxflow.StalenessWarning)
                       for w in recwarn.list)
        for tid in want:
            assert oryxflow.state.get_record(oryxflow.settings.dirpath, tid) is not None

    def test_multi_result_warnings_deduped(self, env):
        # MultiRunResult.warnings is the deduped union across flows: per-flow builds
        # re-warn the same message for shared upstreams
        r1 = oryxflow.core.RunResult(True, [], [], [], warnings=['w1', 'w2'])
        r2 = oryxflow.core.RunResult(True, [], [], [], warnings=['w1'])
        m = oryxflow.core.MultiRunResult(a=r1, b=r2)
        assert m.warnings == ['w1', 'w2']

    def test_warn_survives_error_filter(self, env, monkeypatch):
        # an app-level `warnings.simplefilter('error')` turns warn() into a raise;
        # the advisory must not abort the build -- RunResult still carries it
        import warnings
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'codemod_werror', MODULE_V1)
        t = mod.TaskHash()
        oryxflow.run(t)

        path.write_text(MODULE_V1_EDITED)
        _bump_mtime(path)
        with warnings.catch_warnings():
            warnings.simplefilter('error', oryxflow.StalenessWarning)
            r = oryxflow.run(t)                      # must not raise
        assert r.ran == []
        assert any('changed since cached run' in w for w in r.warnings)

    def test_bulk_accept_reports_unresolvable_keys(self, env, monkeypatch, capsys):
        # a stored key whose file/symbol vanished (rename, move) can't be re-keyed
        # from the record alone: the bulk form must say so instead of silently
        # reading as "verified current"
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, _ = _make_module(env, 'codemod_bulkgone', AUTO_V1)
        t = mod.TaskAuto()
        oryxflow.run(t)
        rec = dict(oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id))
        rec['source_hashes'] = dict(rec['source_hashes'],
                                    **{'gone.py::vanished': 'deadbeef'})
        oryxflow.state.put_record(oryxflow.settings.dirpath, t.task_id, rec)

        accepted = oryxflow.accept_code()
        out = capsys.readouterr().out
        assert 'no longer exist' in out
        assert t.task_id not in accepted             # live keys unchanged: no restamp
