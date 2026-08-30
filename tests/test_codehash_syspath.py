"""Code hashing must survive a package that only exists on a run-time ``sys.path``.

``task_code_hash`` walks a task's import graph and, at the end, folds in every
star-imported module it found. Resolving one of those star targets discovers a file the
walk had not seen -- and it was being recorded into the very mapping the final pass was
iterating, so the walk blew up with ``RuntimeError: dictionary changed size during
iteration`` instead of returning a hash. A project that inserts its package directory
into ``sys.path`` at run time (a script or notebook layout, rather than an installed
package) is exactly where the star target becomes resolvable, which is why those projects
hit it and turned ``code_version_auto`` off.
"""
import sys

import pytest

import oryxflow
import oryxflow.codehash


TASKS_MOD = '''
import oryxflow
from syspath_helper import compute

class TaskSysPath(oryxflow.tasks.TaskPickle):
    def run(self):
        from syspath_lazy import extra
        self.save({'value': compute() + extra()})
'''

HELPER_MOD = '''
from syspath_star import *

def compute():
    return BASE
'''

STAR_MOD = '''
BASE = 1
'''

LAZY_MOD = '''
def extra():
    return 2
'''


@pytest.fixture
def syspath_pkg(tmp_path, monkeypatch):
    """A package dir outside the repo, put on sys.path at run time and imported."""
    root = tmp_path / 'proj'
    root.mkdir()
    (root / 'syspath_tasks.py').write_text(TASKS_MOD)
    (root / 'syspath_helper.py').write_text(HELPER_MOD)
    (root / 'syspath_star.py').write_text(STAR_MOD)
    (root / 'syspath_lazy.py').write_text(LAZY_MOD)

    monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', root)
    monkeypatch.syspath_prepend(str(root))
    names = ('syspath_tasks', 'syspath_helper', 'syspath_star', 'syspath_lazy')
    for n in names:
        sys.modules.pop(n, None)
    try:
        import syspath_tasks
        yield syspath_tasks
    finally:
        for n in names:
            sys.modules.pop(n, None)


class TestSysPathPackage:

    def test_task_code_hash_survives_runtime_syspath(self, syspath_pkg, monkeypatch):
        monkeypatch.setattr(oryxflow.settings, 'code_version_auto', True)
        task = syspath_pkg.TaskSysPath()
        h = oryxflow.codehash.task_code_hash(task)
        assert h

    def test_star_target_is_hashed(self, syspath_pkg):
        # the discovered star target is in the hash, so editing it still invalidates
        hashes = oryxflow.codehash.task_hashes(syspath_pkg.TaskSysPath)
        assert 'syspath_star.py::*' in hashes

    def test_hash_is_stable_across_calls(self, syspath_pkg):
        cls = syspath_pkg.TaskSysPath
        assert oryxflow.codehash.task_code_hash(cls) == oryxflow.codehash.task_code_hash(cls)
