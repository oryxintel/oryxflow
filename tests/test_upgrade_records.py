"""Code-status records written by an older oryxflow are compared exactly, not blessed; class
metadata (`export = True`) is not a code change; a new code_version() method reruns."""
import sys
import importlib.util

import pytest

import oryxflow
import oryxflow.state
import oryxflow.codehash
import oryxflow.events


@pytest.fixture
def env(tmp_path, monkeypatch):
    datadir = tmp_path / 'data'
    datadir.mkdir()
    monkeypatch.setattr(oryxflow.settings, 'dir', str(datadir))
    monkeypatch.setattr(oryxflow.settings, 'dirpath', datadir)
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    monkeypatch.setattr(oryxflow.codehash, 'PROJECT_ROOT', tmp_path)
    oryxflow.state.clear_cache()
    oryxflow.core._code_warned.clear()
    yield tmp_path


def _load(tmp_path, name, body):
    path = tmp_path / (name + '.py')
    path.write_text(body)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod, path


SRC = '''
import oryxflow

FACTOR = 1

class Up(oryxflow.tasks.TaskPickle):
    export = True
    def run(self):
        self.save({'v': FACTOR})

@oryxflow.requires(Up)
class Down(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save(self.inputLoad())
'''


def _as_old_record(task, version):
    """Rewrite the task's record the way oryxflow `version` wrote it."""
    dirpath = oryxflow.settings.dirpath
    rec = dict(oryxflow.state.get_record(dirpath, task.task_id))
    with oryxflow.codehash.rules_of(version):
        rec['source_hashes'] = oryxflow.codehash.task_hashes(task)
    rec['v'] = version
    if version == 4:
        rec['py'] = oryxflow.codehash.LEGACY_PY_TAG
    oryxflow.state.put_record(dirpath, task.task_id, rec)
    return rec


def _reload(tmp_path, name, body):
    sys.modules.pop(name, None)
    oryxflow.codehash._class_source_cache.clear()
    return _load(tmp_path, name, body)


class TestOldRecords:

    @pytest.mark.parametrize('version', [4, 5])
    def test_unchanged_code_is_kept_and_restamped(self, env, version):
        mod, _ = _load(env, 'oldrec_same_{}'.format(version), SRC)
        oryxflow.run(mod.Down())
        old = _as_old_record(mod.Up(), version)
        assert old['source_hashes'] != oryxflow.codehash.task_hashes(mod.Up())  # export line

        r = oryxflow.run(mod.Down())
        assert r.ran == [] and r.warnings == []
        rec = oryxflow.state.get_record(oryxflow.settings.dirpath, mod.Up().task_id)
        assert rec['v'] == oryxflow.state.RECORD_V and 'py' not in rec
        assert rec['output_id'] == old['output_id']
        assert rec['source_hashes'] == oryxflow.codehash.task_hashes(mod.Up())

    @pytest.mark.parametrize('version', [4, 5])
    def test_edit_after_upgrade_reruns(self, env, version):
        # the old cutover trusted any old record on its first build: an edit made after
        # installing the new version, to a task the first build didn't touch, never reran
        name = 'oldrec_edit_{}'.format(version)
        mod, _ = _load(env, name, SRC)
        oryxflow.run(mod.Down())
        _as_old_record(mod.Up(), version)

        mod, _ = _reload(env, name, SRC.replace('FACTOR = 1', 'FACTOR = 2'))
        r = oryxflow.run(mod.Down())
        assert r.did_run(mod.Up) and r.did_run(mod.Down)
        assert mod.Down().outputLoad() == {'v': 2}
        assert 'code change' in r.reasons[mod.Up().task_id]

    def test_uncomparable_record_is_kept_with_a_warning(self, env):
        mod, _ = _load(env, 'oldrec_py', SRC)
        oryxflow.run(mod.Down())
        rec = _as_old_record(mod.Up(), 4)
        rec['py'] = '2.7'       # another interpreter: its ast.dump can't be reproduced
        oryxflow.state.put_record(oryxflow.settings.dirpath, mod.Up().task_id, rec)

        with pytest.warns(oryxflow.StalenessWarning, match='older oryxflow'):
            r = oryxflow.run(mod.Down())
        assert r.ran == []
        assert len(r.warnings) == 1
        assert mod.Up().task_id in r.warnings[0] and 'flow.reset' in r.warnings[0]
        pending = oryxflow.events.warnings()
        assert [w['task_id'] for w in pending] == [mod.Up().task_id]
        assert pending[0]['kind'] == 'old_record'
        # said once: the record is current from here on
        assert oryxflow.run(mod.Down()).warnings == []


class TestMetadataIsNotCode:

    def test_adding_export_does_not_rerun(self, env):
        body = SRC.replace('    export = True\n', '')
        mod, _ = _load(env, 'meta_export', body)
        oryxflow.run(mod.Down())
        mod, _ = _reload(env, 'meta_export', SRC)
        r = oryxflow.run(mod.Down(), export=False)
        assert r.ran == [] and r.warnings == []

    def test_metadata_attrs_left_out_of_the_hash(self, env):
        a, _ = _load(env, 'meta_a', SRC.replace('    export = True\n', ''))
        b, _ = _load(env, 'meta_b', SRC.replace(
            '    export = True\n',
            "    export = True\n    export_source = 'x.py'\n    built = [{'a': 1}]\n"))
        ha = oryxflow.codehash.task_hashes(a.Up())
        hb = oryxflow.codehash.task_hashes(b.Up())
        assert list(ha.values()) == list(hb.values())


COMPUTED = '''
import oryxflow

class T(oryxflow.tasks.TaskPickle):
    def run(self):
        self.save({'v': 1})
'''


class TestComputedCodeVersion:

    def test_switching_to_a_code_version_method_reruns(self, env):
        # its value may fold a data file the cached output was never compared with
        mod, _ = _load(env, 'cv_switch', COMPUTED)
        oryxflow.run(mod.T())
        mod, _ = _reload(env, 'cv_switch', COMPUTED.replace(
            '    def run', "    def code_version(self):\n        return {'data': 'abc'}\n"
                           '    def run'))
        r = oryxflow.run(mod.T())
        assert r.did_run(mod.T)
        assert r.reasons[mod.T().task_id].startswith('code change (auto -> fn:')

    def test_adding_a_pin_stays_free(self, env):
        mod, _ = _load(env, 'cv_pin', COMPUTED)
        oryxflow.run(mod.T())
        mod, _ = _reload(env, 'cv_pin', COMPUTED.replace(
            '    def run', "    code_version = '1'\n    def run"))
        assert oryxflow.run(mod.T()).ran == []


def test_auto_off_warning_names_the_setting(env, monkeypatch):
    mod, _ = _load(env, 'auto_off', COMPUTED.replace(
        'class T(', "class Pin(oryxflow.tasks.TaskPickle):\n    code_version = '1'\n"
                    "    def run(self):\n        self.save(1)\n\n"
                    "@oryxflow.requires(Pin)\nclass T("))
    oryxflow.run(mod.T())
    monkeypatch.setattr(oryxflow.settings, 'code_version_auto', False)
    mod, _ = _reload(env, 'auto_off', COMPUTED.replace(
        'class T(', "class Pin(oryxflow.tasks.TaskPickle):\n    code_version = '1'\n"
                    "    def run(self):\n        self.save(1)\n\n"
                    "@oryxflow.requires(Pin)\nclass T(").replace("{'v': 1}", "{'v': 2}"))
    with pytest.warns(oryxflow.StalenessWarning) as w:
        r = oryxflow.run(mod.T())
    assert r.ran == []
    msg = [str(x.message) for x in w if 'task T:' in str(x.message)][0]
    assert 'code_version_auto = False' in msg and 'expensive' not in msg
