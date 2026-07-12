import json
import datetime

import pytest

import oryxflow
import oryxflow.state
from oryxflow import events
from oryxflow.parameter import Parameter


@pytest.fixture
def env(tmp_path, monkeypatch):
    datadir = tmp_path / 'data'
    datadir.mkdir()
    monkeypatch.setattr(oryxflow.settings, 'dir', str(datadir))
    monkeypatch.setattr(oryxflow.settings, 'dirpath', datadir)
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    oryxflow.state.clear_cache()
    yield tmp_path


def _events(types=None):
    events.flush()
    return list(events.iter_events(types=types))


class TestEvents:

    def test_build_emits_with_shared_run_id(self, env):
        class T1(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({'a': 1})

        class T2(oryxflow.tasks.TaskPickle):
            def requires(self):
                return T1()
            def run(self):
                self.save(self.inputLoad())

        result = oryxflow.run(T2())
        evs = _events()
        assert [e['type'] for e in evs] == \
            ['run_started', 'task_ran', 'task_ran', 'run_finished']
        assert result.run_id is not None
        assert {e['run_id'] for e in evs} == {result.run_id}
        assert len({e['id'] for e in evs}) == len(evs)          # unique ids
        for e in evs:
            datetime.datetime.fromisoformat(e['ts'])            # valid UTC iso ts
            assert e['project_id'] is not None
        finished = evs[-1]
        assert finished['counts'] == {'ran': 2, 'complete': 0, 'failed': 0}
        assert finished['success'] is True

    def test_reasons(self, env):
        class A(oryxflow.tasks.TaskPickle):
            code_version = '1'
            def run(self):
                self.save({'a': 1})

        class B(oryxflow.tasks.TaskPickle):
            def requires(self):
                return A()
            def run(self):
                self.save(self.inputLoad())

        r1 = oryxflow.run(B())
        assert r1.reasons[A().task_id] == 'output missing'
        assert r1.reasons[B().task_id] == 'output missing'

        A.code_version = '2'
        r2 = oryxflow.run(B())
        assert r2.reasons[A().task_id] == 'code change (1 -> 2)'
        assert r2.reasons[B().task_id] == 'upstream rerun'
        # the events tell the same story
        by_task = {e['task_id']: e for e in _events(types=('task_ran',))
                   if e['run_id'] == r2.run_id}
        assert by_task[A().task_id]['reason'] == 'code change (1 -> 2)'
        assert by_task[B().task_id]['reason'] == 'upstream rerun'

    def test_failure_event(self, env):
        class Boom(oryxflow.tasks.TaskPickle):
            def run(self):
                raise ValueError('kaput')

        result = oryxflow.run(Boom(), abort=False)
        assert not result.success
        evs = _events(types=('task_failed',))
        assert len(evs) == 1
        assert 'ValueError' in evs[0]['error'] and 'kaput' in evs[0]['error']
        assert 'kaput' in evs[0]['traceback']
        assert len(evs[0]['traceback']) <= 4096                 # tail-bounded
        assert _events(types=('run_finished',))[-1]['success'] is False

    def test_workflowmulti_flows(self, env):
        class TP(oryxflow.tasks.TaskPickle):
            param = Parameter()
            def run(self):
                self.save({'p': self.param})

        flow = oryxflow.WorkflowMulti(TP, {'a': {'param': 'x'}, 'b': {'param': 'y'}})
        result = flow.run()

        finished = _events(types=('run_finished',))
        assert sorted(e['flow'] for e in finished) == ['a', 'b']
        assert len({e['run_id'] for e in finished}) == 2        # one run_id per flow

        assert len(events.runs(flow='a')) == 1
        assert events.runs(flow='a')[0]['params'] == {'param': 'x'}

        # MultiRunResult aggregates match the per-flow run_finished counts
        assert result.success
        assert len(result.ran) == sum(e['counts']['ran'] for e in finished) == 2
        assert result.failed == [] and result.complete == []
        assert set(result.reasons.values()) == {'output missing'}
        assert result.warnings == []

    def test_head_offload(self, env):
        d = env / '.oryxflow'
        d.mkdir()
        head = d / 'events.jsonl'
        now = datetime.datetime.now(datetime.timezone.utc)
        last_month = (now.replace(day=1) - datetime.timedelta(days=1))
        old_line = json.dumps({'id': 'seed1', 'ts': last_month.isoformat(),
                               'type': 'task_ran', 'v': 1})
        head.write_text(old_line + '\n', encoding='utf-8')

        events.append('run_started', {'tasks': []}, run_id='r1')
        events.flush()

        offloaded = d / 'events-{}.jsonl'.format(last_month.strftime('%Y%m'))
        assert offloaded.exists()
        assert offloaded.read_text(encoding='utf-8') == old_line + '\n'  # rename only
        head_events = [json.loads(l) for l in
                       head.read_text(encoding='utf-8').splitlines()]
        assert [e['type'] for e in head_events] == ['run_started']
        # history spans both files
        assert [e['id'] for e in events.iter_events()][0] == 'seed1'

    def test_status(self, env):
        class Ok(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({'a': 1})

        class Bad(oryxflow.tasks.TaskPickle):
            def run(self):
                raise RuntimeError('nope')

        oryxflow.run(Ok())
        oryxflow.run(Bad(), abort=False)
        s = events.status()
        assert set(s) == {'pending_warnings', 'last_runs', 'recent_failures'}
        assert s['last_runs']['Ok']['type'] == 'task_ran'
        assert s['last_runs']['Bad']['type'] == 'task_failed'
        assert len(s['recent_failures']) == 1
        assert s['pending_warnings'] == []

    def test_print_status(self, env, capsys):
        assert events.print_status() is None                    # empty stream case
        assert 'no events recorded yet' in capsys.readouterr().out

        class Ok(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({'a': 1})

        class Bad(oryxflow.tasks.TaskPickle):
            def run(self):
                raise RuntimeError('nope')

        oryxflow.run(Ok())
        oryxflow.run(Bad(), abort=False)
        events.print_status()
        out = capsys.readouterr().out
        assert 'pending code warnings: 0' in out
        assert 'last run per family:' in out
        assert 'Ok' in out and 'Bad' in out and 'FAILED' in out
        assert 'recent failures: 1' in out and 'nope' in out

    def test_task_log_capture(self, env):
        class T(oryxflow.tasks.TaskPickle):
            def run(self):
                self.logger.info("corr_avg={}", 0.11)
                self.save({'a': 1})

        t = T()
        oryxflow.run(t)    # logging disabled (library default) -- capture works anyway
        logs = _events(types=('task_log',))
        assert len(logs) == 1
        assert logs[0]['message'] == 'corr_avg=0.11'
        assert logs[0]['level'] == 'INFO'
        assert logs[0]['task_id'] == t.task_id

    def test_event_write_failure_does_not_fail_build(self, env, monkeypatch):
        # point eventspath at a *file* so the event dir can't be created
        blocker = env / 'blocked'
        blocker.write_text('not a directory')
        monkeypatch.setattr(oryxflow.settings, 'eventspath', blocker)

        class T(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({'a': 1})

        result = oryxflow.run(T())
        assert result.success and result.did_run(T)

    def test_events_disabled_is_noop(self, env, monkeypatch):
        monkeypatch.setattr(oryxflow.settings, 'events', False, raising=False)

        class T(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({'a': 1})

        result = oryxflow.run(T())
        assert result.success
        assert not (env / '.oryxflow').exists()

    def test_raw_stream_readable(self, env):
        class T(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({'a': 1})

        oryxflow.run(T())
        events.flush()
        head = env / '.oryxflow' / 'events.jsonl'
        with open(head, encoding='utf-8') as fh:                # the tail/jq contract
            rows = [json.loads(line) for line in fh]
        assert {'run_started', 'task_ran', 'run_finished'} <= {r['type'] for r in rows}
