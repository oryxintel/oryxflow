"""``sweep()`` and ``EvalResult``: the function form, the cache, and the verdict rules.

Everything here is offline -- the target is a stub coroutine with a call counter, so no
test makes a network call and every caching claim is checked against real invocations.

The three tests that carry the whole design are ``test_second_sweep_runs_zero_new_cells``
(a re-run costs nothing), ``test_third_arm_runs_only_the_third`` (marginal cost is one
arm) and ``test_editing_the_target_invalidates_only_its_cells`` (an edit is not something
you have to remember to reset). They count stub calls, not log lines.

The verdict rules are driven from hand-built DataFrames -- no pydantic-evals object is
involved -- which is the seam the whole plan rests on.
"""
import sys

import pytest

pytest.importorskip('pydantic_evals')

import pandas as pd                                              # noqa: E402
from pydantic import BaseModel                                   # noqa: E402
from pydantic_evals import Case, Dataset                         # noqa: E402

import oryxflow                                                  # noqa: E402
import oryxflow.state                                            # noqa: E402
from oryxflow.evals.metric import Metric                         # noqa: E402
from oryxflow.evals.sweep import EvalResult, sweep               # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated data dir per test; state cache reset."""
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
    wrote: bool


CALLS = []


def make_cases(n=4):
    return [Case(name='c{}'.format(i), inputs=Turn(q=i),
                 metadata={'kind': 'odd' if i % 2 else 'even'})
            for i in range(n)]


async def run_turn(inputs, prompt_version):
    """The target under test: deterministic, counted, and offline."""
    CALLS.append((inputs.q, prompt_version))
    return Reply(message='{}-{}'.format(prompt_version, inputs.q),
                 wrote=prompt_version == 'b' or inputs.q % 2 == 0)


async def run_turn_edited(inputs, prompt_version):
    """Same name is impossible here, so test 20 swaps the FUNCTION and keeps the class
    name pinned with name=; what moves is the source hash, which is the point."""
    CALLS.append((inputs.q, prompt_version))
    return Reply(message='{}-{}!'.format(prompt_version, inputs.q),
                 wrote=True)


def flat(text):
    """Verdict prose is wrapped for reading; assertions are about the words."""
    return ' '.join(text.split())


def calls_for(arm=None):
    return len([c for c in CALLS if arm is None or c[1] == arm])


@pytest.fixture(autouse=True)
def reset_calls():
    del CALLS[:]
    yield


# ------------------------------------------------------- 17. the function form

def test_function_form_needs_no_class(env, capsys):
    r = sweep(run_turn, cases=make_cases(), prompt_version=['a', 'b'],
              metric=Metric('wrote', 'wrote'), name='FnForm')

    assert isinstance(r, EvalResult)
    df = r.df
    assert set(df['prompt_version']) == {'a', 'b'}
    assert set(df['arm']) == {'a', 'b'}
    assert len(df) == 8
    assert sorted(df['case_name'].unique()) == ['c0', 'c1', 'c2', 'c3']

    # the bill is printed before anything is spent
    out = capsys.readouterr().out
    assert '8 calls' in out
    assert 'cached 0' in out and 'new 8' in out
    assert 'not estimated' in out          # no vendor price table shipped

    # 4 cases x 2 arms, plus preflight's one probe call per arm
    assert calls_for() == 10
    assert calls_for('a') == 5 and calls_for('b') == 5


def test_function_form_rejects_an_arm_the_function_cannot_take(env):
    with pytest.raises(TypeError, match='does not accept model_id'):
        sweep(run_turn, cases=make_cases(), prompt_version=['a'], model_id=['x', 'y'])


# ------------------------------------------------------- 18. the caching claim

def test_second_sweep_runs_zero_new_cells(env, capsys):
    sweep(run_turn, cases=make_cases(), prompt_version=['a', 'b'], name='CacheClaim')
    first = calls_for()
    assert first == 10

    capsys.readouterr()
    r = sweep(run_turn, cases=make_cases(), prompt_version=['a', 'b'], name='CacheClaim')

    assert calls_for() == first           # ZERO new invocations
    assert len(r.df) == 8                 # ...and the frame still comes back in full
    out = capsys.readouterr().out
    assert 'cached 8' in out and 'new 0' in out
    assert r.cached_calls == 8 and r.new_calls == 0


# -------------------------------------------------- 19. the marginal-cost claim

def test_third_arm_runs_only_the_third(env):
    sweep(run_turn, cases=make_cases(), prompt_version=['a', 'b'], name='Marginal')
    two_arms = calls_for()
    assert two_arms == 10

    r = sweep(run_turn, cases=make_cases(), prompt_version=['a', 'b', 'c'],
              name='Marginal')

    assert calls_for() - two_arms == 5    # exactly one arm's worth (4 cases + preflight)
    assert calls_for('a') == 5 and calls_for('b') == 5 and calls_for('c') == 5
    assert r.cached_calls == 8 and r.new_calls == 4
    assert set(r.df['arm']) == {'a', 'b', 'c'}


# --------------------------------------------- 20. editing the target invalidates

def test_editing_the_target_invalidates_only_its_cells(env):
    sweep(run_turn, cases=make_cases(), prompt_version=['a', 'b'], name='Edited')
    before = calls_for()
    assert before == 10

    # an unrelated family stays cached across the edit
    sweep(run_turn, cases=make_cases(), prompt_version=['a'], name='Untouched')
    untouched = calls_for()
    assert untouched - before == 5

    # ...now the target's source changes and the SAME family re-runs
    r = sweep(run_turn_edited, cases=make_cases(), prompt_version=['a', 'b'],
              name='Edited')
    assert calls_for() - untouched == 10           # both of its arms, and only those
    assert bool(r.df['wrote'].astype(bool).all())  # the new function's answers

    # the untouched family is still free
    sweep(run_turn, cases=make_cases(), prompt_version=['a'], name='Untouched')
    assert calls_for() - untouched == 10

    # and the edited family is now cached at its new version
    sweep(run_turn_edited, cases=make_cases(), prompt_version=['a', 'b'], name='Edited')
    assert calls_for() - untouched == 10


# ------------------------------------------------------------ verdict fixtures

def coin_frame(arm_rates, n=12, col='ok'):
    """One arm per entry; ``arm_rates[arm]`` cases pass, spread over ``n`` cases.

    A plain per-case frame with no pydantic-evals object anywhere in it -- the seam.
    """
    rows = []
    for arm, k in arm_rates.items():
        for i in range(n):
            rows.append({'arm': arm, 'case_name': 'c{}'.format(i), col: i < k})
    return pd.DataFrame(rows)


# ----------------------------------------------- 21. no winner when inside noise

def test_no_winner_named_when_the_delta_spans_zero(env, capsys):
    # 12 cases, 6 vs 5: a one-case gap is nowhere near the noise floor
    df = coin_frame({'prod': 6, 'preship': 5})
    r = EvalResult(df, metric=Metric('accuracy', 'ok'), name='Noisy', repeats=1)
    text = r.verdict()

    assert 'inside noise' in text
    assert 'raise repeats' in flat(text)
    assert 'wins' not in text
    assert 'prod wins' not in text and 'preship wins' not in text
    assert 'inside noise' in capsys.readouterr().out


# --------------------------------------- 22. a broken guardrail is not a clean win

def test_guardrail_break_is_never_an_unqualified_win(env):
    rows = []
    for i in range(20):
        # prod is clearly better on accuracy...
        rows.append({'arm': 'prod', 'case_name': 'c{}'.format(i),
                     'ok': True, 'fp': i < 4})
        rows.append({'arm': 'preship', 'case_name': 'c{}'.format(i),
                     'ok': i < 8, 'fp': False})
    df = pd.DataFrame(rows)

    r = EvalResult(df, metric=Metric('accuracy', 'ok'),
                   guardrail=Metric('false positives', 'fp', higher_is_better=False,
                                    budget=0.05),
                   name='Guarded', repeats=3)
    text = r.verdict()

    assert 'outside noise' in text
    assert 'Not a clean win' in text
    assert 'wins' not in text                       # never the unqualified word
    assert 'breaks the false positives guardrail' in flat(text)
    assert 'over budget' in text

    arm, value, interval, clean = r.best()
    assert arm == 'prod' and value == 1.0 and clean is False
    assert interval[0] <= 1.0 and interval[1] <= 1.0


def test_a_clean_win_says_wins(env):
    rows = []
    for i in range(20):
        rows.append({'arm': 'prod', 'case_name': 'c{}'.format(i), 'ok': True,
                     'fp': False})
        rows.append({'arm': 'preship', 'case_name': 'c{}'.format(i), 'ok': i < 8,
                     'fp': False})
    r = EvalResult(pd.DataFrame(rows), metric=Metric('accuracy', 'ok'),
                   guardrail=Metric('false positives', 'fp', higher_is_better=False,
                                    budget=0.05),
                   name='Clean', repeats=3)
    text = r.verdict()
    assert 'prod wins the primary metric' in flat(text)
    assert 'clean win' not in text
    assert r.best()[3] is True


# --------------------------------------------------------------- 23. the report

def test_report_writes_markdown_with_the_table_and_the_verdict(env, tmp_path):
    df = coin_frame({'prod': 12, 'preship': 5}, n=12)
    r = EvalResult(df, metric=Metric('accuracy', 'ok'), name='Reported', repeats=3)

    path = r.report()
    assert path.exists()
    text = path.read_text(encoding='utf-8')

    assert '# Reported' in text
    assert '| arm | rate | n | 95% CI |' in text          # the arm table
    assert '| prod |' in text and '| preship |' in text
    assert 'ACCURACY' in text
    assert '**VERDICT' in text                            # the verdict line
    assert str(path).replace('\\', '/').startswith('results/')

    explicit = r.report(tmp_path / 'sub' / 'out.md')
    assert explicit.exists() and 'VERDICT' in explicit.read_text(encoding='utf-8')


# ------------------------------------------------------- 24. the dead-metric flag

def test_dead_metric_flag_fires_at_zero_in_every_arm(env):
    df = coin_frame({'prod': 0, 'preship': 0}, n=12)
    r = EvalResult(df, metric=Metric('pairing', 'ok'), name='Dead')
    text = r.verdict()
    lines = text.splitlines()

    assert 'identically 0% in all arms' in flat(text)
    assert 'usually measures the harness, not the model' in flat(text)
    assert "Check the metric's inputs before reading anything else" in flat(text)

    # above the tables: nothing below the flag is worth reading
    flag = next(i for i, l in enumerate(lines) if 'identically 0%' in l)
    table = next(i for i, l in enumerate(lines) if l.startswith('PAIRING'))
    assert flag < table


def test_dead_metric_flag_fires_at_one_hundred_in_every_arm(env):
    df = coin_frame({'prod': 12, 'preship': 12}, n=12)
    r = EvalResult(df, metric=Metric('pairing', 'ok'), name='Dead100')
    assert 'identically 100% in all arms' in flat(r.verdict())


def test_dead_metric_flag_silent_when_the_arms_differ(env):
    df = coin_frame({'prod': 12, 'preship': 3}, n=12)
    r = EvalResult(df, metric=Metric('pairing', 'ok'), name='Alive')
    text = r.verdict()
    assert 'identically' not in text
    assert 'measures the harness' not in text


def test_dead_metric_flag_silent_when_arms_agree_at_a_middling_rate(env):
    df = coin_frame({'prod': 6, 'preship': 6}, n=12)
    r = EvalResult(df, metric=Metric('pairing', 'ok'), name='Middling')
    assert 'identically' not in r.verdict()


# ------------------------------------------------------ 25. the denominator line

def test_denominator_drift_is_flagged(env):
    # both arms score 100% of what they answered -- but one answered half as often
    rows = []
    for i in range(20):
        rows.append({'arm': 'prod', 'case_name': 'c{}'.format(i),
                     'answered': True, 'good': i % 3 != 0})
        rows.append({'arm': 'preship', 'case_name': 'c{}'.format(i),
                     'answered': i < 10, 'good': i < 10})
    df = pd.DataFrame(rows)

    r = EvalResult(df, metric=Metric('quality', 'good', where='answered'),
                   name='Drift', repeats=1)
    text = r.verdict()

    assert 'denominator moved (n=20 vs n=10)' in flat(text)
    assert 'not directly comparable across arms' in flat(text)
    assert 'read the coverage metric first' in flat(text)


def test_equal_denominators_are_not_flagged(env):
    df = coin_frame({'prod': 9, 'preship': 4}, n=12)
    r = EvalResult(df, metric=Metric('accuracy', 'ok'), name='Steady')
    assert 'denominator moved' not in r.verdict()


# --------------------------------------------------------- 26. coverage ordering

def test_coverage_metric_renders_first(env):
    rows = []
    for i in range(20):
        rows.append({'arm': 'prod', 'case_name': 'c{}'.format(i),
                     'answered': True, 'good': i % 3 != 0})
        rows.append({'arm': 'preship', 'case_name': 'c{}'.format(i),
                     'answered': i < 14, 'good': i < 10})
    df = pd.DataFrame(rows)

    coverage = Metric('coverage', 'answered')
    r = EvalResult(df, metric=Metric('quality', 'good', where='answered',
                                     coverage=coverage),
                   name='Ordered', repeats=1)
    lines = r.verdict().splitlines()

    cov = next(i for i, l in enumerate(lines) if l.startswith('COVERAGE'))
    qual = next(i for i, l in enumerate(lines) if l.startswith('QUALITY'))
    assert cov < qual
    assert 'never read alone' in flat(r.verdict(print_it=False))


# ------------------------------------------------------------------ extra rules

def test_holdout_and_failure_counts_are_stated(env):
    rows = []
    for i in range(12):
        rows.append({'arm': 'prod', 'case_name': 'c{}'.format(i), 'ok': i < 9,
                     'error': '' if i else 'boom'})
        rows.append({'arm': 'preship', 'case_name': 'c{}'.format(i), 'ok': i < 4,
                     'error': ''})
    r = EvalResult(pd.DataFrame(rows), metric=Metric('accuracy', 'ok'),
                   name='Counts', excluded=3, repeats=1)
    text = r.verdict()

    assert '3 holdout case(s) excluded from every arm' in flat(text)
    assert '1 case(s) failed and are excluded from every rate' in flat(text)


def test_synthetic_is_reported_beside_the_headline_not_in_it(env):
    rows = []
    for i in range(12):
        rows.append({'arm': 'prod', 'case_name': 'r{}'.format(i), 'ok': i < 6,
                     'synthetic': False})
        rows.append({'arm': 'preship', 'case_name': 'r{}'.format(i), 'ok': i < 3,
                     'synthetic': False})
    for i in range(6):
        rows.append({'arm': 'prod', 'case_name': 's{}'.format(i), 'ok': True,
                     'synthetic': True})
        rows.append({'arm': 'preship', 'case_name': 's{}'.format(i), 'ok': True,
                     'synthetic': True})
    r = EvalResult(pd.DataFrame(rows), metric=Metric('accuracy', 'ok'),
                   name='Synth', repeats=1)
    text = r.verdict()

    assert 'synthetic n=12' in text
    # the headline is real-only: 6/12, not 12/18
    assert '(6/12)' in text
    assert r.best()[1] == 0.5


def test_weakest_slices_are_named(env):
    rows = []
    for i in range(12):
        rows.append({'arm': 'prod', 'case_name': 'c{}'.format(i), 'ok': i % 4 != 0,
                     'phrasing': 'vague' if i < 6 else 'plain'})
        rows.append({'arm': 'preship', 'case_name': 'c{}'.format(i), 'ok': i < 2,
                     'phrasing': 'vague' if i < 6 else 'plain'})
    r = EvalResult(pd.DataFrame(rows), metric=Metric('accuracy', 'ok'),
                   slices=('phrasing',), name='Sliced', repeats=1)
    text = r.verdict()
    assert 'Weakest slices for prod' in text
    assert 'phrasing=vague' in text


def test_no_metric_is_a_clear_error_not_a_crash(env):
    r = EvalResult(coin_frame({'a': 1}, n=2))
    with pytest.raises(ValueError, match='no metric'):
        r.verdict()
    assert len(r.df) == 2                      # ...but the escape hatch still works


# ---------------------------------------------------- the invalidation mechanism

def test_target_source_hash_moves_with_the_body(env):
    """What makes test 20 work: the token is the function's source, not its name."""
    from oryxflow.evals.sweep import _case_digest, _source_hash

    assert _source_hash(run_turn) != _source_hash(run_turn_edited)
    assert _source_hash(run_turn) == _source_hash(run_turn)

    # ...and the case set is folded in too, so adding a case re-runs the arm
    ds4 = Dataset(name='d', cases=make_cases(4))
    assert _case_digest(ds4) == _case_digest(Dataset(name='d', cases=make_cases(4)))
    assert _case_digest(ds4) != _case_digest(Dataset(name='d', cases=make_cases(5)))


def test_added_cases_rerun_the_arm(env):
    sweep(run_turn, cases=make_cases(4), prompt_version=['a'], name='Grown')
    assert calls_for() == 5
    sweep(run_turn, cases=make_cases(5), prompt_version=['a'], name='Grown')
    assert calls_for() == 11                  # 5 cases + preflight, nothing reused


# ------------------------------------------------------------ shapes of the grid

def test_multiple_axes_are_a_cartesian_product_with_legible_arms(env):
    async def two_axis(inputs, prompt_version, model_id):
        CALLS.append((inputs.q, prompt_version + '/' + model_id))
        return Reply(message='m', wrote=True)

    r = sweep(two_axis, cases=make_cases(2), prompt_version=['a', 'b'],
              model_id=['x', 'y'], name='Grid')

    assert sorted(set(r.df['arm'])) == ['prompt_version_a_model_id_x',
                                        'prompt_version_a_model_id_y',
                                        'prompt_version_b_model_id_x',
                                        'prompt_version_b_model_id_y']
    assert set(r.df['prompt_version']) == {'a', 'b'}
    assert set(r.df['model_id']) == {'x', 'y'}
    assert len(r.df) == 8                     # 2 cases x 4 arms
    assert calls_for() == 12                  # + one preflight per arm


def test_a_scalar_keyword_is_fixed_for_every_arm(env):
    async def fixed_arm(inputs, prompt_version, temperature):
        CALLS.append((inputs.q, '{}@{}'.format(prompt_version, temperature)))
        return Reply(message='m', wrote=True)

    r = sweep(fixed_arm, cases=make_cases(2), prompt_version=['a', 'b'],
              temperature=0.0, name='Fixed')
    assert set(r.df['arm']) == {'a', 'b'}
    assert 'temperature' not in r.df.columns  # not an axis, so not a tag
    assert all(c[1].endswith('@0.0') for c in CALLS)


# --------------------------------------------------------------- the class form

def test_class_form_sweeps_a_taskeval_subclass(env):
    from oryxflow.evals.task import TaskEval

    async def case(self, inputs):
        CALLS.append((inputs.q, self.prompt_version))
        return Reply(message='m', wrote=inputs.q % 2 == 0)

    cls = type('ClassArm', (TaskEval,), dict(
        dataset=Dataset(name='ds', cases=make_cases(4)),
        prompt_version=oryxflow.Parameter(default='a'),
        metric=Metric('wrote', 'wrote'),
        preflight=None,
        code_version='v1',
        case=case))

    r = sweep(cls, prompt_version=['a', 'b'])
    assert set(r.df['arm']) == {'a', 'b'}
    assert calls_for() == 8                   # preflight disabled on this class
    assert r.metric.label == 'wrote'          # picked up from the class

    before = calls_for()
    sweep(cls, prompt_version=['a', 'b'])
    assert calls_for() == before              # still cached


def test_class_form_refuses_a_dataset_argument(env):
    from oryxflow.evals.task import TaskEval

    cls = type('ClassArmDs', (TaskEval,), dict(
        dataset=Dataset(name='ds', cases=make_cases(2)), code_version='v1'))
    with pytest.raises(ValueError, match='declares its own dataset'):
        sweep(cls, cases=make_cases(2))


# ------------------------------------- watch=: files the target READS, not contains

async def run_turn_reading(inputs, prompt_version):
    """Reads its prompt from disk -- the shape watch= exists for."""
    CALLS.append((inputs.q, prompt_version))
    text = open('prompts/system.md', encoding='utf-8').read()
    return Reply(message=text, wrote=text.startswith('be'))


def _write_prompt(root, text):
    (root / 'prompts').mkdir(exist_ok=True)
    (root / 'prompts' / 'system.md').write_text(text, encoding='utf-8')


def test_watch_reruns_when_a_read_file_changes(env, capsys):
    # without watch= the prompt is outside the cache key: this is the trap, pinned
    # so it stays a deliberate default rather than becoming one by accident.
    _write_prompt(env, 'be concise')
    kw = dict(dataset=Dataset(name='d', cases=make_cases(2)),
              metric=Metric('yield', 'wrote'), confirm=False,
              prompt_version=['a', 'b'])

    sweep(run_turn_reading, name='Unwatched', **kw)
    cold = calls_for()
    assert cold > 0
    _write_prompt(env, 'be verbose')
    sweep(run_turn_reading, name='Unwatched', **kw)
    assert calls_for() == cold          # blind to the edit

    # with watch= the same edit re-runs every arm that reads it
    _write_prompt(env, 'be concise')
    del CALLS[:]
    sweep(run_turn_reading, name='Watched', watch='prompts/*.md', **kw)
    cold = calls_for()
    sweep(run_turn_reading, name='Watched', watch='prompts/*.md', **kw)
    assert calls_for() == cold          # no edit -> free

    _write_prompt(env, 'be verbose')
    sweep(run_turn_reading, name='Watched', watch='prompts/*.md', **kw)
    assert calls_for() == cold * 2      # edited -> both arms again


def test_watch_pattern_matching_nothing_raises(env):
    # a typo'd path that hashed to "no files" would report every run as unchanged
    _write_prompt(env, 'be concise')
    with pytest.raises(FileNotFoundError):
        sweep(run_turn_reading, dataset=Dataset(name='d', cases=make_cases(2)),
              metric=Metric('yield', 'wrote'), confirm=False,
              watch='prompts/*.nope', prompt_version=['a'])


def test_watch_accepts_a_callable_for_anchored_patterns(env):
    _write_prompt(env, 'be concise')
    root = env
    sweep(run_turn_reading, dataset=Dataset(name='d', cases=make_cases(2)),
          metric=Metric('yield', 'wrote'), confirm=False, name='Anchored',
          watch=lambda: oryxflow.hash_files('prompts/*.md', root=root),
          prompt_version=['a'])
    cold = calls_for()
    sweep(run_turn_reading, dataset=Dataset(name='d', cases=make_cases(2)),
          metric=Metric('yield', 'wrote'), confirm=False, name='Anchored',
          watch=lambda: oryxflow.hash_files('prompts/*.md', root=root),
          prompt_version=['a'])
    assert calls_for() == cold


def test_watch_is_rejected_on_the_class_form(env):
    # the class form declares its own code_version(); two mechanisms would disagree
    class Declared(oryxflow.evals.task.TaskEval):
        dataset = Dataset(name='d', cases=make_cases(2))
        metric = Metric('yield', 'wrote')

        async def case(self, inputs):
            return Reply(message='x', wrote=True)

    with pytest.raises(ValueError, match='watch='):
        sweep(Declared, confirm=False, watch='prompts/*.md')


# ------------------------------- 24. sweep labels never shadow a case's own column

def test_metadata_key_colliding_with_the_arm_label_is_refused_before_spending(env):
    """`arm` is the word a real dataset reaches for first -- an eval comparing an
    'outline' arm with a 'guidelines' arm tags every case with exactly that. The
    tag used to overwrite it silently, so slices=('arm',) reported the flow name
    back and the comparison the dataset was built for was unavailable."""
    cases = [Case(name='c{}'.format(i), inputs=Turn(q=i),
                  metadata={'arm': 'outline' if i % 2 else 'guidelines'})
             for i in range(4)]
    with pytest.raises(ValueError, match="'arm'"):
        sweep(run_turn, cases=cases, prompt_version=['a', 'b'],
              metric=Metric('wrote', 'wrote'), name='Collide', confirm=False)
    assert calls_for() == 0            # refused BEFORE any call was made


def test_metadata_key_colliding_with_an_axis_name_is_refused(env):
    cases = [Case(name='c{}'.format(i), inputs=Turn(q=i),
                  metadata={'prompt_version': 'from-the-dataset'}) for i in range(2)]
    with pytest.raises(ValueError, match='prompt_version'):
        sweep(run_turn, cases=cases, prompt_version=['a', 'b'],
              metric=Metric('wrote', 'wrote'), name='Collide2', confirm=False)


def test_ordinary_metadata_is_untouched(env):
    r = sweep(run_turn, cases=make_cases(), prompt_version=['a', 'b'],
              metric=Metric('wrote', 'wrote'), slices=('kind',), name='NoClash',
              confirm=False)
    assert set(r.df['kind']) == {'odd', 'even'}
    assert set(r.df['arm']) == {'a', 'b'}


# ------------------------------- 25. an axis that does not vary stays out of the name

def test_single_value_axis_is_not_in_the_arm_name(env):
    """A constant axis lengthens every row of every table and distinguishes nothing."""
    async def target(inputs, model_id, prompt_version):
        return Reply(message='x', wrote=True)
    r = sweep(target, cases=make_cases(2), model_id=['gemini-flash'],
              prompt_version=['a', 'b'], metric=Metric('wrote', 'wrote'),
              name='Naming', confirm=False)
    assert sorted(r.arms) == ['a', 'b']


def test_two_varying_axes_keep_their_qualified_names(env):
    async def target(inputs, model_id, prompt_version):
        return Reply(message='x', wrote=True)
    r = sweep(target, cases=make_cases(2), model_id=['m1', 'm2'],
              prompt_version=['a', 'b'], metric=Metric('wrote', 'wrote'),
              name='Naming2', confirm=False)
    assert 'model_id_m1_prompt_version_a' in r.arms


# ------------------------------- 26. evaluators reach the function form

def test_evaluators_are_passed_to_the_generated_dataset(env):
    """Scoring against expected_output is the evaluator's job -- the target only
    ever sees `inputs` -- so without this the commonest eval shape of all, a
    classifier graded on its expected label, could not use the short form."""
    from pydantic_evals.evaluators import EqualsExpected

    async def classify(inputs, prompt_version):
        return 'odd' if inputs.q % 2 else 'even'

    cases = [Case(name='c{}'.format(i), inputs=Turn(q=i),
                  expected_output='odd' if i % 2 else 'even') for i in range(4)]
    r = sweep(classify, cases=cases, evaluators=(EqualsExpected(),),
              prompt_version=['a'], metric=Metric('accuracy', 'EqualsExpected'),
              name='Classify', confirm=False)
    assert 'EqualsExpected' in r.df.columns
    assert r.best()[1] == 1.0


# ------------------------------- 27. unmeasured rows are stated, not folded in

def test_a_partly_judged_metric_says_so(env):
    rows = []
    for arm in ('prod', 'preship'):
        for i in range(8):
            rows.append({'arm': arm, 'case_name': 'c{}'.format(i), 'error': '',
                         'pairing': (arm == 'prod') if i < 4 else None})
    r = EvalResult(pd.DataFrame(rows), metric=Metric('pairing', 'pairing'),
                   name='Partly', repeats=1)
    text = flat(r.verdict())
    assert '8 of 16 eligible rows carry no pairing verdict' in text
    assert 'unmeasured is not failed' in text


def test_a_wholly_unmeasured_metric_says_so_loudly(env):
    rows = [{'arm': a, 'case_name': 'c{}'.format(i), 'error': '', 'pairing': None}
            for a in ('prod', 'preship') for i in range(4)]
    r = EvalResult(pd.DataFrame(rows), metric=Metric('pairing', 'pairing'),
                   name='Blank', repeats=1)
    text = flat(r.verdict())
    assert 'NOT MEASURED' in text
    # the rate itself renders as n/a, never as a confident zero
    assert 'prod n/a (0/0)' in text
    assert 'VERDICT no arm produced a usable rate' in text


# ------------------------------- 28. weakest slices means weakest

def test_a_slice_at_or_above_the_arm_rate_is_not_called_weakest(env):
    rows = []
    for i in range(8):
        rows.append({'arm': 'solo', 'case_name': 'c{}'.format(i), 'error': '',
                     'ok': True, 'phrasing': 'a' if i < 4 else 'b'})
    r = EvalResult(pd.DataFrame(rows), metric=Metric('accuracy', 'ok'),
                   slices=('phrasing',), name='AllFine', repeats=1)
    assert 'Weakest slices' not in r.verdict()


def test_a_sweep_where_nothing_varies_still_names_its_one_cell(env):
    """Collapsing every axis would leave 'default', which says less than the
    verbose name -- and with one row there is no table width to save."""
    async def target(inputs, model_id, prompt_version):
        return Reply(message='x', wrote=True)
    r = sweep(target, cases=make_cases(2), model_id=['m1'], prompt_version=['a'],
              metric=Metric('wrote', 'wrote'), name='Solo', confirm=False)
    assert r.arms == ['model_id_m1_prompt_version_a']


# ------------------------------- 29. a sync case silently serialises

def test_a_sync_target_warns_that_concurrency_is_being_ignored(env):
    """It is not an error and the numbers are identical -- you just wait 5x longer
    with the knob still reading as set, which nothing else would ever tell you."""
    def sync_target(inputs, prompt_version):
        return Reply(message='x', wrote=True)

    with pytest.warns(RuntimeWarning, match='concurrency'):
        sweep(sync_target, cases=make_cases(2), prompt_version=['a'],
              metric=Metric('wrote', 'wrote'), concurrency=4, name='SyncWarn',
              confirm=False)


def test_an_async_target_does_not_warn(env, recwarn):
    sweep(run_turn, cases=make_cases(2), prompt_version=['a'],
          metric=Metric('wrote', 'wrote'), concurrency=4, name='AsyncQuiet',
          confirm=False)
    assert not [w for w in recwarn if 'concurrency' in str(w.message)]


def test_a_sync_target_at_concurrency_one_does_not_warn(env, recwarn):
    """Nothing is being ignored there, so there is nothing to say."""
    def sync_target(inputs, prompt_version):
        return Reply(message='x', wrote=True)
    sweep(sync_target, cases=make_cases(2), prompt_version=['a'],
          metric=Metric('wrote', 'wrote'), concurrency=1, name='SyncSerial',
          confirm=False)
    assert not [w for w in recwarn if 'concurrency' in str(w.message)]


# ------------------------------- 30. a clean guardrail is not a dead metric

def test_a_guardrail_inside_budget_in_every_arm_is_not_flagged(env):
    """0% false-positives in both arms is the outcome the guardrail exists to
    confirm; flagging it teaches the reader to ignore the flag."""
    rows = [{'arm': a, 'case_name': 'c{}'.format(i), 'error': '',
             'ok': i % 2 == 0, 'acted': False, 'control': True}
            for a in ('prod', 'preship') for i in range(6)]
    r = EvalResult(pd.DataFrame(rows), metric=Metric('quality', 'ok'),
                   guardrail=Metric('false action', 'acted', where='control',
                                    higher_is_better=False, budget=0.0),
                   name='CleanGuard', repeats=1)
    text = flat(r.verdict())
    assert 'FALSE ACTION: identically' not in text


def test_a_guardrail_broken_in_every_arm_is_still_flagged(env):
    """Identically 100% over budget really is suspicious - the control column may
    not be what the metric thinks it is."""
    rows = [{'arm': a, 'case_name': 'c{}'.format(i), 'error': '',
             'ok': i % 2 == 0, 'acted': True, 'control': True}
            for a in ('prod', 'preship') for i in range(6)]
    r = EvalResult(pd.DataFrame(rows), metric=Metric('quality', 'ok'),
                   guardrail=Metric('false action', 'acted', where='control',
                                    higher_is_better=False, budget=0.0),
                   name='BrokeGuard', repeats=1)
    assert 'FALSE ACTION: identically 100%' in flat(r.verdict())


# ------------------------------- 31. the cheap probe is offered at the decision point

def _answers(monkeypatch, *replies):
    seen = iter(replies)
    asked = []

    def fake_input(prompt):
        asked.append(prompt)
        return next(seen)
    # `oryxflow.evals.sweep` as an ATTRIBUTE is the function -- the package
    # re-exports it over its own submodule -- so reach the module through sys.modules.
    sweep_module = sys.modules['oryxflow.evals.sweep']
    monkeypatch.setattr('builtins.input', fake_input)
    monkeypatch.setattr(sweep_module, '_interactive', lambda: True)
    return asked


def test_the_confirmation_offers_the_one_call_check(env, monkeypatch, capsys):
    """A --check flag only helps someone who already knows it exists, and the
    person who needs it is running the eval for the first time."""
    asked = _answers(monkeypatch, 'c', 'y')
    sweep(run_turn, cases=make_cases(2), prompt_version=['a'],
          metric=Metric('wrote', 'wrote'), name='Offered', confirm=True)
    assert 'c=check one call first' in asked[0]
    assert 'preflight OK' in capsys.readouterr().out
    # ...and having probed, it asks again rather than assuming consent
    assert len(asked) == 2
    assert 'c=check' not in asked[1]


def test_answering_yes_straight_away_never_probes(env, monkeypatch, capsys):
    _answers(monkeypatch, 'y')
    sweep(run_turn, cases=make_cases(2), prompt_version=['a'],
          metric=Metric('wrote', 'wrote'), name='StraightYes', confirm=True)
    assert 'preflight OK' not in capsys.readouterr().out


def test_declining_after_the_probe_still_aborts(env, monkeypatch):
    _answers(monkeypatch, 'c', 'n')
    with pytest.raises(RuntimeError, match='aborted before spending'):
        sweep(run_turn, cases=make_cases(2), prompt_version=['a'],
              metric=Metric('wrote', 'wrote'), name='ProbedNo', confirm=True)


def test_nothing_is_spent_before_an_answer(env, monkeypatch):
    """The probe is offered, never run automatically: a tool that bills money does
    not make its first call unasked, however cheap."""
    _answers(monkeypatch, 'n')
    with pytest.raises(RuntimeError):
        sweep(run_turn, cases=make_cases(2), prompt_version=['a'],
              metric=Metric('wrote', 'wrote'), name='NoSpend', confirm=True)
    assert calls_for() == 0


# ------------------------------- 32. a crashed evaluator is not an absent one

def test_an_evaluator_that_raised_is_reported_above_the_rate(env):
    """An evaluator that crashed leaves no column, which reads exactly like one
    that declined to score. Judge broken on every case vs judge with nothing to
    judge must not look the same."""
    rows = [{'arm': a, 'case_name': 'c{}'.format(i), 'error': '', 'ok': None,
             'evaluator_errors': 1, 'evaluator_error': 'AuthError: no api key'}
            for a in ('prod', 'preship') for i in range(4)]
    r = EvalResult(pd.DataFrame(rows), metric=Metric('quality', 'ok'),
                   name='BrokenJudge', repeats=1)
    text = flat(r.verdict())
    assert '8 evaluator call(s) RAISED' in text
    assert 'AuthError: no api key' in text
    assert 'NOT MEASURED' in text          # ...and still never a confident 0%


def test_no_evaluator_errors_says_nothing(env):
    rows = [{'arm': a, 'case_name': 'c{}'.format(i), 'error': '', 'ok': True,
             'evaluator_errors': 0, 'evaluator_error': ''}
            for a in ('prod', 'preship') for i in range(4)]
    r = EvalResult(pd.DataFrame(rows), metric=Metric('quality', 'ok'),
                   name='FineJudge', repeats=1)
    assert 'RAISED' not in r.verdict()


def test_an_axis_named_arm_does_not_invent_a_meta_column(env):
    """The axis writes the column the tag then wants; preserving our own write
    would produce a spurious meta_arm."""
    async def target(inputs, arm):
        return Reply(message='x', wrote=True)
    r = sweep(target, cases=make_cases(2), arm=['a', 'b'],
              metric=Metric('wrote', 'wrote'), name='ArmAxis', confirm=False)
    assert 'meta_arm' not in r.df.columns
    assert sorted(set(r.df['arm'])) == ['a', 'b']


def test_a_real_metadata_arm_column_is_still_preserved_when_reached_directly(env):
    """sweep refuses it up front, but EvalResult built by hand must not lose it."""
    import pandas as _pd
    from oryxflow.evals.sweep import _tagger
    df = _pd.DataFrame([{'case_name': 'c0', 'arm': 'outline', 'wrote': True}])
    tagged = _tagger(['prompt_version'])('live', {'prompt_version': 'live'}, df)
    assert tagged['meta_arm'].tolist() == ['outline']
    assert tagged['arm'].tolist() == ['live']


# ------------------------------- 33. measured usage, once the task records it

def test_measured_usage_is_reported_when_the_task_recorded_it(env):
    """The bill before a run can only estimate; this is the thing that happened."""
    rows = [{'arm': a, 'case_name': 'c{}'.format(i), 'error': '', 'ok': True,
             'input_tokens': 1000, 'output_tokens': 250}
            for a in ('x', 'y') for i in range(3)]
    r = EvalResult(pd.DataFrame(rows), metric=Metric('ok', 'ok'), name='Usage',
                   repeats=1)
    text = flat(r.verdict())
    assert 'measured usage: input tokens 6,000, output tokens 1,500' in text


def test_no_usage_columns_means_no_usage_line(env):
    r = EvalResult(coin_frame({'a': 1, 'b': 0}, n=4), metric=Metric('ok', 'ok'),
                   name='NoUsage', repeats=1)
    assert 'measured usage' not in r.verdict()


# ------------------------------- 34. the judge is reported, and the rate corrected

def test_judge_alignment_and_correction_appear_with_the_metric(env):
    rows = []
    for arm, judged in (('prod', True), ('preship', False)):
        for i in range(40):
            rows.append({'arm': arm, 'case_name': 'c{}'.format(i), 'error': '',
                         'ok': judged if i % 5 else (not judged),
                         'ok_human': (judged if i % 4 else (not judged))
                                     if i < 20 else None})
    r = EvalResult(pd.DataFrame(rows), metric=Metric('ok', 'ok', human='ok_human'),
                   name='Judged', repeats=1)
    text = flat(r.verdict())
    assert 'judge vs 40 human label(s)' in text
    assert 'kappa' in text and 'read kappa, not this' in text


def test_an_uninformative_judge_refuses_to_correct_and_says_why(env):
    rows = [{'arm': a, 'case_name': 'c{}'.format(i), 'error': '', 'ok': True,
             'ok_human': i % 10 != 0}
            for a in ('prod', 'preship') for i in range(30)]
    r = EvalResult(pd.DataFrame(rows), metric=Metric('ok', 'ok', human='ok_human'),
                   name='BadJudge', repeats=1)
    text = flat(r.verdict())
    assert 'NOT CORRECTED' in text
    assert 'corrected for judge error' not in text


def test_no_human_column_means_no_judge_section(env):
    r = EvalResult(coin_frame({'a': 1, 'b': 0}, n=6), metric=Metric('ok', 'ok'),
                   name='NoJudge', repeats=1)
    assert 'kappa' not in r.verdict()


# ------------------------------- 35. a guardrail the interval does not demonstrate

def test_a_guardrail_clear_on_the_point_but_not_the_interval_says_so(env):
    rows = [{'arm': 'solo', 'case_name': 'c{}'.format(i), 'error': '',
             'ok': True, 'acted': False, 'control': True} for i in range(8)]
    r = EvalResult(pd.DataFrame(rows), metric=Metric('quality', 'ok'),
                   guardrail=Metric('false action', 'acted', where='control',
                                    higher_is_better=False, budget=0.05),
                   name='Edge', repeats=1)
    text = flat(r.verdict())
    assert 'not demonstrated, only not yet violated' in text


# ------------------------------- 36. a saturated eval has stopped discriminating

def test_a_lone_arm_at_one_hundred_percent_is_flagged(env):
    rows = [{'arm': 'prod', 'case_name': 'c{}'.format(i), 'error': '', 'ok': True}
            for i in range(12)]
    r = EvalResult(pd.DataFrame(rows), metric=Metric('write rate', 'ok'),
                   name='Saturated', repeats=1)
    text = flat(r.verdict())
    assert 'saturated eval has stopped discriminating' in text


def test_a_lone_arm_below_one_hundred_is_not_flagged(env):
    rows = [{'arm': 'prod', 'case_name': 'c{}'.format(i), 'error': '',
             'ok': i > 0} for i in range(12)]
    r = EvalResult(pd.DataFrame(rows), metric=Metric('write rate', 'ok'),
                   name='NotSaturated', repeats=1)
    assert 'saturated' not in r.verdict()


def test_usage_totals_are_readable_not_scientific(env):
    """A token count as '7.356e+04' is unreadable as the thing it is."""
    rows = [{'arm': 'a', 'case_name': 'c{}'.format(i), 'error': '', 'ok': True,
             'input_tokens': 900 + i} for i in range(80)]
    text = EvalResult(pd.DataFrame(rows), metric=Metric('ok', 'ok'), name='U',
                      repeats=1).verdict(False)
    assert 'input tokens 75,160' in text
    assert 'e+0' not in text
