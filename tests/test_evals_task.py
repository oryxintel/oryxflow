"""``TaskEval``: one arm of an eval matrix, cached as parquet.

Everything here is offline -- the task under test is a stub coroutine returning canned
outputs, so no test makes a network call.

Covers: the documented row schema; a failure rate above the threshold raising and
writing NOTHING (a parquet of exceptions must never become a cache entry); failures
below the threshold surviving as rows with ``error`` set and staying out of the metric;
``preflight`` aborting the sweep after a single call; ``concurrency`` being insignificant
so it cannot fragment the cache; and ``rep`` numbering repeats within a case group.
"""
import pytest

pytest.importorskip('pydantic_evals')

from pydantic import BaseModel                                   # noqa: E402
from pydantic_evals import Case, Dataset                          # noqa: E402
from pydantic_evals.evaluators import Evaluator, EvaluatorContext  # noqa: E402

import oryxflow                                                   # noqa: E402
import oryxflow.state                                             # noqa: E402
from oryxflow.evals.metric import Metric                          # noqa: E402
from oryxflow.evals.task import TaskEval, EvalFailureRateError    # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated data dir per test; state cache reset."""
    datadir = tmp_path / 'data'
    datadir.mkdir()
    monkeypatch.setattr(oryxflow.settings, 'dir', str(datadir))
    monkeypatch.setattr(oryxflow.settings, 'dirpath', datadir)
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    oryxflow.state.clear_cache()
    oryxflow.core._code_warned.clear()
    yield tmp_path


class Turn(BaseModel):
    q: int


class Reply(BaseModel):
    message: str
    wrote: bool


class Wrote(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> bool:
        return bool(ctx.output.wrote)


class Half(Evaluator):
    def evaluate(self, ctx: EvaluatorContext) -> float:
        return 0.5


def make_dataset(n=4, name='ds'):
    cases = [Case(name='c{}'.format(i), inputs=Turn(q=i),
                  metadata={'kind': 'odd' if i % 2 else 'even'})
             for i in range(n)]
    return Dataset(name=name, cases=cases, evaluators=[Wrote(), Half()])


class _Stub:
    """Canned outputs plus a call counter; `boom` names the inputs that raise."""

    def __init__(self, boom=()):
        self.boom = set(boom)
        self.calls = 0

    async def __call__(self, inputs):
        self.calls += 1
        if inputs.q in self.boom:
            raise ValueError('stub blew up on q={}'.format(inputs.q))
        return Reply(message='reply {}'.format(inputs.q), wrote=inputs.q % 2 == 0)


def make_task(stub, dataset=None, family='TaskEvalStub', **attrs):
    """A TaskEval subclass wired to a stub. retry_task off so calls are countable."""
    body = dict(
        dataset=dataset if dataset is not None else make_dataset(),
        retry_task=None,
        preflight=None,
        code_version='test',
    )
    body.update(attrs)

    async def case(self, inputs):
        return await stub(inputs)

    body['case'] = case
    return type(family, (TaskEval,), body)


# ---------------------------------------------------------------- 12. schema

def test_frame_has_documented_schema(env):
    stub = _Stub()
    cls = make_task(stub)
    task = cls()
    task.run()

    df = task.outputLoad(keys='cases')
    assert len(df) == 4
    assert stub.calls == 4

    for col in ['case_name', 'source_case_name', 'rep', 'kind',
                'Wrote', 'Half', 'task_duration_s', 'error', 'message', 'wrote']:
        assert col in df.columns, col

    # structural columns lead, in schema order
    assert list(df.columns)[:3] == ['case_name', 'source_case_name', 'rep']

    assert sorted(df['case_name']) == ['c0', 'c1', 'c2', 'c3']
    assert list(df['source_case_name']) == list(df['case_name'])
    assert set(df['rep']) == {0}
    assert set(df['kind']) == {'even', 'odd'}
    assert list(df['error']) == ['', '', '', '']
    assert set(df['Half']) == {0.5}
    assert (df['task_duration_s'] >= 0).all()

    # output fields are flattened from the pydantic model, values intact
    by_case = df.set_index('case_name')
    assert by_case.loc['c2', 'message'] == 'reply 2'
    assert bool(by_case.loc['c2', 'wrote']) is True
    assert bool(by_case.loc['c1', 'wrote']) is False
    # the assertion column mirrors the output field it scores
    assert list(df['Wrote'].astype(bool)) == list(df['wrote'].astype(bool))


def test_frame_survives_parquet_roundtrip(env):
    """The saved cache entry, reread, is the same frame -- dtypes included."""
    stub = _Stub()
    task = make_task(stub, family='TaskEvalRoundtrip')()
    task.run()
    df = task.outputLoad(keys='cases')
    # bool-valued assertion columns survive parquet as a boolean dtype, not object
    assert df['Wrote'].dtype.name in ('bool', 'boolean')
    assert list(df['Wrote']) == [True, False, True, False]
    assert task.complete()


# ------------------------------------------------- 13. above the failure rate

def test_failure_rate_above_threshold_raises_and_writes_nothing(env):
    stub = _Stub(boom=[0, 1, 2, 3])
    task = make_task(stub, family='TaskEvalAllFail')()

    with pytest.raises(EvalFailureRateError) as excinfo:
        task.run()

    assert '100%' in str(excinfo.value)
    assert 'stub blew up' in str(excinfo.value)
    # nothing cached: the next reader could not tell exceptions from a measurement
    assert not task.output()['cases'].exists()
    assert not task.complete()


# ------------------------------------------------- 14. below the failure rate

def test_failures_below_threshold_are_rows_excluded_from_the_metric(env):
    # 1 of 4 fails = 25%, under a max_failure_rate of 0.5
    stub = _Stub(boom=[1])
    task = make_task(stub, family='TaskEvalOneFail', max_failure_rate=0.5)()
    task.run()

    df = task.outputLoad(keys='cases')
    assert len(df) == 4
    failed = df[df['error'] != '']
    assert len(failed) == 1
    assert failed.iloc[0]['case_name'] == 'c1'
    assert 'stub blew up on q=1' in failed.iloc[0]['error']
    # the failure keeps its metadata, so a per-slice failure count is possible
    assert failed.iloc[0]['kind'] == 'odd'

    metric = Metric('wrote', 'Wrote')
    value, n, numerator = metric.compute(df)
    assert n == 3                     # the failure is not evidence either way
    assert numerator == 2             # c0 and c2 wrote; c3 did not
    assert value == pytest.approx(2 / 3)

    # and it is excluded from a sliced metric too
    odd = df[df['kind'] == 'odd']
    assert metric.compute(odd)[1] == 1


# ------------------------------------------------------------- 15. preflight

def test_preflight_failure_aborts_before_the_sweep(env):
    """A credential or wiring failure costs ONE call, not the whole matrix."""
    stub = _Stub(boom=[0])
    cls = make_task(stub, family='TaskEvalPreflight')
    del cls.preflight                 # restore the default preflight

    task = cls()
    with pytest.raises(ValueError, match='stub blew up on q=0'):
        task.run()

    assert stub.calls == 1
    assert not task.output()['cases'].exists()


def test_preflight_runs_one_case_then_the_sweep(env):
    stub = _Stub()
    cls = make_task(stub, family='TaskEvalPreflightOk')
    del cls.preflight

    task = cls()
    task.run()
    assert stub.calls == 5            # 1 preflight + 4 cases
    assert len(task.outputLoad(keys='cases')) == 4


def test_preflight_none_disables_it(env):
    stub = _Stub()
    task = make_task(stub, family='TaskEvalNoPreflight')()
    assert task.preflight is None
    task.run()
    assert stub.calls == 4


# ---------------------------------------------------- 16. insignificant param

def test_concurrency_does_not_fragment_the_cache(env):
    """`concurrency` changes how fast a sweep runs, never what it returns."""
    stub = _Stub()
    cls = make_task(stub, family='TaskEvalConcurrency')
    assert cls(concurrency=1).task_id == cls(concurrency=8).task_id
    # ...while a significant parameter still does
    assert cls(repeats=1).task_id != cls(repeats=2).task_id


def test_concurrency_reuses_the_cached_output(env):
    stub = _Stub()
    cls = make_task(stub, family='TaskEvalConcurrencyCache')
    cls(concurrency=2).run()
    assert stub.calls == 4
    assert cls(concurrency=16).complete()


# ------------------------------------------------------------------- repeats

def test_repeats_number_the_reps_within_each_case(env):
    stub = _Stub()
    task = make_task(stub, family='TaskEvalRepeats')(repeats=2)
    task.run()

    df = task.outputLoad(keys='cases')
    assert len(df) == 8
    assert stub.calls == 8
    for name, group in df.groupby('source_case_name'):
        assert sorted(group['rep']) == [0, 1], name
    assert sorted(df['source_case_name'].unique()) == ['c0', 'c1', 'c2', 'c3']


def test_repeats_number_failures_too(env):
    """A repeated case that always raises still gets rep 0 and rep 1."""
    stub = _Stub(boom=[1])
    task = make_task(stub, family='TaskEvalRepeatsFail',
                     max_failure_rate=0.5)(repeats=2)
    task.run()

    df = task.outputLoad(keys='cases')
    assert len(df) == 8
    failed = df[df['error'] != '']
    assert list(failed['source_case_name']) == ['c1', 'c1']
    assert sorted(failed['rep']) == [0, 1]


# ------------- fields the report carries that the frame used to throw away

def test_recorded_metrics_and_attributes_become_columns(env):
    """`increment_eval_metric` / `set_eval_attribute` are how a task reports what a
    call actually cost. Dropping them is why a sweep could only ever ESTIMATE cost."""
    from pydantic_evals import Case, Dataset, increment_eval_metric, set_eval_attribute

    class Out(BaseModel):
        ok: bool

    class T(TaskEval):
        dataset = Dataset(name='d', cases=[Case(name='c0', inputs=Turn(q=1))])
        metric = None
        code_version = 'v1'
        retry_task = None

        async def case(self, inputs):
            increment_eval_metric('input_tokens', 120)
            set_eval_attribute('model_snapshot', 'flash-002')
            return Out(ok=True)

    df = T()._to_frame(T()._evaluate())
    assert df['input_tokens'].tolist() == [120]
    assert df['model_snapshot'].tolist() == ['flash-002']


def test_expected_output_is_on_the_frame(env):
    """Without it every comparison against the answer key -- a confusion matrix, a
    per-label breakdown -- has to join back by case name."""
    from pydantic_evals import Case, Dataset

    class Out(BaseModel):
        label: str

    class T(TaskEval):
        dataset = Dataset(name='d', cases=[
            Case(name='c0', inputs=Turn(q=1), expected_output='odd'),
            Case(name='c1', inputs=Turn(q=2), expected_output='even')])
        metric = None
        code_version = 'v1'
        retry_task = None

        async def case(self, inputs):
            return Out(label='odd' if inputs.q % 2 else 'even')

    df = T()._to_frame(T()._evaluate())
    assert sorted(df['expected'].tolist()) == ['even', 'odd']
    assert sorted(df['label'].tolist()) == ['even', 'odd']


def test_total_duration_is_kept_alongside_task_duration(env):
    from pydantic_evals import Case, Dataset

    class Out(BaseModel):
        ok: bool

    class T(TaskEval):
        dataset = Dataset(name='d', cases=[Case(name='c0', inputs=Turn(q=1))])
        metric = None
        code_version = 'v1'
        retry_task = None

        async def case(self, inputs):
            return Out(ok=True)

    df = T()._to_frame(T()._evaluate())
    assert 'total_duration_s' in df.columns and 'task_duration_s' in df.columns
