"""Fixes from real-project usage: status-file writes on Windows, interpreter-stable code hashes."""
import ast
import importlib.util
import os
import sys

import pytest

import oryxflow
import oryxflow.codehash
import oryxflow.state


@pytest.fixture
def env(tmp_path, monkeypatch):
    datadir = tmp_path / 'data'
    datadir.mkdir()
    monkeypatch.setattr(oryxflow.settings, 'dir', str(datadir))
    monkeypatch.setattr(oryxflow.settings, 'dirpath', datadir)
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(oryxflow.state, '_REPLACE_DELAYS', (0,) * 8)
    oryxflow.state.clear_cache()
    oryxflow.core._code_warned.clear()
    yield tmp_path


def _make_module(tmp_path, name, body):
    path = tmp_path / (name + '.py')
    path.write_text(body)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


TASK_V1 = '''
import oryxflow

FACTOR = 1

class TaskFix(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'value': FACTOR})
'''

TASK_V2 = TASK_V1.replace('FACTOR = 1', 'FACTOR = 2')


def _fresh_import(tmp_path, name, body):
    oryxflow.codehash._hash_cache.clear()
    oryxflow.codehash._module_cache.clear()
    oryxflow.codehash._task_hash_cache.clear()
    oryxflow.codehash._symindex_cache.clear()
    return _make_module(tmp_path, name, body)


class TestStatusFileWrite:

    def test_replace_retries_on_permission_error(self, env, monkeypatch):
        real, calls = os.replace, []

        def flaky(src, dst):
            calls.append(dst)
            if len(calls) <= 2:
                raise PermissionError(5, 'Access is denied')
            return real(src, dst)
        monkeypatch.setattr(oryxflow.state.os, 'replace', flaky)
        oryxflow.state.put_record(oryxflow.settings.dirpath, 'T_1', {'v': 1})
        oryxflow.state.clear_cache()
        assert oryxflow.state.get_record(oryxflow.settings.dirpath, 'T_1') == {'v': 1}
        assert len(calls) == 3

    def test_gives_up_and_removes_tmp(self, env, monkeypatch):
        def locked(src, dst):
            raise PermissionError(5, 'Access is denied')
        monkeypatch.setattr(oryxflow.state.os, 'replace', locked)
        with pytest.raises(PermissionError):
            oryxflow.state.put_record(oryxflow.settings.dirpath, 'T_1', {'v': 1})
        assert list(oryxflow.settings.dirpath.glob('*.tmp')) == []

    def test_other_errors_are_not_retried(self, env, monkeypatch):
        calls = []

        def broken(src, dst):
            calls.append(dst)
            raise OSError(28, 'No space left on device')
        monkeypatch.setattr(oryxflow.state.os, 'replace', broken)
        with pytest.raises(OSError):
            oryxflow.state.put_record(oryxflow.settings.dirpath, 'T_1', {'v': 1})
        assert len(calls) == 1

    def test_advisory_write_failure_does_not_fail_the_build(self, env, monkeypatch):
        mod = _fresh_import(env, 'fixmod_adv', TASK_V1)
        t = mod.TaskFix()
        oryxflow.run(t)
        # the next build's advisory sweep re-stamps a missing record -- make that write fail
        os.remove(oryxflow.state._store_path(oryxflow.settings.dirpath))
        oryxflow.state.clear_cache()

        def locked(*a, **k):
            raise PermissionError(5, 'Access is denied')
        monkeypatch.setattr(oryxflow.state, 'put_record', locked)
        with pytest.warns(UserWarning, match='code-status record'):
            r = oryxflow.run(t)
        assert r.success and r.ran == []

    def test_post_run_write_failure_warns(self, env, monkeypatch):
        mod = _fresh_import(env, 'fixmod_post', TASK_V1)

        def locked(*a, **k):
            raise PermissionError(5, 'Access is denied')
        monkeypatch.setattr(oryxflow.state, 'put_record', locked)
        with pytest.warns(UserWarning, match='code-status record'):
            r = oryxflow.run(mod.TaskFix())
        assert r.success


class TestInterpreterStableHashes:

    def test_dump_ignores_empty_fields(self):
        node = ast.parse('def f(x):\n    return x\n').body[0]
        node.type_params = []
        with_empty = oryxflow.codehash._dump(node)
        del node.type_params
        assert oryxflow.codehash._dump(node) == with_empty

    def test_dump_still_sees_real_changes(self):
        a = ast.parse('def f(x):\n    return x\n')
        b = ast.parse('def f(x):\n    return x + 1\n')
        assert oryxflow.codehash._dump(a) != oryxflow.codehash._dump(b)

    def test_record_format_is_5_without_interpreter_tag(self, env):
        mod = _fresh_import(env, 'fixmod_v5', TASK_V1)
        t = mod.TaskFix()
        oryxflow.run(t)
        rec = oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id)
        assert oryxflow.state.RECORD_V == 5 and rec['v'] == 5
        assert 'py' not in rec
        assert not hasattr(oryxflow.codehash, 'PY_TAG')

    def test_record_from_another_interpreter_is_compared(self, env):
        mod = _fresh_import(env, 'fixmod_py', TASK_V1)
        t = mod.TaskFix()
        oryxflow.run(t)
        rec = dict(oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id))
        rec['py'] = '3.11'                     # stamped by another Python version
        oryxflow.state.put_record(oryxflow.settings.dirpath, t.task_id, rec)
        assert oryxflow.run(t).ran == []       # unchanged code: still cached

        mod2 = _fresh_import(env, 'fixmod_py', TASK_V2)
        assert [x.task_family for x in oryxflow.run(mod2.TaskFix()).ran] == ['TaskFix']

    def test_old_record_format_is_restamped_once(self, env):
        mod = _fresh_import(env, 'fixmod_old', TASK_V1)
        t = mod.TaskFix()
        oryxflow.run(t)
        rec = dict(oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id))
        rec['v'] = 4
        rec['source_hashes'] = {'old-formula': 'x'}
        oryxflow.state.put_record(oryxflow.settings.dirpath, t.task_id, rec)
        assert oryxflow.run(t).ran == []
        assert oryxflow.state.get_record(oryxflow.settings.dirpath, t.task_id)['v'] == 5


class TestHashFiles:

    def _files(self, root):
        (root / 'q').mkdir()
        (root / 'q' / 'a.sql').write_bytes(b'select 1\n')
        (root / 'q' / 'b.sql').write_bytes(b'select 22\n')

    def test_digest_unchanged_by_the_rewrite(self, tmp_path):
        self._files(tmp_path)
        assert oryxflow.hash_files('q/*.sql', root=str(tmp_path)) == '1e9b1604649cbf759b6a2407e6fcdca6'

    def test_unchanged_files_are_not_reread(self, tmp_path, monkeypatch):
        self._files(tmp_path)
        first = oryxflow.hash_files('q/*.sql', root=str(tmp_path))
        reads = []
        real = oryxflow.utils._file_chunks
        monkeypatch.setattr(oryxflow.utils, '_file_chunks', lambda f: reads.append(f) or real(f))
        assert oryxflow.hash_files('q/*.sql', root=str(tmp_path)) == first
        assert reads == []

    def test_same_bytes_new_mtime_same_digest(self, tmp_path):
        self._files(tmp_path)
        first = oryxflow.hash_files('q/*.sql', root=str(tmp_path))
        f = tmp_path / 'q' / 'a.sql'
        f.write_bytes(b'select 1\n')
        st = os.stat(f)
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10**9))
        assert oryxflow.hash_files('q/*.sql', root=str(tmp_path)) == first

    def test_edit_changes_digest(self, tmp_path):
        self._files(tmp_path)
        first = oryxflow.hash_files('q/*.sql', root=str(tmp_path))
        f = tmp_path / 'q' / 'a.sql'
        f.write_bytes(b'select 2\n')                  # same size
        st = os.stat(f)
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10**9))
        assert oryxflow.hash_files('q/*.sql', root=str(tmp_path)) != first
