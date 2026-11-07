"""A task module edited in the same second as its last compile, at the same size (``FACTOR = 1`` -> ``2``), passes
CPython's bytecode check (source mtime in whole seconds + size), so the old code runs. The engine sees the new source,
so it reruns the task, and used to stamp the record with the NEW hashes over the OLD code's output: a silent stale
build that no later run redid. Now the run says so, removes the stale bytecode, and the next run redoes the task."""
import importlib.util
import os
import py_compile
import sys

import pytest

import oryxflow
import oryxflow.codehash
import oryxflow.state

SRC = '''
import oryxflow

FACTOR = 1

class Up(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'v': FACTOR})

@oryxflow.requires(Up)
class Down(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save(self.inputLoad())
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    datadir = tmp_path / 'data'
    datadir.mkdir()
    monkeypatch.setattr(oryxflow.settings, 'dir', str(datadir))
    monkeypatch.setattr(oryxflow.settings, 'dirpath', datadir)
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(sys, 'dont_write_bytecode', False)
    oryxflow.state.clear_cache()
    oryxflow.core._code_warned.clear()
    yield tmp_path


def _import(path, name):
    """What a fresh process does: import through the bytecode cache (writing it when missing or invalid)."""
    sys.modules.pop(name, None)
    oryxflow.codehash._class_source_cache.clear()
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _same_second_edit(path, new_text):
    """Rewrite ``path`` at the same size, keeping its mtime in the same whole second but later than the cache."""
    st = os.stat(path)
    pyc = importlib.util.cache_from_source(str(path))
    base = (st.st_mtime_ns // 10**9) * 10**9
    os.utime(path, ns=(base, base + 100_000_000))              # source: .1 s into the second
    os.utime(pyc, ns=(base, base + 200_000_000))               # the cache: written at .2 s, from that source
    path.write_text(new_text)
    os.utime(path, ns=(base, base + 700_000_000))              # the edit: .7 s, same second, same size
    assert os.path.getsize(path) == st.st_size


def test_a_same_second_same_size_edit_is_not_stamped_as_built_by_the_new_code(env):
    path = env / 'stale_bc_tasks.py'
    path.write_text(SRC)
    mod = _import(path, 'stale_bc_tasks')
    oryxflow.run(mod.Down())
    assert mod.Down().outputLoad() == {'v': 1}
    _same_second_edit(path, SRC.replace('FACTOR = 1', 'FACTOR = 2'))

    mod = _import(path, 'stale_bc_tasks')                      # the next process: the stale cache passes the check
    assert mod.FACTOR == 1                                      # CPython ran the old code (the root cause)
    with pytest.warns(UserWarning, match='stale_bc_tasks.py changed in the same second'):
        oryxflow.run(mod.Down())

    mod = _import(path, 'stale_bc_tasks')                      # the run after: fresh code, the task is redone
    assert mod.FACTOR == 2
    r = oryxflow.run(mod.Down())
    assert r.did_run(mod.Up) and mod.Down().outputLoad() == {'v': 2}


def _compile(path, mode=py_compile.PycInvalidationMode.TIMESTAMP):
    py_compile.compile(str(path), cfile=importlib.util.cache_from_source(str(path)), doraise=True,
                       invalidation_mode=mode)


def test_a_fresh_or_missing_cache_is_not_stale(tmp_path):
    path = tmp_path / 'fresh_mod.py'
    path.write_text('FACTOR = 1\n')
    _compile(path)
    assert not oryxflow.codehash.stale_bytecode(path)
    assert not oryxflow.codehash.stale_bytecode(tmp_path / 'no_cache.py')


def test_hash_checked_bytecode_is_never_reported(tmp_path):
    path = tmp_path / 'hash_mod.py'
    path.write_text('FACTOR = 1\n')
    _compile(path, py_compile.PycInvalidationMode.CHECKED_HASH)
    _same_second_edit(path, 'FACTOR = 2\n')
    assert not oryxflow.codehash.stale_bytecode(path)


def test_a_source_mtime_truncated_to_the_second_is_not_stale(tmp_path):
    """A file sync or copy that keeps whole-second mtimes moves the source before its cache, never after it."""
    path = tmp_path / 'synced_mod.py'
    path.write_text('FACTOR = 1\n')
    base = (os.stat(path).st_mtime_ns // 10**9) * 10**9
    os.utime(path, ns=(base, base + 300_000_000))
    _compile(path)
    os.utime(importlib.util.cache_from_source(str(path)), ns=(base, base + 500_000_000))
    os.utime(path, ns=(base, base))                            # the sync: .3 s -> .000000000
    assert not oryxflow.codehash.stale_bytecode(path)
    _same_second_edit(path, 'FACTOR = 2\n')                     # the real case is still caught
    assert oryxflow.codehash.stale_bytecode(path)
