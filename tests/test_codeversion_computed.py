"""``code_version`` declared as a METHOD: the value it returns is the task's code identity.

Auto invalidation hashes Python, so a task whose result is determined by bytes outside the
import graph -- a rendered prompt, a ``.sql`` file, a resolved model id -- kept serving a stale
cached output. Computing the token folds those bytes in: the method is called on every
completeness check and its return value hashed to ``fn:<md5>``, so the task reruns when the
thing it actually depends on moves, with no version string to remember to bump.

Covers: a computed token invalidates and a stable one doesn't; ``hash_files`` drives it from
file CONTENT (not mtime) and raises rather than silently hashing nothing; the constant form is
untouched; editing the method body alone is invisible to the advisory AST hash; and the state
record stores the RESOLVED token at schema v4, never a bound-method repr.
"""
import os
import sys
import json
import pathlib
import importlib.util

import pytest

import oryxflow
import oryxflow.state
import oryxflow.codehash


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated data dir + event dir per test; state/codehash caches reset."""
    datadir = tmp_path / 'data'
    datadir.mkdir()
    monkeypatch.setattr(oryxflow.settings, 'dir', str(datadir))
    monkeypatch.setattr(oryxflow.settings, 'dirpath', datadir)
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    monkeypatch.setattr(oryxflow.settings, 'code_version_auto', True)
    oryxflow.state.clear_cache()
    oryxflow.core._code_warned.clear()
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


def _record(task):
    return oryxflow.state.get_record(oryxflow.settings.dirpath, task.task_id)


# what code_version() reads: a stand-in for the non-Python thing under test (a rendered
# template, a resolved snapshot id). Mutated in-process, so no source file moves with it.
_PROBE = ['v-one']

# where the .sql files live, so code_version() can hash them without a chdir
_SQLROOT = [None]


class TestComputedToken:
    """1, 2, 7 -- the token moves with what the method returns, and only with that."""

    def test_computed_token_invalidates(self, env):
        class ProbeA(oryxflow.tasks.TaskPickle):
            def code_version(self):
                return _PROBE[0]
            def run(self):
                self.save({'v': _PROBE[0]})

        _PROBE[0] = 'v-one'
        t = ProbeA()
        token1 = oryxflow.core._resolve_code_version(t)
        assert token1.startswith('fn:')
        r1 = oryxflow.run(t)
        assert r1.did_run(ProbeA)
        assert t.complete()
        assert t.outputLoad() == {'v': 'v-one'}

        _PROBE[0] = 'v-two'                       # the non-Python input changed
        assert oryxflow.core._resolve_code_version(t) != token1
        assert not t.complete()                   # ...and that alone makes it stale

        r2 = oryxflow.run(t)
        assert r2.did_run(ProbeA)
        assert r2.reasons[t.task_id] == 'code change ({} -> {})'.format(
            token1, oryxflow.core._resolve_code_version(t))
        assert t.outputLoad() == {'v': 'v-two'}   # actually recomputed
        assert oryxflow.run(t).ran == []          # re-baselined

    def test_stable_computed_token_no_rerun(self, env):
        class ProbeB(oryxflow.tasks.TaskPickle):
            def code_version(self):
                return _PROBE[0]
            def run(self):
                self.save({'v': _PROBE[0]})

        _PROBE[0] = 'steady'
        t = ProbeB()
        assert oryxflow.run(t).did_run(ProbeB)

        r = oryxflow.run(t)                       # probe untouched -> cache trusted
        assert not r.did_run(ProbeB)
        assert r.ran == [] and r.complete == [t]
        assert r.warnings == []

    def test_computed_token_propagates_downstream(self, env):
        # the point of folding it into the fingerprint: the whole band reruns, not just
        # the task holding the method
        class ProbeUp(oryxflow.tasks.TaskPickle):
            def code_version(self):
                return _PROBE[0]
            def run(self):
                self.save({'v': _PROBE[0]})

        class ProbeDown(oryxflow.tasks.TaskPickle):
            def requires(self):
                return ProbeUp()
            def run(self):
                self.save(self.inputLoad())

        _PROBE[0] = 'p1'
        assert len(oryxflow.run(ProbeDown()).ran) == 2

        _PROBE[0] = 'p2'
        r = oryxflow.run(ProbeDown())
        assert r.did_run(ProbeUp) and r.did_run(ProbeDown)
        assert r.reasons[ProbeDown().task_id] == 'upstream rerun'

    def test_keep_versions_path_is_the_resolved_token(self, env):
        # keep_versions names the output directory after the version; str(<bound method>)
        # carries a memory address, which would move the path every process
        class ProbeKeep(oryxflow.tasks.TaskPickle):
            keep_versions = True
            def code_version(self):
                return _PROBE[0]
            def run(self):
                self.save({'v': _PROBE[0]})

        _PROBE[0] = 'keep-1'
        t = ProbeKeep()
        token = oryxflow.core._resolve_code_version(t)
        vdir = next(p.name for p in pathlib.Path(t.output().path).parents
                    if p.name.startswith('v'))
        assert vdir == 'v' + token.replace(':', '_')
        assert '0x' not in vdir and 'bound' not in vdir

        oryxflow.run(t)
        path_v1 = t.output().path
        assert pathlib.Path(path_v1).exists()
        assert t.output().path == path_v1          # stable within the process

        _PROBE[0] = 'keep-2'                       # a new version gets its own directory
        assert t.output().path != path_v1
        assert oryxflow.run(t).did_run(ProbeKeep)
        assert pathlib.Path(path_v1).exists()      # old version intact

    def test_record_round_trip(self, env):
        class ProbeRec(oryxflow.tasks.TaskPickle):
            def code_version(self):
                return {'b': 2, 'a': 1}           # dicts serialize with sorted keys
            def run(self):
                self.save({'a': 1})

        t = ProbeRec()
        oryxflow.run(t)

        rec = _record(t)
        assert rec['v'] == 4 == oryxflow.state.RECORD_V
        stored = rec['code_version']
        assert isinstance(stored, str)
        assert stored.startswith('fn:') and len(stored) == len('fn:') + 16
        assert 'bound method' not in stored and 'ProbeRec' not in stored
        assert stored == oryxflow.core._resolve_code_version(t)

        # and it survives the JSON store, which a bound method would not
        raw = json.loads((oryxflow.settings.dirpath
                          / oryxflow.settings.state_filename).read_text())
        assert raw[t.task_id]['code_version'] == stored


class TestHashFiles:
    """3, 4 -- content, not mtime; and no silent no-op on a typo'd pattern."""

    def test_file_content_drives_rerun_not_mtime(self, env):
        qdir = env / 'q'
        qdir.mkdir()
        sql = qdir / 'report.sql'
        sql.write_text('select 1\n')
        _SQLROOT[0] = str(env)

        class Query(oryxflow.tasks.TaskPickle):
            def code_version(self):
                return oryxflow.hash_files('q/*.sql', root=_SQLROOT[0])
            def run(self):
                self.save({'sql': (qdir / 'report.sql').read_text()})

        t = Query()
        assert oryxflow.run(t).did_run(Query)
        assert oryxflow.run(t).ran == []

        sql.write_text('select 2\n')              # the bytes that determine the result
        assert not t.complete()
        r = oryxflow.run(t)
        assert r.did_run(Query)
        assert t.outputLoad() == {'sql': 'select 2\n'}

        _bump_mtime(sql, 3600)                    # same bytes, newer clock
        assert t.complete()
        assert oryxflow.run(t).ran == []

        sql.write_text('select 2\n')              # rewritten identically: still no rerun
        _bump_mtime(sql, 7200)
        assert oryxflow.run(t).ran == []

    def test_adding_a_matching_file_moves_the_digest(self, env):
        qdir = env / 'q2'
        qdir.mkdir()
        (qdir / 'a.sql').write_text('select 1\n')
        h1 = oryxflow.hash_files('q2/*.sql', root=str(env))
        (qdir / 'b.sql').write_text('select 2\n')
        assert oryxflow.hash_files('q2/*.sql', root=str(env)) != h1
        # the name is part of the digest, so a rename registers
        (qdir / 'b.sql').rename(qdir / 'c.sql')
        assert oryxflow.hash_files('q2/*.sql', root=str(env)) != h1

    def test_hash_files_raises_on_no_match(self, env):
        (env / 'q3').mkdir()
        with pytest.raises(FileNotFoundError):
            oryxflow.hash_files('q3/*.sql', root=str(env))
        with pytest.raises(FileNotFoundError):
            oryxflow.hash_files('nowhere/**/*.jinja2', root=str(env))
        # one good pattern does not excuse a bad one
        (env / 'q3' / 'ok.sql').write_text('select 1\n')
        with pytest.raises(FileNotFoundError):
            oryxflow.hash_files('q3/*.sql', 'q3/*.yaml', root=str(env))


CONST_V1 = '''
import oryxflow

FACTOR = 1

class TaskConst(oryxflow.tasks.TaskPickle):
    code_version = '1'
    def run(self):
        self.save({'value': FACTOR})
'''

CONST_EDITED = '''
import oryxflow

FACTOR = 2

class TaskConst(oryxflow.tasks.TaskPickle):
    code_version = '1'
    def run(self):
        self.save({'value': FACTOR})
'''


class TestConstantFormUntouched:
    """5 -- routing the reads through the resolver must be a no-op for a plain string."""

    def test_constant_pin_reruns_on_bump_and_warns_on_silent_edit(self, env, monkeypatch,
                                                                  recwarn):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'cvconst_mod', CONST_V1)
        t = mod.TaskConst()
        assert oryxflow.run(t).did_run(mod.TaskConst)
        assert oryxflow.run(t).ran == []
        assert _record(t)['code_version'] == '1'      # the string itself, not a fn: hash

        # a real logic edit with the pin unchanged: advisory only, no rerun
        path.write_text(CONST_EDITED)
        _bump_mtime(path)
        with pytest.warns(oryxflow.StalenessWarning, match='code_version still 1'):
            r = oryxflow.run(t)
        assert r.ran == []
        assert any('changed since cached run' in w for w in r.warnings)
        assert not any('code_version()' in w for w in r.warnings)   # constant wording

        # bumping the pin is what recomputes
        mod.TaskConst.code_version = '2'
        r2 = oryxflow.run(t)
        assert r2.did_run(mod.TaskConst)
        assert r2.reasons[t.task_id] == 'code change (1 -> 2)'
        assert _record(t)['code_version'] == '2'

    def test_unversioned_auto_task_unaffected(self, env, monkeypatch):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        body = CONST_V1.replace("    code_version = '1'\n", '')
        mod, path = _make_module(env, 'cvauto_mod', body)
        t = mod.TaskConst()
        oryxflow.run(t)
        assert _record(t)['code_version'] is None     # auto: no own token recorded

        path.write_text(CONST_EDITED.replace("    code_version = '1'\n", ''))
        _bump_mtime(path)
        r = oryxflow.run(t)
        assert r.did_run(mod.TaskConst)
        assert r.reasons[t.task_id] == 'code change (auto: cvauto_mod.py::FACTOR)'


# same returned value, different method source: the AST hash must not see the difference
CV_METHOD_A = '''
import oryxflow

FACTOR = 1

class TaskMethod(oryxflow.tasks.TaskPickle):
    def code_version(self):
        return 'tok-a'
    def run(self):
        self.save({'value': FACTOR})
'''

CV_METHOD_A_REWRITTEN = '''
import oryxflow

FACTOR = 1

class TaskMethod(oryxflow.tasks.TaskPickle):
    def code_version(self):
        """Rewritten body, same answer."""
        parts = ['tok', 'a']
        return '-'.join(parts)
    def run(self):
        self.save({'value': FACTOR})
'''

CV_METHOD_B = '''
import oryxflow

FACTOR = 1

class TaskMethod(oryxflow.tasks.TaskPickle):
    def code_version(self):
        return 'tok-b'
    def run(self):
        self.save({'value': FACTOR})
'''


class TestMethodBodyIsTokenNotLogic:
    """6 -- a `def code_version` is the token, so its source is stripped from the AST hash.

    Without the exclusion, editing the method would move the computed token AND the advisory
    source hash at once, warning "code changed but code_version didn't" about a change the
    token already handled.
    """

    def test_method_source_is_excluded_from_the_ast_hash(self, tmp_path):
        a = tmp_path / 'a.py'
        b = tmp_path / 'b.py'
        c = tmp_path / 'c.py'
        a.write_text(CV_METHOD_A)
        b.write_text(CV_METHOD_A_REWRITTEN)
        c.write_text(CV_METHOD_B)
        h = oryxflow.codehash.file_hash(a)
        assert oryxflow.codehash.file_hash(b) == h    # body rewritten
        assert oryxflow.codehash.file_hash(c) == h    # return value changed
        # a module-level `def code_version` is ordinary code, not a task token
        d = tmp_path / 'd.py'
        d.write_text(CV_METHOD_A + '\ndef code_version(self):\n    return 1\n')
        assert oryxflow.codehash.file_hash(d) != h

    def test_body_edit_is_silent_but_a_new_return_value_reruns(self, env, monkeypatch,
                                                               recwarn):
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'cvmethod_mod', CV_METHOD_A)
        t = mod.TaskMethod()
        hashes_before = oryxflow.codehash.task_hashes(mod.TaskMethod)
        oryxflow.run(t)
        token_a = oryxflow.core._resolve_code_version(t)

        # (a) the METHOD SOURCE changes, what it RETURNS does not
        path.write_text(CV_METHOD_A_REWRITTEN)
        _bump_mtime(path)
        mod2, _ = _make_module(env, 'cvmethod_mod', CV_METHOD_A_REWRITTEN)
        t2 = mod2.TaskMethod()
        assert t2.task_id == t.task_id
        assert oryxflow.codehash.task_hashes(mod2.TaskMethod) == hashes_before
        assert oryxflow.core._resolve_code_version(t2) == token_a

        r = oryxflow.run(t2)
        assert r.ran == [] and r.warnings == []
        assert not any(isinstance(w.message, oryxflow.StalenessWarning)
                       for w in recwarn.list)

        # (b) what it RETURNS changes -> the token moves, the task reruns
        path.write_text(CV_METHOD_B)
        _bump_mtime(path, 20)
        mod3, _ = _make_module(env, 'cvmethod_mod', CV_METHOD_B)
        t3 = mod3.TaskMethod()
        token_b = oryxflow.core._resolve_code_version(t3)
        assert token_b != token_a
        r2 = oryxflow.run(t3)
        assert r2.did_run(mod3.TaskMethod)
        assert r2.reasons[t3.task_id] == 'code change ({} -> {})'.format(token_a, token_b)

    def test_computed_warning_names_the_method(self, env, monkeypatch):
        # a task pinned by a computed token whose SURROUNDING logic changed still gets the
        # advisory -- with wording that doesn't tell the user to bump anything
        monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', env)
        mod, path = _make_module(env, 'cvwarn_mod', CV_METHOD_A)
        t = mod.TaskMethod()
        oryxflow.run(t)

        path.write_text(CV_METHOD_A.replace('FACTOR = 1', 'FACTOR = 2'))
        _bump_mtime(path)
        with pytest.warns(oryxflow.StalenessWarning, match=r'code_version\(\) still computes'):
            r = oryxflow.run(t)
        assert r.ran == []                                   # advisory, not a rerun
        assert any('fn:' in w for w in r.warnings)
        assert not any('Bump code_version' in w for w in r.warnings)
