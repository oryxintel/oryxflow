import pytest
import oryxflow


oryxflow.settings.log_level = 'WARNING'


class RGetData(oryxflow.tasks.TaskCache):
    n = oryxflow.IntParameter(default=3)
    def run(self):
        self.save(list(range(self.n)))


@oryxflow.requires(RGetData)
class RProcess(oryxflow.tasks.TaskCache):
    def run(self):
        data = self.inputLoad()
        self.save([x * 2 for x in data])


class RBoom(oryxflow.tasks.TaskCache):
    def run(self):
        raise ValueError("kaboom")


@oryxflow.requires(RBoom)
class RDownstream(oryxflow.tasks.TaskCache):
    def run(self):
        self.save(self.inputLoad())


@pytest.fixture
def fresh():
    # clear the in-memory cache so each test sees a clean DAG state
    oryxflow.cache.data.clear()
    yield
    oryxflow.cache.data.clear()


def test_did_run_and_complete_and_reset(fresh):
    flow = oryxflow.Workflow(RProcess)
    r = flow.run()
    assert r.did_run(RProcess) is True
    assert r.did_run(RGetData) is True
    assert any(isinstance(t, RProcess) for t in r.ran)
    assert r.failed == []
    assert bool(r) is True

    # immediate re-run: everything cached
    r2 = flow.run()
    assert r2.did_run(RProcess) is False
    assert r2.did_run(RGetData) is False
    assert any(isinstance(t, RProcess) for t in r2.complete)

    # reset took
    flow.reset(RProcess)
    r3 = flow.run()
    assert r3.did_run(RProcess) is True


def test_ran_of_variants(fresh):
    # two param-variants of the same task
    oryxflow.run([RGetData(n=3), RGetData(n=5)])
    r = oryxflow.run([RGetData(n=3), RGetData(n=5)], forced_all=True)
    got = r.ran_of(RGetData)
    assert len(got) == 2
    assert all(isinstance(t, RGetData) for t in got)
    assert sorted(t.n for t in got) == [3, 5]


def test_failure_context(fresh):
    r = oryxflow.run(RBoom(), abort=False)
    assert bool(r) is False
    assert len(r.failed) == 1
    f = r.failure_of(RBoom)
    assert f is not None
    assert isinstance(f.exception, ValueError)
    assert "kaboom" in str(f.exception)
    assert f.traceback and "kaboom" in f.traceback
    assert r.first_exception is f.exception


def test_first_exception_skips_dependency_failures(fresh):
    # RBoom raises (run error), RDownstream is appended to failed with exception=None
    # (dependency failed) AFTER the run-error. first_exception must be the run-error.
    r = oryxflow.run(RDownstream(), abort=False)
    assert len(r.failed) == 2
    assert r.first_exception is not None
    assert isinstance(r.first_exception, ValueError)


def test_label_formatting(fresh):
    from oryxflow.core import _task_label
    assert _task_label(RGetData(n=3)) == "RGetData(n=3)"
    # no-param task
    assert _task_label(RBoom()) == "RBoom"
    r = oryxflow.run(RGetData(n=3), forced_all=True)
    s = str(r)
    assert "{" not in s and "}" not in s
    assert "RGetData(n=3)" in s


def test_back_compat(fresh):
    r = oryxflow.run(RGetData(n=3))
    assert r.scheduling_succeeded == r.success
    assert isinstance(r.summary(), str)
    assert r.summary() == str(r)
    assert bool(r) is True

    with pytest.raises(RuntimeError) as excinfo:
        oryxflow.run(RBoom(), abort=True)
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_multi_run_result(fresh):
    # WorkflowMulti.run() returns a dict-like that also supports .summary()/.success,
    # so print(result.summary()) works the same as for a single Workflow.
    flow = oryxflow.WorkflowMulti(RGetData, {'a': {'n': 3}, 'b': {'n': 4}})
    result = flow.run()
    assert isinstance(result, dict)              # still a {flow_name: RunResult} dict
    assert isinstance(result['a'], oryxflow.RunResult)
    assert isinstance(result.summary(), str)     # aggregate summary works
    assert "===== a =====" in result.summary()
    assert "===== b =====" in result.summary()
    assert result.success is True
    assert bool(result) is True


def test_complete_cap(fresh):
    # itemized but capped at 10 with "... and K more"
    tasks = [RGetData(n=i) for i in range(13)]
    oryxflow.run(tasks)
    r = oryxflow.run(tasks)   # all complete now
    assert len(r.complete) == 13
    s = str(r)
    assert "... and 3 more" in s
