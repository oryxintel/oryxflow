"""A command line for one ``TaskEval``, with its flags DERIVED from its Parameters.

The point of deriving rather than declaring: a hand-maintained ``argparse`` block
drifts from the task the moment a parameter is added. Adding
``model_id = oryxflow.Parameter()`` to the task adds ``--model-id`` here, with the
right value type and the right required/optional status, and there is no second
place to forget to edit.

Each declared Parameter becomes a **repeatable** option, because repeating a flag
is how a sweep axis is declared::

    python my_eval.py --model-id sonnet --model-id haiku --repeats 3

Built-in flags on top of the derived ones: ``--repeats``, ``--concurrency``,
``--reset``, ``--check``, ``--csv``, ``--yes``.
"""

import datetime
import enum
import inspect
import pathlib
import traceback
from typing import List, Optional

import typer

from oryxflow.parameter import (
    BoolParameter,
    ChoiceParameter,
    DateParameter,
    DictParameter,
    EnumParameter,
    FloatParameter,
    IntParameter,
    ListParameter,
)

# Declared on TaskEval itself and already carried by a built-in flag, so they are
# not derived a second time.
BUILTIN_PARAMS = ('repeats', 'concurrency')

# Flag names the built-ins own. A declared Parameter that would collide is a hard
# error here rather than a flag that silently shadows another one.
RESERVED_FLAGS = {
    'repeats', 'concurrency', 'reset', 'check', 'csv', 'yes', 'help'}


def _load_sweep():
    """Import ``sweep`` lazily, so ``--check`` never needs the sweep machinery.

    Kept as a function rather than a module-scope import for two reasons: a
    credentials probe should not pay for anything it does not use, and a test
    can replace this one seam to exercise the CLI without running a sweep.
    """
    from oryxflow.evals.sweep import sweep
    return sweep


def _flag(name):
    """``model_id`` -> ``--model-id``."""
    return '--' + name.replace('_', '-')


def _choices_enum(name, values):
    """An Enum over ``values``, which is how Typer renders a choice.

    Member names are positional (``v0``, ``v1``) because a choice like ``gpt-4``
    is not an identifier; the *value* is what the user types and what comes
    back, so the names are never seen.
    """
    seen = list(dict.fromkeys(values))
    members = dict(('v{}'.format(i), v) for i, v in enumerate(seen))
    return enum.Enum('{}Choices'.format(name.title().replace('_', '')), members)


def _element_type(name, param):
    """``(annotation element type, coercer)`` for one declared Parameter.

    The element type is what Typer validates at parse time -- an out-of-range
    choice or a non-integer then fails with the valid list before a single call
    is billed, instead of surfacing as an exception partway through a paid
    sweep. The coercer turns what Click hands back into the value the engine's
    Parameter expects, using that Parameter's own ``parse``, so the two cannot
    disagree about what a value means.
    """
    if isinstance(param, ChoiceParameter):
        return _choices_enum(name, list(param._choices)), lambda v: v.value
    if isinstance(param, EnumParameter):
        # the engine serializes an enum by NAME, so the CLI takes names too
        return (_choices_enum(name, [m.name for m in param._enum]),
                lambda v: param.parse(v.value))
    if isinstance(param, BoolParameter):
        # `--flag true --flag false` is a two-arm sweep; a bare on/off switch
        # could not express that, so a bool is a two-valued choice, not a flag.
        return _choices_enum(name, ['true', 'false']), lambda v: param.parse(v.value)
    if isinstance(param, IntParameter):
        return int, lambda v: v
    if isinstance(param, FloatParameter):
        return float, lambda v: v
    if isinstance(param, DateParameter):
        return datetime.datetime, lambda v: v.date()
    if isinstance(param, (DictParameter, ListParameter)):
        return str, param.parse                      # JSON text
    return str, lambda v: v


def _option_help(param):
    """Help for a derived flag: its description, its default, and that it repeats."""
    bits = []
    if param.description:
        bits.append(param.description)
    if param.has_task_value():
        try:
            bits.append('default: {}'.format(param.task_value()))
        except Exception:
            pass
    bits.append('repeatable: each value is one arm of the sweep')
    return '; '.join(bits)


def _task_class(task):
    """Accept either a TaskEval subclass or an instance of one."""
    return task if isinstance(task, type) else type(task)


def _param_default(cls, name, fallback):
    """The declared default of one of the task's own Parameters."""
    param = dict(cls.get_params()).get(name)
    if param is None or not param.has_task_value():
        return fallback
    return param.task_value()


def _derived_params(cls):
    """``[(name, Parameter)]`` this CLI turns into flags -- the whole derivation."""
    derived = [(n, p) for n, p in cls.get_params() if n not in BUILTIN_PARAMS]
    for name, _param in derived:
        if name in RESERVED_FLAGS:
            raise ValueError(
                "{}: parameter '{}' collides with the built-in {} flag; "
                "rename the parameter".format(cls.__name__, name, _flag(name)))
    return derived


def check(task, arms=None, repeats=None, concurrency=None):
    """Run the task's ``preflight()`` once and return the instance it probed.

    This is what ``--check`` does. It exists because a wiring failure otherwise
    reads as a measurement: run from the wrong working directory and every case
    raises, the sweep completes, and an empty result is cached as if it were a
    result. One call answers "do my credentials work" before anything is billed.
    """
    cls = _task_class(task)
    kwargs = dict((name, values[0]) for name, values in (arms or {}).items() if values)
    if repeats is not None:
        kwargs['repeats'] = repeats
    if concurrency is not None:
        kwargs['concurrency'] = concurrency
    instance = cls(**kwargs)
    if instance.preflight is None:
        typer.echo('{}: no preflight declared, nothing to check.'.format(cls.__name__))
        return instance
    instance.preflight()
    typer.echo('{}: preflight OK.'.format(cls.__name__))
    return instance


def _execute(cls, arms, repeats, concurrency, reset, do_check, csv_path, yes):
    """The command body, kept out of the generated signature so it stays readable."""
    if do_check:
        # --check short-circuits BEFORE `sweep` is imported: the probe must not
        # depend on, or pay for, the machinery it is checking the way into.
        try:
            return check(cls, arms, repeats=repeats, concurrency=concurrency)
        except Exception as exc:
            typer.echo('{}: PREFLIGHT FAILED: {}'.format(cls.__name__, exc), err=True)
            typer.echo(traceback.format_exc(), err=True)
            raise typer.Exit(code=1)

    sweep = _load_sweep()
    # `confirm` is the bill: sweep prints the projected calls, the cached/new
    # split and the cost estimate, and asks only when uncached cells exist.
    # --yes answers that question in advance; it never skips the print.
    try:
        result = sweep(cls, repeats=repeats, concurrency=concurrency, reset=reset,
                       confirm=not yes, **arms)
    except RuntimeError as exc:
        # Declining the bill is a decision, not a failure: say so in one line and
        # exit clean. A traceback here reads as "something broke" for the one
        # outcome the confirmation exists to make easy.
        if 'aborted before spending' not in str(exc):
            raise
        typer.echo(str(exc))
        raise typer.Exit(code=0)

    frame = getattr(result, 'df', None)
    if csv_path is not None and frame is not None:
        frame.to_csv(str(csv_path), index=False)
        typer.echo('wrote {} rows to {}'.format(len(frame), csv_path))

    verdict = getattr(result, 'verdict', None)
    if callable(verdict):
        verdict()
    return result


def build_app(task):
    """The Typer app for ``task``, with one repeatable option per declared Parameter.

    Separate from :func:`cli` so a caller (or a test) can hold the app itself --
    to mount it under a larger Typer application, or to drive it with
    ``typer.testing.CliRunner``.
    """
    cls = _task_class(task)
    derived = _derived_params(cls)

    coercers = {}
    signature = []
    for name, param in derived:
        element, coerce = _element_type(name, param)
        coercers[name] = coerce
        required = not param.has_task_value()
        signature.append(inspect.Parameter(
            name, inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option(
                ... if required else None, _flag(name),
                help=_option_help(param), show_default=False),
            annotation=List[element] if required else Optional[List[element]]))

    def builtin(pyname, flag, annotation, default, help):
        # the python name is prefixed so a derived parameter can never collide
        # with a built-in inside the generated signature
        return inspect.Parameter(
            'opt_' + pyname, inspect.Parameter.KEYWORD_ONLY,
            default=typer.Option(default, flag, help=help), annotation=annotation)

    signature += [
        builtin('repeats', '--repeats', int, _param_default(cls, 'repeats', 1),
                'runs per case; more repeats narrow the noise band'),
        builtin('concurrency', '--concurrency', int,
                _param_default(cls, 'concurrency', 4),
                'cases in flight at once; affects speed only, never the result'),
        builtin('reset', '--reset', bool, False,
                'discard the cached cells for these arms and re-run them'),
        builtin('check', '--check', bool, False,
                'run preflight() only and exit: one call, to prove credentials work'),
        builtin('csv', '--csv', Optional[pathlib.Path], None,
                'write the resulting per-case frame to this path'),
        builtin('yes', '--yes', bool, False,
                'answer the cost confirmation in advance'),
    ]

    def main(**kwargs):
        arms = {}
        for name, _param in derived:
            values = kwargs.get(name) or []
            if values:
                arms[name] = [coercers[name](v) for v in values]
        return _execute(
            cls, arms,
            repeats=kwargs['opt_repeats'], concurrency=kwargs['opt_concurrency'],
            reset=kwargs['opt_reset'], do_check=kwargs['opt_check'],
            csv_path=kwargs['opt_csv'], yes=kwargs['opt_yes'])

    doc = (cls.__doc__ or '').strip().splitlines()
    main.__doc__ = doc[0] if doc else 'Run the {} eval.'.format(cls.__name__)
    main.__name__ = cls.__name__
    # Typer reads the signature and the annotations, so building those is what
    # makes the flags derived rather than declared.
    main.__signature__ = inspect.Signature(signature)
    main.__annotations__ = dict((p.name, p.annotation) for p in signature)

    app = typer.Typer(add_completion=False, no_args_is_help=False)
    app.command()(main)
    return app


def cli(task, argv=None):
    """A Typer app for one TaskEval, with flags DERIVED from its Parameters.

    Each declared Parameter becomes a repeatable option (``--model-id`` for
    ``model_id``), so adding a parameter adds a flag and the two cannot drift.
    Built-ins: --repeats --concurrency --reset --check --csv --yes.

    ``--check`` runs ``preflight()`` only and exits. ``--yes`` answers the cost
    confirmation in advance; without it the projected call count and cost are
    printed and confirmation is required when uncached cells exist.

    ``argv=None`` reads ``sys.argv`` and behaves like a command line: usage
    errors print and exit. Passing a list runs that instead and RETURNS the
    result, which is what makes it testable::

        if __name__ == '__main__':
            ev.cli(MyEval)
    """
    command = typer.main.get_command(build_app(task))
    return command(args=argv, standalone_mode=argv is None)
