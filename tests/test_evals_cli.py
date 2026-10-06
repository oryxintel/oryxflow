"""``ev.cli``: a Typer app for one ``TaskEval``, with flags derived from its Parameters.

Everything here is offline -- the task under test is a stub coroutine, ``sweep`` is
replaced at its one lazy-import seam, and no test makes a network call.

The load-bearing pair:

* **Flags are derived.** A subclass declaring ``model_id`` and ``prompt_version``
  exposes ``--model-id`` and ``--prompt-version``; a *second* subclass declaring one
  more Parameter exposes one more flag with no edit to ``cli.py``. That second
  assertion is the anti-drift property itself -- a hand-maintained argparse block
  passes the first test and fails the second.
* **``--check`` runs preflight and stops.** One call, then exit, and the sweep is
  never even imported. A wiring failure otherwise reads as a measurement: every case
  raises, the sweep completes, and an empty result is cached as if it were a result.
"""
import re

import pytest

pytest.importorskip('pydantic_evals')

import typer                                                      # noqa: E402
from pydantic import BaseModel                                    # noqa: E402
from pydantic_evals import Case, Dataset                          # noqa: E402
from typer.testing import CliRunner                               # noqa: E402

import oryxflow                                                   # noqa: E402
import oryxflow.state                                             # noqa: E402
from oryxflow.evals import cli as cli_module                      # noqa: E402
from oryxflow.evals.cli import build_app, cli                     # noqa: E402
from oryxflow.evals.task import TaskEval                          # noqa: E402


runner = CliRunner()


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


def make_dataset(n=4):
    return Dataset(name='ds', cases=[Case(name='c{}'.format(i), inputs=Turn(q=i))
                                     for i in range(n)])


class _Stub:
    """A canned ``case()`` plus a call counter; `boom` makes it raise."""

    def __init__(self, boom=False):
        self.boom = boom
        self.calls = 0

    async def __call__(self, inputs):
        self.calls += 1
        if self.boom:
            raise RuntimeError('no credentials in this environment')
        return Reply(message='reply {}'.format(inputs.q), wrote=True)


def make_task(family, stub=None, **attrs):
    """A ``TaskEval`` subclass wired to a stub, with the given extra Parameters."""
    stub = stub if stub is not None else _Stub()
    body = dict(dataset=make_dataset(), retry_task=None, code_version='test')
    body.update(attrs)

    async def case(self, inputs):
        return await stub(inputs)

    body['case'] = case
    cls = type(family, (TaskEval,), body)
    cls.stub = stub
    return cls


class _Result:
    """What a stubbed ``sweep`` hands back: a frame and a verdict, nothing more."""

    def __init__(self, df=None):
        self.df = df
        self.verdicts = 0

    def verdict(self):
        self.verdicts += 1
        typer.echo('VERDICT stub')


class _SweepStub:
    """Stands in for ``ev.sweep``, recording its kwargs.

    It honours ``confirm=`` the way the real one must -- prompting before it
    spends anything -- so ``--yes`` can be tested as behaviour (no prompt) and
    not merely as a keyword that was passed along.
    """

    def __init__(self, result=None):
        self.calls = []
        self.result = result if result is not None else _Result()

    def __call__(self, task, **kwargs):
        self.calls.append((task, kwargs))
        if kwargs.get('confirm'):
            typer.confirm('132 calls (66 new) ~ $1.20. Run?', abort=True)
        typer.echo('SWEEP ran')
        return self.result

    @property
    def kwargs(self):
        return self.calls[-1][1]


@pytest.fixture
def sweep(monkeypatch):
    stub = _SweepStub()
    monkeypatch.setattr(cli_module, '_load_sweep', lambda: stub)
    return stub


@pytest.fixture
def no_sweep(monkeypatch):
    """Fail loudly if the sweep seam is touched at all."""
    def boom():
        raise AssertionError('sweep was loaded, but this run should not have')
    monkeypatch.setattr(cli_module, '_load_sweep', boom)


def flat(text):
    """Collapse the help renderer's wrapping so an assertion can be a plain string."""
    return re.sub(r'\s+', ' ', text)


# ------------------------------------------------------- 24. flags are derived

def test_flags_are_derived_from_the_tasks_parameters(env):
    cls = make_task('CliDerivedA',
                    model_id=oryxflow.Parameter(default='sonnet'),
                    prompt_version=oryxflow.Parameter(default='prod'))

    result = runner.invoke(build_app(cls), ['--help'])

    assert result.exit_code == 0
    out = flat(result.output)
    assert 'Usage:' in out
    assert '--model-id' in out
    assert '--prompt-version' in out
    # underscores become hyphens; the python name is never the flag
    assert '--model_id' not in out
    # and the built-ins are all there beside the derived ones
    for flag in ['--repeats', '--concurrency', '--reset', '--check', '--csv', '--yes']:
        assert flag in out, flag


def test_adding_a_parameter_adds_a_flag_with_no_cli_edit(env):
    """The anti-drift property: the flag list follows the task, not a hand-kept list."""
    before = make_task('CliDriftBefore',
                       model_id=oryxflow.Parameter(default='sonnet'))
    after = make_task('CliDriftAfter',
                      model_id=oryxflow.Parameter(default='sonnet'),
                      prompt_version=oryxflow.Parameter(default='prod'))

    out_before = flat(runner.invoke(build_app(before), ['--help']).output)
    out_after = flat(runner.invoke(build_app(after), ['--help']).output)

    assert '--prompt-version' not in out_before
    assert '--prompt-version' in out_after          # no edit to cli.py in between
    # and the new flag is live, not merely printed
    assert runner.invoke(build_app(before), ['--prompt-version', 'prod']).exit_code == 2


def test_a_derived_flag_is_repeatable_and_collects_every_value(env, sweep):
    cls = make_task('CliRepeatable', model_id=oryxflow.Parameter(default='sonnet'))

    result = runner.invoke(build_app(cls),
                           ['--model-id', 'sonnet', '--model-id', 'haiku', '--yes'])

    assert result.exit_code == 0, result.output
    assert sweep.kwargs['model_id'] == ['sonnet', 'haiku']


def test_an_unset_flag_is_not_passed_at_all(env, sweep):
    """No value means "leave it on its default", not "sweep over None"."""
    cls = make_task('CliUnset', model_id=oryxflow.Parameter(default='sonnet'))

    runner.invoke(build_app(cls), ['--yes'])

    assert 'model_id' not in sweep.kwargs


# --------------------------------------------------------- parameter typing

def test_choice_parameter_rejects_an_invalid_value_at_parse_time(env, no_sweep):
    """Free validation: the valid list, before anything is billed."""
    cls = make_task('CliChoice',
                    prompt_version=oryxflow.ChoiceParameter(
                        choices=['prod', 'preship'], default='prod'))

    result = runner.invoke(build_app(cls), ['--prompt-version', 'typo', '--yes'])

    assert result.exit_code == 2
    out = flat(result.output)
    assert 'prod' in out and 'preship' in out
    # the help shows the choices too, so the valid list is discoverable
    assert '<prod|preship>' in flat(runner.invoke(build_app(cls), ['--help']).output)


def test_numeric_parameters_arrive_typed_and_reject_junk(env, sweep):
    cls = make_task('CliNumeric',
                    top_k=oryxflow.IntParameter(default=3),
                    temperature=oryxflow.FloatParameter(default=0.0))

    result = runner.invoke(build_app(cls),
                           ['--top-k', '3', '--top-k', '5',
                            '--temperature', '0.7', '--yes'])
    assert result.exit_code == 0, result.output
    assert sweep.kwargs['top_k'] == [3, 5]
    assert sweep.kwargs['temperature'] == [0.7]

    bad = runner.invoke(build_app(cls), ['--top-k', 'three', '--yes'])
    assert bad.exit_code == 2
    assert 'not a valid int' in flat(bad.output)


def test_bool_parameter_is_a_two_valued_axis_not_a_switch(env, sweep):
    """`--strict true --strict false` is a two-arm sweep; a flag could not say that."""
    cls = make_task('CliBool', strict=oryxflow.BoolParameter(default=False))

    result = runner.invoke(build_app(cls),
                           ['--strict', 'true', '--strict', 'false', '--yes'])

    assert result.exit_code == 0, result.output
    assert sweep.kwargs['strict'] == [True, False]


def test_a_parameter_without_a_default_is_a_required_flag(env, no_sweep):
    cls = make_task('CliRequired', tag=oryxflow.Parameter())

    result = runner.invoke(build_app(cls), ['--yes'])

    assert result.exit_code == 2
    assert '--tag' in flat(result.output)


def test_a_parameter_colliding_with_a_builtin_flag_is_refused(env):
    cls = make_task('CliCollide', reset=oryxflow.Parameter(default='x'))

    with pytest.raises(ValueError, match='--reset'):
        build_app(cls)


# ------------------------------------------- 25. --check runs preflight only

def test_check_runs_preflight_only_and_exits(env, no_sweep):
    """One call answers "do my credentials work"; the sweep never starts."""
    stub = _Stub()
    cls = make_task('CliCheck', stub=stub, model_id=oryxflow.Parameter(default='sonnet'))

    result = runner.invoke(build_app(cls), ['--check'])

    assert result.exit_code == 0, result.output
    assert stub.calls == 1                       # the dataset has 4 cases
    assert 'preflight OK' in result.output
    assert 'SWEEP ran' not in result.output      # and `no_sweep` proves it never loaded


def test_check_uses_the_first_value_of_each_arm(env, no_sweep):
    cls = make_task('CliCheckArm', model_id=oryxflow.Parameter(default='sonnet'))

    instance = cli(cls, argv=['--check', '--model-id', 'haiku', '--model-id', 'opus'])

    assert instance.model_id == 'haiku'
    assert cls.stub.calls == 1


def test_check_reports_a_preflight_failure_and_exits_nonzero(env, no_sweep):
    stub = _Stub(boom=True)
    cls = make_task('CliCheckFail', stub=stub)

    result = runner.invoke(build_app(cls), ['--check'])

    assert result.exit_code == 1
    assert 'PREFLIGHT FAILED' in result.output
    assert 'no credentials in this environment' in result.output
    assert stub.calls == 1


def test_check_says_so_when_no_preflight_is_declared(env, no_sweep):
    stub = _Stub()
    cls = make_task('CliCheckNone', stub=stub, preflight=None)

    result = runner.invoke(build_app(cls), ['--check'])

    assert result.exit_code == 0, result.output
    assert 'no preflight declared' in result.output
    assert stub.calls == 0


# ------------------------------------------------------------ the cost gate

def test_yes_suppresses_the_confirmation(env, sweep):
    cls = make_task('CliYes')

    result = runner.invoke(build_app(cls), ['--yes'])

    assert result.exit_code == 0, result.output
    assert '132 calls' not in result.output      # nothing was asked
    assert sweep.kwargs['confirm'] is False
    assert 'SWEEP ran' in result.output


def test_without_yes_the_bill_is_shown_and_confirmation_is_required(env, sweep):
    cls = make_task('CliConfirm')

    declined = runner.invoke(build_app(cls), [], input='n\n')
    assert declined.exit_code == 1               # aborted, nothing spent
    assert '132 calls' in declined.output
    assert 'SWEEP ran' not in declined.output
    assert sweep.kwargs['confirm'] is True

    accepted = runner.invoke(build_app(cls), [], input='y\n')
    assert accepted.exit_code == 0, accepted.output
    assert 'SWEEP ran' in accepted.output


# ---------------------------------------------------------- the other built-ins

def test_builtin_flags_reach_the_sweep(env, sweep):
    cls = make_task('CliBuiltins')

    result = runner.invoke(build_app(cls),
                           ['--repeats', '3', '--concurrency', '8', '--reset', '--yes'])

    assert result.exit_code == 0, result.output
    assert sweep.kwargs['repeats'] == 3
    assert sweep.kwargs['concurrency'] == 8
    assert sweep.kwargs['reset'] is True


def test_builtin_defaults_come_from_the_tasks_own_parameters(env, sweep):
    cls = make_task('CliBuiltinDefaults')

    runner.invoke(build_app(cls), ['--yes'])

    assert sweep.kwargs['repeats'] == 1          # TaskEval.repeats default
    assert sweep.kwargs['concurrency'] == 4      # TaskEval.concurrency default


def test_csv_writes_the_resulting_frame(env, tmp_path, monkeypatch):
    pd = pytest.importorskip('pandas')
    frame = pd.DataFrame({'case_name': ['c0', 'c1'], 'wrote': [True, False]})
    stub = _SweepStub(result=_Result(df=frame))
    monkeypatch.setattr(cli_module, '_load_sweep', lambda: stub)

    out = tmp_path / 'cases.csv'
    cls = make_task('CliCsv')
    result = runner.invoke(build_app(cls), ['--yes', '--csv', str(out)])

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert list(pd.read_csv(out)['case_name']) == ['c0', 'c1']
    assert stub.result.verdicts == 1             # and the verdict still printed


# ----------------------------------------------------------------- the entry point

def test_cli_with_an_explicit_argv_returns_the_result(env, sweep):
    cls = make_task('CliArgv', model_id=oryxflow.Parameter(default='sonnet'))

    result = cli(cls, argv=['--model-id', 'haiku', '--yes'])

    assert result is sweep.result
    assert sweep.kwargs['model_id'] == ['haiku']


def test_cli_accepts_an_instance_as_well_as_a_class(env, sweep):
    cls = make_task('CliInstance', model_id=oryxflow.Parameter(default='sonnet'))

    cli(cls(), argv=['--yes'])

    assert sweep.calls[-1][0] is cls


def test_declining_the_bill_exits_clean_not_with_a_traceback(monkeypatch, capsys):
    """Choosing not to spend is the outcome the confirmation exists to make easy;
    a traceback there reads as 'something broke'."""
    import typer
    from oryxflow.evals import cli as cli_module

    def refuse(*a, **k):
        raise RuntimeError('sweep aborted before spending on 14 new calls')
    monkeypatch.setattr(cli_module, '_load_sweep', lambda: refuse)

    with pytest.raises(typer.Exit) as excinfo:
        cli_module._execute(make_task('AbortEval'), {}, None, None, False, False, None, False)
    assert excinfo.value.exit_code == 0
    assert 'aborted before spending' in capsys.readouterr().out


def test_a_real_runtime_error_still_propagates(monkeypatch):
    from oryxflow.evals import cli as cli_module

    def boom(*a, **k):
        raise RuntimeError('the provider exploded')
    monkeypatch.setattr(cli_module, '_load_sweep', lambda: boom)
    with pytest.raises(RuntimeError, match='provider exploded'):
        cli_module._execute(make_task('AbortEval'), {}, None, None, False, False, None, False)


def test_launching_from_another_directory_warns(tmp_path):
    from oryxflow.evals.cli import _warn_if_launched_elsewhere
    script = tmp_path / 'evals' / 'run_eval_x.py'
    script.parent.mkdir()
    script.write_text('')
    assert _warn_if_launched_elsewhere(str(script), cwd=script.parent) is None
    message = _warn_if_launched_elsewhere(str(script), cwd=tmp_path)
    assert message and 'not from its own directory' in message
    assert _warn_if_launched_elsewhere('-c', cwd=tmp_path) is None


def test_rescore_is_a_builtin_flag():
    from oryxflow.evals.cli import RESERVED_FLAGS
    assert 'rescore' in RESERVED_FLAGS
