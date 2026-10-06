"""The two-stage cell: model calls cached apart from scoring.

A cell is ``<Eval>Outputs`` (the model calls, stored as JSON) feeding ``<Eval>`` (the
evaluators, stored as the per-case frame). Every claim here is checked by counting stub
invocations through ``sweep()`` -- the engine path a real run takes:

- editing a scorer's configuration re-scores with ZERO model calls;
- changing what the arm reads (``code_version``) re-runs both stages;
- editing the case set re-runs the class form (it used to serve stale cells);
- a typed output survives the JSON round trip, so ``ctx.output.attr`` scorers work;
- repeats and failures keep their ``rep`` / ``error`` rows through re-scoring;
- a span-reading evaluator is routed to the outputs stage.
"""
import dataclasses

import pytest

pytest.importorskip('pydantic_evals')

from pydantic import BaseModel                                    # noqa: E402
from pydantic_evals import Case, Dataset                           # noqa: E402
from pydantic_evals.evaluators import Evaluator, EvaluatorContext  # noqa: E402

import oryxflow                                                    # noqa: E402
import oryxflow.state                                              # noqa: E402
from oryxflow.evals.metric import Metric                           # noqa: E402
from oryxflow.evals.sweep import sweep                             # noqa: E402
from oryxflow.evals.task import TaskEval, _needs_trace, _split_rep  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    datadir = tmp_path / 'data'
    datadir.mkdir()
    monkeypatch.setattr(oryxflow.settings, 'dir', str(datadir))
    monkeypatch.setattr(oryxflow.settings, 'dirpath', datadir)
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    monkeypatch.chdir(tmp_path)
    oryxflow.state.clear_cache()
    oryxflow.core._code_warned.clear()
    yield tmp_path


class Turn(BaseModel):
    q: int


class Reply(BaseModel):
    message: str
    score: float


CALLS = []


@pytest.fixture(autouse=True)
def reset_calls():
    CALLS.clear()
    yield


@dataclasses.dataclass
class Above(Evaluator):
    """Reads the TYPED output (ctx.output.score), so it proves the rehydration."""
    cut: float = 0.5

    def evaluate(self, ctx: EvaluatorContext) -> bool:
        return ctx.output.score > self.cut


def make_cases(n=4, bump=0):
    return [Case(name='c{}'.format(i), inputs=Turn(q=i + bump),
                 metadata={'kind': 'odd' if i % 2 else 'even'}) for i in range(n)]


def make_eval(cut=0.5, cases=None, arm='v1', family='SplitEval', boom=()):
    async def case(self, inputs):
        CALLS.append(inputs.q)
        if inputs.q in boom:
            raise ValueError('boom on {}'.format(inputs.q))
        return Reply(message='m{}'.format(inputs.q), score=inputs.q / 4.0)

    return type(family, (TaskEval,), dict(
        dataset=Dataset(name='ds', cases=cases or make_cases(),
                        evaluators=[Above(cut=cut)]),
        metric=Metric('above', 'Above'),
        retry_task=None, preflight=None, max_failure_rate=0.5,
        code_version=lambda self, arm=arm: arm,
        case=case))


def run(cls, **kw):
    return sweep(cls, confirm=False, **kw)


def test_a_scorer_edit_rescores_with_zero_model_calls(env):
    r1 = run(make_eval(cut=0.5))
    assert len(CALLS) == 4
    before = r1.df.set_index('case_name')['Above'].tolist()

    r2 = run(make_eval(cut=0.1))       # same family, same arm, a different rubric
    assert len(CALLS) == 4, 'a scorer edit must not call the model again'
    after = r2.df.set_index('case_name')['Above'].tolist()
    assert before != after             # ...but the scores did change


def test_an_unchanged_cell_is_free_in_both_stages(env):
    run(make_eval())
    run(make_eval())
    assert len(CALLS) == 4


def test_changing_what_the_arm_reads_reruns_the_model(env):
    run(make_eval(arm='v1'))
    run(make_eval(arm='v2'))
    assert len(CALLS) == 8


def test_editing_a_case_reruns_the_class_form(env):
    run(make_eval(cases=make_cases(bump=0)))
    run(make_eval(cases=make_cases(bump=10)))
    assert len(CALLS) == 8, 'an edited case set must not be served from cache'


def test_outputs_are_stored_as_json_records(env):
    cls = make_eval()
    run(cls, repeats=2)
    records = cls._outputs_cls()(repeats=2).outputLoad(keys='outputs')
    assert len(records) == 8
    first = records[0]
    assert first['output'] == {'message': 'm0', 'score': 0.0}
    assert first['output_type'].endswith(':Reply')
    assert sorted({(r['source_case_name'], r['rep']) for r in records}) == sorted(
        ('c{}'.format(i), rep) for i in range(4) for rep in (0, 1))


def test_repeats_and_failures_survive_rescoring(env):
    run(make_eval(cut=0.5, boom=(3,)), repeats=2)
    r = run(make_eval(cut=0.1, boom=(3,)), repeats=2)
    df = r.df
    assert len(CALLS) == 8
    failed = df[df['error'] != '']
    assert set(failed['source_case_name']) == {'c3'}
    assert sorted(failed['rep'].tolist()) == [0, 1]
    assert set(df['rep']) == {0, 1}


def test_split_rep_reads_the_pydantic_evals_suffix():
    assert _split_rep('c1 [2/3]', 'c1') == ('c1', 1)
    assert _split_rep('c1') == ('c1', 0)
    assert _split_rep('odd [name] x') == ('odd [name] x', 0)


def test_span_evaluators_are_routed_to_the_outputs_stage():
    from pydantic_evals.evaluators import HasMatchingSpan
    assert _needs_trace(HasMatchingSpan(query={'name_contains': 'tool'}))
    assert not _needs_trace(Above())

    class Flagged(Evaluator):
        needs_trace = True

        def evaluate(self, ctx):
            return True

    assert _needs_trace(Flagged())


def test_a_trace_evaluator_scores_in_the_outputs_stage(env):
    class Traced(Evaluator):
        needs_trace = True

        def evaluate(self, ctx: EvaluatorContext) -> bool:
            return True

    async def case(self, inputs):
        CALLS.append(inputs.q)
        return Reply(message='m', score=1.0)

    cls = type('TracedEval', (TaskEval,), dict(
        dataset=Dataset(name='ds', cases=make_cases(2),
                        evaluators=[Traced(), Above()]),
        metric=Metric('above', 'Above'), retry_task=None, preflight=None,
        code_version='v1', case=case))
    df = run(cls).df
    assert df['Traced'].tolist() == [True, True]
    assert df['Above'].tolist() == [True, True]
    records = cls._outputs_cls()().outputLoad(keys='outputs')
    assert records[0]['trace_results'] == {'Traced': True}


def test_direct_run_still_builds_its_outputs(env):
    cls = make_eval()
    task = cls()
    task.run()
    assert len(CALLS) == 4
    assert len(task.outputLoad(keys='cases')) == 4


def test_reset_discards_the_model_calls_too(env):
    run(make_eval())
    run(make_eval(), reset=True)
    assert len(CALLS) == 8, '--reset must re-call the model, not only re-score'


def test_rescore_reruns_scoring_without_model_calls(env):
    run(make_eval())
    r = run(make_eval(), rescore=True)
    assert len(CALLS) == 4
    assert len(r.df) == 4


def test_the_bill_charges_a_rescore_nothing(env, capsys):
    run(make_eval(cut=0.5))
    capsys.readouterr()
    run(make_eval(cut=0.1))
    out = capsys.readouterr().out
    assert 'new 0' in out
    assert 're-scoring 1 arm from stored outputs: no model calls' in out


# ------------------------------------------------------------- baseline + side by side

def make_armed(family='ArmedEval'):
    async def case(self, inputs):
        CALLS.append(inputs.q)
        bonus = 0.5 if self.prompt_version == 'live' else 0.0
        return Reply(message='{}-{}'.format(self.prompt_version, inputs.q),
                     score=inputs.q / 4.0 + bonus)

    return type(family, (TaskEval,), dict(
        dataset=Dataset(name='ds', cases=make_cases(), evaluators=[Above()]),
        metric=Metric('above', 'Above'), retry_task=None, preflight=None,
        prompt_version=oryxflow.Parameter(default='baseline'),
        code_version='v1', case=case))


def test_the_verdict_reads_every_arm_against_the_baseline(env):
    r = run(make_armed(), prompt_version=['live', 'baseline'])
    assert r.baseline == 'baseline'
    text = ' '.join(r.verdict(print_it=False).split())
    assert 'live vs baseline on above' in text
    assert 'wins' not in text


def test_an_unknown_baseline_is_refused(env):
    with pytest.raises(ValueError, match='not one of the arms'):
        run(make_armed(), prompt_version=['live', 'baseline'], baseline='nope')


def test_side_by_side_shows_only_disagreeing_cases_by_default(env, tmp_path):
    r = run(make_armed(), prompt_version=['live', 'baseline'])
    path = r.side_by_side(tmp_path / 'sbs.md')
    text = path.read_text(encoding='utf-8')
    # baseline scores q/4 > 0.5 -> c3 only; live adds 0.5 -> c1, c2, c3
    assert '## c1' in text and '## c2' in text
    assert '## c0' not in text and '## c3' not in text
    assert '### baseline (baseline)' in text
    assert 'live-1' in text
    full = r.side_by_side(tmp_path / 'all.md', all=True).read_text(encoding='utf-8')
    assert '## c0' in full and '## c3' in full
    assert len(CALLS) == 8, 'rendering reads stored outputs; it calls nothing'


def test_side_by_side_takes_a_template_string(env, tmp_path):
    r = run(make_armed(), prompt_version=['live', 'baseline'])
    text = r.side_by_side(tmp_path / 't.md', all=True,
                          template='{% for c in cases %}[{{ c.name }}]{% endfor %}\n'
                          ).read_text(encoding='utf-8')
    assert text.strip() == '[c0][c1][c2][c3]'
