"""Run an eval across a grid of arms, cached one cell per arm, and read the result.

The shortest thing that works has to be the cached thing, or nobody uses the cache.
``sweep()`` therefore takes a plain async function: no class, no scaffold, no
``set_dir``, six lines end to end -- and every cell is still one cached parquet, so a
second run of an unchanged arm costs nothing and editing the function re-runs only the
cells that function feeds.

``EvalResult`` is the reading half. ``.df`` is always there as the escape hatch;
``.verdict()`` prints the comparison with the four things a number alone cannot say --
whether the gap clears the noise floor, whether the winner broke a guardrail, whether the
metric is measuring the harness rather than the model, and whether the two rates even
share a denominator.
"""

from __future__ import annotations

import ast
import datetime
import hashlib
import inspect
import itertools
import json
import re
import sys
import textwrap
from pathlib import Path

import oryxflow
import oryxflow.codehash

from oryxflow.evals.stats import (RateCI, corrected_rate, delta_ci,
                                  judge_alignment, rate_ci)
from oryxflow.evals.task import TaskEval

NAN = float('nan')

# Attributes and parameters TaskEval already owns; an arm keyword of the same name would
# quietly become something other than an arm.
_TASK_ATTRS = ('dataset', 'metric', 'guardrail', 'slices', 'preflight', 'case',
               'code_version', 'persists', 'max_failure_rate', 'retry_task',
               'preview_chars', 'task_family', 'task_id')

_SLUG = re.compile(r'[^A-Za-z0-9.\-]+')


# --------------------------------------------------------------------------- sweep

def sweep(target, *, cases=None, dataset=None, metric=None, guardrail=None,
          slices=(), repeats=1, concurrency=4, reset=False, confirm=None,
          name=None, cost_per_call=None, watch=None, evaluators=(), **arms):
    """Run an eval across a grid of arms, cached one cell per arm.

    ``target`` is either a ``TaskEval`` subclass, or a plain async function taking
    ``(inputs, **arm_params)`` -- the function form builds the task for you, so a
    first eval needs no class, no scaffold and no set_dir, and is still cached::

        r = ev.sweep(run_turn, cases=ev.load_cases('cases.csv', inputs=Turn),
                     prompt_version=['prod', 'preship'], repeats=3,
                     metric=ev.Metric('yield', 'wrote'))
        r.verdict()

    Keyword arguments holding a **list** become the sweep axes -- one cached cell per
    combination. A scalar keyword is a fixed parameter applied to every arm. The
    function form passes them to ``target`` by name, and refuses a name ``target``
    cannot accept: a swept parameter the function ignores would run the same work N
    times and label the copies as if they were different arms.

    ``evaluators`` are pydantic-evals evaluators for the generated dataset -- how a
    case gets scored against its ``expected_output``, which the target never sees::

        r = ev.sweep(classify, cases=cases, evaluators=(EqualsExpected(),),
                     metric=ev.Metric('accuracy', 'EqualsExpected'), prompt=['v1', 'v2'])

    The synthesized task's ``code_version()`` folds in the source of ``target`` and the
    identity of the cases, so editing either re-runs the cells that depend on it and
    nothing else. That is what makes the cache safe to trust rather than something to
    remember to reset. Note it hashes ``target``'s OWN source: a helper it calls, or a
    prompt it renders, is covered by ``watch=`` below, not by this. (An unwatched module
    that moves is not silent -- the engine raises a ``StalenessWarning`` saying the
    cached output is being reused -- but it does not re-run on its own.)

    ``watch`` extends that to files ``target`` READS rather than contains -- a prompt
    template, a ``.sql`` file, a config. Pass glob patterns (or a callable returning
    any stable value) and editing a matched file re-runs the arms that read it::

        r = ev.sweep(run_turn, cases=cases, watch='prompts/*.md',
                     prompt_version=['prod', 'preship'])

    Without it a prompt lives outside the cache key, and editing one between two
    sweeps re-runs nothing -- which reads last week's numbers as this week's. A
    pattern matching no file raises rather than silently hashing to "unchanged".

    Patterns resolve against the CURRENT WORKING DIRECTORY, so an eval script that
    may be launched from anywhere should anchor them::

        HERE = pathlib.Path(__file__).parent
        watch=lambda: oryxflow.hash_files('prompts/*.md', root=HERE)

    Prints the projected call count, the cached/new split and an estimated cost before
    running. The cost is deliberately crude -- ``calls x cost_per_call``, and nothing at
    all unless you pass ``cost_per_call``; a built-in vendor price table would be wrong
    within a month.

    ``confirm`` asks before spending: ``True`` always, ``False`` never, ``None`` (the
    default) only when there are new cells and a terminal to answer on.

    Each cell is labelled by the axis values that actually VARY across the sweep, and
    every row is tagged with that label in an ``arm`` column. A case whose metadata
    already uses one of those names is refused before anything is spent, rather than
    shadowed -- see ``_check_metadata``.

    Returns an :py:class:`EvalResult`.
    """
    axes, fixed = _split_arms(arms, repeats)
    task_cls, dataset, arm_names = _resolve_target(
        target, cases, dataset, metric, guardrail, slices, axes, fixed, name, watch,
        evaluators)

    metric = metric if metric is not None else getattr(task_cls, 'metric', None)
    guardrail = guardrail if guardrail is not None else getattr(task_cls, 'guardrail', None)
    slices = tuple(slices) if slices else tuple(getattr(task_cls, 'slices', ()) or ())

    params = _grid(axes, fixed, repeats, concurrency)
    n_cases = len(getattr(dataset, 'cases', None) or [])
    label = name or task_cls.task_family
    _check_metadata(dataset, axes)

    flow, cached_flows = _split_cached(task_cls, params, reset)
    calls = {f: n_cases * int(p.get('repeats', 1)) for f, p in params.items()}
    cached_calls = sum(v for f, v in calls.items() if f in cached_flows)
    new_calls = sum(calls.values()) - cached_calls

    _print_bill(label, n_cases, params, calls, cached_calls, new_calls,
                cost_per_call, task_cls)
    _ask(label, new_calls, confirm, task_cls, params, cached_flows)

    df = oryxflow.runIterConcat(task_cls, params, reset=reset,
                                concat_fn=_tagger(list(axes)))
    return EvalResult(df, metric=metric, guardrail=guardrail, slices=slices,
                      arm_col='arm', name=label, n_cases=n_cases,
                      repeats=_common_repeats(params),
                      calls=sum(calls.values()), cached_calls=cached_calls,
                      new_calls=new_calls, excluded=getattr(cases, 'excluded', 0),
                      flow=flow, billed=True)


def _check_metadata(dataset, axes):
    """Refuse a case metadata key the sweep is about to tag over -- BEFORE spending.

    Every row is tagged with its axis values and an ``arm`` label naming the cell.
    A metadata key of the same name would be shadowed by that tag, so
    ``slices=('arm',)`` would silently break the metric down by the flow name
    instead of by the reader's own column, with no error and a plausible-looking
    table. The collision is knowable from the cases alone, so it is raised here
    rather than discovered in the output of a run that has already been paid for.
    """
    reserved = set(axes) | {'arm'}
    seen = set()
    for case in getattr(dataset, 'cases', None) or []:
        seen.update(getattr(case, 'metadata', None) or {})
    clash = sorted(seen & reserved)
    if clash:
        raise ValueError(
            "sweep: case metadata key(s) {} are also sweep labels, so the sweep's own "
            "tag would shadow them and any slice on that name would report the arm "
            "label instead of your column. Rename the metadata key (e.g. '{}' -> "
            "'{}_kind').".format(', '.join(repr(c) for c in clash), clash[0], clash[0]))


def _split_arms(arms, repeats):
    """List-valued keywords are the sweep axes; scalars are fixed for every arm."""
    axes, fixed = {}, {}
    for key, value in arms.items():
        if key in _TASK_ATTRS:
            raise ValueError(
                "sweep: '{}' is a TaskEval attribute, not an arm -- rename the "
                "parameter".format(key))
        if isinstance(value, (list, tuple)):
            if not len(value):
                raise ValueError("sweep: axis '{}' is empty".format(key))
            axes[key] = list(value)
        else:
            fixed[key] = value
    if isinstance(repeats, (list, tuple)):
        axes['repeats'] = list(repeats)
    return axes, fixed


def _grid(axes, fixed, repeats, concurrency):
    """``{flow_name: params}`` -- the cartesian product of the axes, named legibly."""
    names = list(axes)
    varying = {k for k in names if len(set(map(str, axes[k]))) > 1}
    # ...unless NOTHING varies: a lone cell named 'default' tells the reader less
    # than a verbose name, and there is no column width to save with one row.
    varying = varying or None
    combos = list(itertools.product(*[axes[k] for k in names])) if names else [()]
    out = {}
    for values in combos:
        pairs = dict(zip(names, values))
        flow = _flow_name(names, values, varying)
        while flow in out:
            flow += '_'
        row = dict(fixed)
        row.update(pairs)
        row.setdefault('concurrency', concurrency)
        if not isinstance(repeats, (list, tuple)):
            row.setdefault('repeats', repeats)
        out[flow] = row
    return out


def _plural(n, word):
    return '{} {}'.format(n, word if n == 1 else word + 's')


def _common_repeats(params):
    reps = {int(p.get('repeats', 1)) for p in params.values()}
    return reps.pop() if len(reps) == 1 else None


def _flow_name(names, values, varying=None):
    """Name a cell by what actually varies across the sweep.

    An axis with a single value is the same in every arm, so putting it in the
    name lengthens every row of every table without telling the reader anything.
    `model_id=['gemini-3.5-flash'], prompt_version=['prod','preship']` names the
    arms `prod` and `preship`, not `model_id_gemini-3.5-flash_prompt_version_prod`.
    """
    pairs = [(k, v) for k, v in zip(names, values)
             if varying is None or k in varying]
    if not pairs:
        return 'default'
    if len(pairs) == 1:
        return _slug(pairs[0][1])
    return '_'.join('{}_{}'.format(k, _slug(v)) for k, v in pairs)


def _slug(value):
    return _SLUG.sub('_', str(value)).strip('_') or 'x'


def _tagger(axis_names):
    """Tag each flow's rows with its axis values AND an ``arm`` column.

    The arm column is the flow name, so a multi-axis sweep still has one label to
    compare on and ``delta_ci`` has a column to pair by.

    A case metadata key of the same name is MOVED to ``case_<name>`` rather than
    overwritten. Overwriting silently destroys the reader's own column -- and
    ``arm`` is the word a real dataset reaches for first (an eval comparing an
    'outline' arm with a 'guidelines' arm tags every case with exactly that), so
    the collision is likely rather than exotic.
    """
    def concat_fn(identifier, params, df):
        df = df.copy()
        # snapshot first: an axis literally named `arm` writes the column the tag
        # then wants, and preserving THAT would invent a meta_arm from our own write
        original = set(df.columns)
        for key in axis_names:
            if key in params:
                _preserve(df, key, original)
                df[key] = params[key]
        _preserve(df, 'arm', original)
        df['arm'] = str(identifier)
        return df
    return concat_fn


def _preserve(df, name, original):
    """Copy a pre-existing ``name`` column aside before the tag overwrites it."""
    if name not in original or name not in df.columns:
        return
    target = 'meta_' + name
    while target in df.columns:
        target += '_'
    df[target] = df[name]


# ------------------------------------------------------------------ target -> task

def _resolve_target(target, cases, dataset, metric, guardrail, slices, axes, fixed, name,
                    watch=None, evaluators=()):
    arm_names = [k for k in list(axes) + list(fixed) if k != 'repeats']

    if isinstance(target, type) and issubclass(target, TaskEval):
        if cases is not None or dataset is not None:
            raise ValueError(
                'sweep: {} declares its own dataset -- pass cases=/dataset= only with '
                'the function form'.format(target.task_family))
        if target.dataset is None:
            raise ValueError('sweep: {} has no dataset'.format(target.task_family))
        if watch is not None:
            raise ValueError(
                'sweep: watch= applies to the function form -- {} declares its own '
                'code_version(), so fold the files in there'.format(target.task_family))
        if evaluators:
            raise ValueError(
                'sweep: evaluators= applies to the function form -- {} declares its own '
                'dataset, so put them on it'.format(target.task_family))
        return target, target.dataset, arm_names

    if not callable(target):
        raise TypeError(
            'sweep: target must be a TaskEval subclass or an async function taking '
            '(inputs, **arm_params), got {!r}'.format(target))

    dataset = (dataset if dataset is not None
               else _dataset(cases, name, target, evaluators))
    _check_signature(target, arm_names)
    return (_synthesize(target, dataset, axes, fixed, metric, guardrail, slices,
                        arm_names, name, watch),
            dataset, arm_names)


def _dataset(cases, name, target, evaluators=()):
    """Build the Dataset the function form runs.

    ``evaluators`` is here because the target sees only ``inputs``: scoring
    against a case's ``expected_output`` is the evaluator's job, and without a
    way to pass them the commonest eval shape of all -- a classifier graded on
    its expected label -- could not use the short form at all.
    """
    if cases is None:
        raise ValueError(
            'sweep: the function form needs cases= (see ev.load_cases) or dataset=')
    from pydantic_evals import Dataset
    return Dataset(name=name or getattr(target, '__name__', 'sweep'), cases=list(cases),
                   evaluators=list(evaluators))


def _check_signature(target, arm_names):
    """An arm the function cannot accept is a silent no-op: N identical cells, labelled
    as if they were different arms. Refuse it where it is written."""
    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError):
        return
    params = list(sig.parameters.values())
    if any(p.kind == p.VAR_KEYWORD for p in params):
        return
    accepted = {p.name for p in params
                if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    missing = [k for k in arm_names if k not in accepted]
    if missing:
        raise TypeError(
            'sweep: {}() does not accept {} -- a swept parameter the function ignores '
            'runs the same work for every arm and labels the copies as branches'.format(
                getattr(target, '__name__', target), ', '.join(sorted(missing))))


def _synthesize(target, dataset, axes, fixed, metric, guardrail, slices, arm_names, name,
                watch=None):
    """Build the TaskEval subclass the function form runs as.

    Parameters are the arm keys, ``case()`` calls ``target``, and ``code_version()``
    returns the source hash of ``target`` plus the identity of the cases -- so editing
    either invalidates exactly the cells that read it, with no version to bump.
    """
    body = {'dataset': dataset}
    if metric is not None:
        body['metric'] = metric
    if guardrail is not None:
        body['guardrail'] = guardrail
    if slices:
        body['slices'] = tuple(slices)

    for key, values in axes.items():
        if key != 'repeats':
            body[key] = _param_for(values[0])
    for key, value in fixed.items():
        body[key] = _param_for(value)

    digest = {'target': _source_hash(target), 'cases': _case_digest(dataset)}
    if watch is not None:
        digest['watch'] = _watch_digest(watch)
    body['code_version'] = lambda self: digest
    body['case'] = _make_case(target, arm_names)
    # the generated case() is always a coroutine, so the sync-ness of the target
    # it wraps has to be recorded here or it is unrecoverable downstream
    body['_sync_target'] = not inspect.iscoroutinefunction(inspect.unwrap(target))

    cls = type(name or _class_name(target), (TaskEval,), body)
    cls.__module__ = getattr(target, '__module__', __name__)
    return cls


def _class_name(target):
    """``run_turn`` -> ``RunTurn``: the cache directory is ``task_id.split('_')[0]``, so
    a family with an underscore in it would be filed under its first word."""
    raw = getattr(target, '__name__', 'Sweep')
    parts = [p for p in re.split(r'[^A-Za-z0-9]+', raw) if p]
    return ''.join(p[:1].upper() + p[1:] for p in parts) or 'Sweep'


def _param_for(value):
    if isinstance(value, bool):
        return oryxflow.BoolParameter(default=value)
    if isinstance(value, int):
        return oryxflow.IntParameter(default=value)
    if isinstance(value, float):
        return oryxflow.FloatParameter(default=value)
    if isinstance(value, dict):
        return oryxflow.DictParameter(default=value)
    if isinstance(value, (list, tuple)):
        return oryxflow.ListParameter(default=list(value))
    return oryxflow.Parameter(default=value)


def _make_case(target, arm_names):
    async def case(self, inputs):
        out = target(inputs, **{k: getattr(self, k) for k in arm_names})
        if inspect.isawaitable(out):
            out = await out
        return out
    return case


def _watch_digest(watch):
    """Fold files the target READS into its cache key.

    ``watch`` is a glob pattern, a list of them, or a callable returning any stable
    value. Globs go through ``oryxflow.hash_files``, which raises when a pattern
    matches nothing -- a typo'd path that hashed to "no files" would report every
    run as unchanged, which is the failure this exists to prevent.
    """
    if callable(watch):
        return watch()
    patterns = [watch] if isinstance(watch, str) else list(watch)
    return oryxflow.hash_files(*patterns)


def _source_hash(fn):
    """md5 over the function's normalized source -- what makes an edit invalidate."""
    fn = inspect.unwrap(fn)
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return 'nosource:{}.{}'.format(getattr(fn, '__module__', '?'),
                                       getattr(fn, '__qualname__', repr(fn)))
    try:
        tree = ast.parse(src)
        strip = getattr(oryxflow.codehash, '_strip_docstrings', None)
        if strip is not None:
            tree = strip(tree)
        blob = ast.dump(tree)
    except SyntaxError:
        blob = src
    return hashlib.md5(blob.encode('utf-8')).hexdigest()[:16]


def _case_digest(dataset):
    """Identity of the case set: add, remove or edit a case and the arms re-run.

    A value that cannot be serialized stably contributes NOTHING rather than its repr --
    a repr carrying a memory address would move the cache key every process, which costs
    real money on a sweep that should have been free.
    """
    parts = []
    for case in getattr(dataset, 'cases', None) or []:
        parts.append(str(getattr(case, 'name', '')))
        for attr in ('inputs', 'metadata', 'expected_output'):
            parts.append(_stable(getattr(case, attr, None)))
    return hashlib.md5('|'.join(parts).encode('utf-8')).hexdigest()[:16]


def _stable(obj):
    if obj is None:
        return ''
    dump = getattr(obj, 'model_dump_json', None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            pass
    try:
        return json.dumps(obj, sort_keys=True)
    except (TypeError, ValueError):
        return ''


# ------------------------------------------------------------------------ the bill

def _split_cached(task_cls, params, reset):
    """``(WorkflowMulti, {flow names already cached})``.

    WorkflowMulti has no ``complete()``; the per-flow ``Workflow`` it hands back does.
    """
    flow = oryxflow.WorkflowMulti(task_cls, params)
    cached = set()
    if reset:
        return flow, cached
    for fname in params:
        try:
            if flow.get_flow(fname).complete(task_cls):
                cached.add(fname)
        except Exception:
            pass
    return flow, cached


def _print_bill(label, n_cases, params, calls, cached_calls, new_calls,
                cost_per_call, task_cls):
    sym = _symbols()
    reps = _common_repeats(params)
    shape = '{} {} {} arms'.format(_plural(n_cases, 'case'), sym['times'], len(params))
    if reps is not None:
        shape += ' {} {}'.format(sym['times'], _plural(reps, 'rep'))
    lines = ['{}{}{} = {} calls'.format(label, sym['dot'], shape, sum(calls.values())),
             '  cached {}{}new {}'.format(cached_calls, sym['dot'], new_calls)]
    if new_calls and getattr(task_cls, 'preflight', None) is not None:
        lines[-1] += '   (+1 preflight call per new arm)'
    if cost_per_call is None:
        lines.append('  estimated cost: not estimated -- pass cost_per_call= for a crude '
                     'calls x cost_per_call figure')
    else:
        lines.append('  estimated cost: ~{:.2f} (crude: {} new calls x {})'.format(
            new_calls * float(cost_per_call), new_calls, cost_per_call))
    _emit(lines + [''])


def _ask(label, new_calls, confirm, task_cls=None, params=None, cached=()):
    """Confirm the bill -- offering the cheap probe AT the decision point.

    A ``--check`` flag only helps someone who already knows it exists, and the
    person who needs it most is running this eval for the first time. So the
    prompt offers it: `c` spends ONE call to prove the wiring, prints the result,
    and asks again. Nothing is spent before an answer, which is why the probe is
    offered here rather than run automatically before the bill -- a tool that
    bills money should not make its first call unasked, however cheap.
    """
    if not new_calls or confirm is False:
        return
    if confirm is None and not _interactive():
        return
    probe = _prober(task_cls, params, cached)
    prompt = 'Run {} new calls for {}? (y/n{}) '.format(
        new_calls, label, '/c=check one call first' if probe else '')
    while True:
        answer = input(prompt).strip().lower()
        if answer.startswith('y'):
            return
        if probe and answer.startswith('c'):
            probe()
            probe = None
            prompt = 'Run {} new calls for {}? (y/n) '.format(new_calls, label)
            continue
        raise RuntimeError(
            'sweep aborted before spending on {} new calls'.format(new_calls))


def _prober(task_cls, params, cached):
    """A callable that preflights the first UNCACHED arm, or None if there is none."""
    if task_cls is None or not params or getattr(task_cls, 'preflight', None) is None:
        return None
    todo = [f for f in params if f not in (cached or ())]
    if not todo:
        return None

    def probe():
        try:
            task_cls(**params[todo[0]]).preflight()
            _emit(['  preflight OK -- one call succeeded on arm {!r}.'.format(todo[0])])
        except Exception as exc:
            _emit(['  PREFLIGHT FAILED on arm {!r}: {}: {}'.format(
                todo[0], type(exc).__name__, exc)])
    return probe


def _interactive():
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except Exception:
        return False


# ---------------------------------------------------------------------- the result

class EvalResult:
    """The frame a sweep produced, and the questions a rate alone cannot answer.

    ``.df`` is the per-case frame tagged by arm -- always available, always the escape
    hatch. ``.verdict()`` prints the comparison, ``.report()`` writes it as markdown,
    ``.best()`` returns ``(arm, value, interval, clean)`` where ``clean`` is False if a
    guardrail budget broke.

    Constructible directly from a hand-built DataFrame, which is the point of the seam:
    every rule below is testable with no pydantic-evals object anywhere near it.
    """

    def __init__(self, df, metric=None, guardrail=None, slices=(), arm_col=None,
                 name=None, n_cases=None, repeats=None, calls=None, cached_calls=None,
                 new_calls=None, excluded=0, flow=None, billed=False):
        self.df = df
        self.metric = metric
        self.guardrail = guardrail
        self.slices = tuple(slices or ())
        self.name = name or 'eval'
        self.n_cases = n_cases
        self.repeats = repeats
        self.calls = calls
        self.cached_calls = cached_calls
        self.new_calls = new_calls
        self.excluded = excluded or 0
        self.flow = flow
        # sweep() already printed the shape and the cached/new split as the bill;
        # the verdict repeating them verbatim is just noise on a terminal.
        self.billed = billed
        self.arm_col = arm_col or ('arm' if 'arm' in list(df.columns) else None)
        self.arms = self._arms()

    # ---- frames

    def _arms(self):
        if not self.arm_col or self.arm_col not in self.df.columns:
            return []
        return list(dict.fromkeys(self.df[self.arm_col].tolist()))

    def _real(self, df=None):
        """Real cases only. A synthetic set written by the mind that wrote the prompt
        flatters it, so it never enters the headline."""
        df = self.df if df is None else df
        if 'synthetic' not in df.columns:
            return df
        return df[~df['synthetic'].astype('boolean').fillna(False)]

    def _synthetic(self):
        if 'synthetic' not in self.df.columns:
            return self.df.iloc[0:0]
        return self.df[self.df['synthetic'].astype('boolean').fillna(False)]

    def _arm_rows(self, arm, real=True):
        df = self._real() if real else self.df
        return df[df[self.arm_col] == arm]

    def _metrics(self):
        """Coverage first, then the primary metric, then the guardrail."""
        out = []
        if self.metric is not None and self.metric.coverage is not None:
            out.append((self.metric.coverage, 'coverage'))
        if self.metric is not None:
            out.append((self.metric, 'primary'))
        if self.guardrail is not None:
            out.append((self.guardrail, 'guardrail'))
        return out

    def _values(self, metric, real=True):
        out = {}
        for arm in self.arms:
            try:
                out[arm] = metric.compute(self._arm_rows(arm, real=real))
            except KeyError:
                out[arm] = (NAN, 0, None)
        return out

    # ---- public

    def best(self):
        """``(arm, value, interval, clean)`` -- ``clean`` is False if a guardrail broke."""
        self._need_metric()
        values = self._values(self.metric)
        arm = self._best_arm(values)
        if arm is None:
            return None, NAN, (NAN, NAN), False
        ci = self._ci(self.metric, arm)
        return arm, values[arm][0], (ci.low, ci.high), not self._guardrail_broken(arm)

    def verdict(self, print_it=True):
        """Print the comparison. Returns the text."""
        lines = self._lines()
        if print_it:
            _emit(lines)
        return '\n'.join(lines)

    def report(self, path=None):
        """Write the verdict as markdown. Returns the path."""
        self._need_metric()
        if path is None:
            path = Path('results') / '{}-{}.md'.format(
                datetime.date.today().isoformat(), _slug(self.name))
        path = Path(path)
        if path.parent != Path(''):
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._markdown(), encoding='utf-8')
        return path

    # ---- rendering

    def _need_metric(self):
        if self.metric is None:
            raise ValueError(
                'this result has no metric -- pass metric=ev.Metric(...) to sweep(), or '
                'read r.df directly')

    def _lines(self):
        self._need_metric()
        sym = _symbols()
        head = [] if self.billed else self._header(sym)
        out = head + self._notes() + self._usage() + self._dead_flags()
        for metric, role in self._metrics():
            out += [''] + self._block(metric, role, sym)
        out += [''] + self._verdict_lines() + self._slice_lines()
        return out

    def _header(self, sym):
        if self.calls is None:
            return ['{}{}{} rows{}{} arms'.format(
                self.name, sym['dot'], len(self.df), sym['dot'], len(self.arms))]
        shape = '{} {} {} arms'.format(
            _plural(self.n_cases, 'case'), sym['times'], len(self.arms))
        if self.repeats:
            shape += ' {} {}'.format(sym['times'], _plural(self.repeats, 'rep'))
        out = ['{}{}{} = {} calls'.format(self.name, sym['dot'], shape, self.calls)]
        if self.cached_calls is not None and not self.billed:
            out.append('  cached {}{}new {}'.format(
                self.cached_calls, sym['dot'], self.new_calls))
        return out

    def _notes(self):
        out = []
        if self.excluded:
            out.append('  {} holdout case(s) excluded from every arm -- a case whose text '
                       'is in the prompt scores against its own answer key.'.format(
                           self.excluded))
        failed = self._failures()
        if failed:
            out.append(
                '  {} case(s) failed and are excluded from every rate.'.format(failed))
        broken = self._evaluator_errors()
        if broken:
            out.append(
                '!! {} evaluator call(s) RAISED, leaving their columns empty. An '
                'evaluator that crashed looks exactly like one with nothing to '
                'score, so check this before reading any rate below: {}'.format(
                    broken[0], broken[1]))
        return out

    def _evaluator_errors(self):
        """``(count, first message)`` for evaluators that raised, or None."""
        df = self.df
        if 'evaluator_errors' not in df.columns:
            return None
        total = int(df['evaluator_errors'].fillna(0).sum())
        if not total:
            return None
        messages = df.get('evaluator_error')
        first = ''
        if messages is not None:
            hits = [m for m in messages.fillna('').tolist() if m]
            first = hits[0][:160] if hits else ''
        return total, first

    # Conventional names a task can fill with `increment_eval_metric(...)`. Reported
    # by name rather than inferred from any numeric column: guessing which column is
    # a token count would eventually sum something that is not one.
    USAGE_COLUMNS = ('input_tokens', 'output_tokens', 'total_tokens', 'cost')

    def _usage(self):
        """What the run actually used, when the task recorded it.

        The bill before a run can only ever be an estimate; this is the measured
        thing, and it is here because the frame now carries whatever
        ``increment_eval_metric`` put on each case.
        """
        parts = []
        for col in self.USAGE_COLUMNS:
            if col not in self.df.columns:
                continue
            try:
                total = self.df[col].fillna(0).sum()
            except Exception:
                continue
            if not total:
                continue
            # a token count in scientific notation ('7.356e+04') is unreadable as
            # the thing it is; whole numbers stay whole
            parts.append('{} {}'.format(
                col.replace('_', ' '),
                '{:,.0f}'.format(total) if float(total).is_integer()
                else '{:,.2f}'.format(total)))
        if not parts:
            return []
        return ['  measured usage: ' + ', '.join(parts)]

    def _failures(self):
        if 'error' not in self.df.columns:
            return 0
        return int((self.df['error'].fillna('') != '').sum())

    def _dead_flags(self):
        """A metric identically 0% or 100% in every arm usually measures the harness.

        Printed ABOVE the tables on purpose: if it fires, nothing below it is worth
        reading. This is the check that catches a display cap or a broken column reaching
        the scorer, which silently invalidates a whole sweep.
        """
        out = []
        if len(self.arms) < 2:
            return out + self._saturated()
        for metric, role in self._metrics():
            values = [v for v, n, _num in self._values(metric).values() if n]
            if len(values) < 2 or any(v != v for v in values):
                continue
            # A guardrail identical and INSIDE budget in every arm is the outcome it
            # exists to confirm -- no control was violated anywhere. Calling that
            # "probably measures the harness" trains the reader to skip the flag.
            if role == 'guardrail' and not metric.over_budget(values[0]):
                continue
            if len(set(values)) == 1 and values[0] in (0.0, 1.0):
                out.append(
                    "!! {}: identically {} in all arms -- this usually measures the "
                    "harness, not the model. Check the metric's inputs before reading "
                    "anything else.".format(metric.label.upper(), _pct(values[0])))
        return out

    def _saturated(self):
        """A single arm scoring 100% is usually a case set that stopped being hard.

        Only for a lone arm: with two, the dead-metric check already covers it. A
        perfect score is not evidence the system is good, it is evidence the cases
        no longer separate anything.
        """
        if self.metric is None:
            return []
        value, n, _num = self._values(self.metric).get(
            self.arms[0], (NAN, 0, None)) if self.arms else (NAN, 0, None)
        if n < 5 or value != value:
            return []
        perfect = value == 1.0 if self.metric.higher_is_better else value == 0.0
        if not perfect:
            return []
        return ['!! {} is perfect on all {} cases. A saturated eval has stopped '
                'discriminating -- it cannot show a regression or an improvement '
                'from here. Add harder cases rather than reading this as a '
                'result.'.format(self.metric.label.upper(), n)]

    def _block(self, metric, role, sym):
        values = self._values(metric)
        if role == 'guardrail':
            head = '{} (guardrail{}budget {})'.format(
                metric.label.upper(), sym['dot'], _pct(metric.budget))
        elif role == 'coverage':
            head = '{} (coverage)'.format(metric.label.upper())
        else:
            head = '{} ({})'.format(
                metric.label.upper(),
                'higher is better' if metric.higher_is_better else 'lower is better')
        out = [head]

        width = max([len(str(a)) for a in self.arms] or [1])
        methods = set()
        for arm in self.arms:
            value, n, numerator = values[arm]
            ci = self._ci(metric, arm)
            methods.add(ci.method)
            row = '  {:<{w}}  {:>4}  ({}/{})'.format(
                arm, _pct(value), '?' if numerator is None else numerator, n, w=width)
            if ci.method != 'empty':
                row += '   95% CI [{}, {}]'.format(_pct(ci.low), _pct(ci.high))
            if role == 'guardrail' and metric.over_budget(value):
                row += '   over budget'
            out.append(row)
        out += self._budget_edge(metric, role, values)

        out += (self._unmeasured_line(metric) + self._judge_lines(metric, values)
                + self._denominator_line(values))
        if role == 'primary':
            out += self._delta_line(metric, values, sym) + self._synthetic_line(metric)
        if role == 'coverage':
            out.append('  read this first -- a quality rate over a shrinking subset '
                       'improves as the subset shrinks, so it is never read alone.')
        caveats = sorted(methods - {'bca', 'empty'})
        if caveats:
            out.append('  interval from a {} fallback: every case agreed, so the '
                       'bootstrap has nothing to resample.'.format(caveats[0]))
        return out

    def _unmeasured_line(self, metric):
        """Rows the metric applied to but that carry no verdict.

        They are out of the rate (unmeasured is not failed), so they have to be
        stated: a judge that graded a third of the rows would otherwise report a
        confident number over a subset nobody was told about.
        """
        try:
            missing = sum(metric.unmeasured(self._arm_rows(arm)) for arm in self.arms)
            total = sum(len(metric.eligible(self._arm_rows(arm))) for arm in self.arms)
        except Exception:
            return []
        if not missing:
            return []
        if missing == total:
            return ['  NOT MEASURED: no row has a {} verdict, so there is no rate here '
                    '-- not a 0%. Check that whatever fills it actually ran.'.format(
                        metric.label)]
        return ['  {} of {} eligible rows carry no {} verdict and are OUT of the rate '
                '(unmeasured is not failed) -- the rate is over the {} that were.'.format(
                    missing, total, metric.label, total - missing)]

    def _budget_edge(self, metric, role, values):
        """A guardrail whose point estimate clears its budget but whose interval
        does not. The bound is what to act on: at n=12, `0% (0/12)` is entirely
        consistent with a true rate above a 5% budget."""
        if role != 'guardrail' or metric.budget is None:
            return []
        for arm in self.arms:
            value = values[arm][0]
            if value != value or metric.over_budget(value):
                continue
            ci = self._ci(metric, arm)
            bound = ci.low if metric.higher_is_better else ci.high
            if bound == bound and metric.over_budget(bound):
                return ['  within budget on the point estimate, but the interval '
                        'for {} reaches {} -- at this sample size the guardrail is '
                        'not demonstrated, only not yet violated.'.format(
                            arm, _pct(bound))]
        return []

    def _judge_lines(self, metric, values):
        """How far the judge can be trusted, and the rate corrected for it.

        Printed with the metric it qualifies rather than as a footnote: an
        uncorrected rate from an unvalidated judge is the number people quote.
        """
        if not getattr(metric, 'human', None):
            return []
        column = metric.column if isinstance(metric.column, str) else None
        if column is None or metric.human not in self.df.columns:
            return []
        try:
            align = judge_alignment(self._real(), column, metric.human)
        except KeyError:
            return []
        if not align.n:
            return []
        out = ['  judge vs {} human label(s):  TPR {}  TNR {}  kappa {:.2f}  '
               '(agreement {} -- read kappa, not this)'.format(
                   align.n, _pct(align.tpr), _pct(align.tnr), align.kappa,
                   _pct(align.agreement))]
        if align.note:
            out.append('  judge caveat: {}'.format(align.note))
        if not align.informative:
            out.append('  NOT CORRECTED: the judge carries no usable signal against '
                       'those labels, so its rate above stands uncorrected -- and '
                       'should not be trusted.')
            return out
        for arm in self.arms:
            raw = values[arm][0]
            fixed = corrected_rate(raw, align)
            if fixed == fixed:
                out.append('  {} corrected for judge error: {} -> {}'.format(
                    arm, _pct(raw), _pct(fixed)))
        return out

    def _denominator_line(self, values):
        """A rate over a filtered subset is gameable by shrinking the subset, and this
        is the only automatic defence against it."""
        ns = [n for _v, n, _num in values.values()]
        if len(ns) < 2 or min(ns) <= 0:
            return []
        if (max(ns) - min(ns)) / float(max(ns)) <= 0.2:
            return []
        return ['  denominator moved (n={} vs n={}) -- this rate is not directly '
                'comparable across arms; read the coverage metric first.'.format(
                    max(ns), min(ns))]

    def _delta_line(self, metric, values, sym):
        pair = self._pair(values)
        if pair is None:
            return []
        delta = self._delta(metric, pair[0], pair[1])
        if delta is None:
            return []
        tail = 'inside noise' if delta.spans_zero else 'outside noise'
        return ['  {}  {} {} {}  = {}  [{}, {}]   {}'.format(
            sym['delta'], pair[0], sym['minus'], pair[1], _pp(delta.value),
            _pp(delta.low), _pp(delta.high), tail)]

    def _synthetic_line(self, metric):
        synth = self._synthetic()
        if not len(synth):
            return []
        value, n, numerator = metric.compute(synth)
        return ['  synthetic n={}  {} ({}/{}) -- reported beside the headline, never in '
                'it.'.format(n, _pct(value), numerator, n)]

    def _verdict_lines(self):
        values = self._values(self.metric)
        arm = self._best_arm(values)
        if arm is None:
            return ['VERDICT  no arm produced a usable rate.']
        if len(self.arms) < 2:
            return ['VERDICT  single arm: {} at {} -- nothing to compare it with.'.format(
                arm, _pct(values[arm][0]))]

        pair = self._pair(values)
        delta = self._delta(self.metric, pair[0], pair[1]) if pair else None

        if delta is None:
            body = ('the arms share no case, so nothing can be compared -- run the same '
                    'cases in every arm.')
        elif delta.spans_zero:
            # No winner is named: at this sample size the gap is indistinguishable from
            # the run-to-run wobble, and naming one would be reporting noise as a result.
            body = ('{} vs {} = {} [{}, {}], inside noise at {} -- raise repeats '
                    'before calling this real.'.format(
                        pair[0], pair[1], _pp(delta.value), _pp(delta.low),
                        _pp(delta.high),
                        _plural(self.repeats, 'rep') if self.repeats else '? reps'))
        elif self._guardrail_broken(arm):
            body = ('{} improves the primary metric ({}, outside noise) but breaks the '
                    '{} guardrail ({} > {}). Not a clean win.'.format(
                        arm, _pp(delta.value), self.guardrail.label,
                        _pct(self._values(self.guardrail)[arm][0]),
                        _pct(self.guardrail.budget)))
        else:
            body = '{} wins the primary metric ({}, outside noise).'.format(
                arm, _pp(delta.value))
        return _wrap('VERDICT  ', body)

    def _slice_lines(self):
        if not self.slices:
            return []
        values = self._values(self.metric)
        arm = self._best_arm(values)
        if arm is None:
            return []
        rows = self._arm_rows(arm)
        found = []
        for col in self.slices:
            if col not in rows.columns:
                continue
            for value in dict.fromkeys(rows[col].tolist()):
                group = rows[rows[col] == value]
                rate, n, numerator = self.metric.compute(group)
                if n and rate == rate:
                    found.append((rate, '{}={} {} ({}/{})'.format(
                        col, value, _pct(rate), numerator, n)))
        if not found:
            return []
        found.sort(key=lambda x: x[0], reverse=not self.metric.higher_is_better)
        # Only slices genuinely below the arm's own rate. Listing a 100% slice
        # under "weakest" reads as a warning about something that is fine.
        overall = values[arm][0]
        if overall == overall:
            found = [f for f in found
                     if (f[0] < overall if self.metric.higher_is_better
                         else f[0] > overall)]
        if not found:
            return []
        return ['', 'Weakest slices for {}:  {}'.format(
            arm, '  '.join(text for _r, text in found[:3]))]

    # ---- computation

    def _ci(self, metric, arm):
        try:
            return rate_ci(self._arm_rows(arm), metric)
        except Exception:
            return RateCI(NAN, NAN, NAN, 0, 0, None, 'empty')

    def _usable(self, values):
        usable = [(a, v) for a, (v, n, _num) in values.items() if n and v == v]
        usable.sort(key=lambda x: x[1], reverse=self.metric.higher_is_better)
        return usable

    def _best_arm(self, values):
        usable = self._usable(values)
        return usable[0][0] if usable else None

    def _pair(self, values):
        """Best arm and its runner-up: the only comparison a verdict needs to name."""
        usable = self._usable(values)
        return (usable[0][0], usable[1][0]) if len(usable) > 1 else None

    def _delta(self, metric, arm_a, arm_b):
        try:
            return delta_ci(self._real(), metric, arm_a, arm_b, arm_col=self.arm_col)
        except Exception:
            return None

    def _guardrail_broken(self, arm):
        if self.guardrail is None:
            return False
        value = self._values(self.guardrail).get(arm, (NAN, 0, None))[0]
        return self.guardrail.over_budget(value)

    # ---- markdown

    def _markdown(self):
        sym = _ASCII
        out = ['# {}'.format(self.name), '']
        out += [line.strip() for line in self._header(sym)] + ['']
        for note in self._notes() + self._dead_flags():
            out.append('> {}'.format(note.strip()))
        out += ['']
        for metric, role in self._metrics():
            block = self._block(metric, role, sym)
            out += ['## {}'.format(block[0]), '',
                    '| arm | rate | n | 95% CI |', '| --- | --- | --- | --- |']
            values = self._values(metric)
            for arm in self.arms:
                value, n, numerator = values[arm]
                ci = self._ci(metric, arm)
                out.append('| {} | {} | {}/{} | [{}, {}] |'.format(
                    arm, _pct(value), '?' if numerator is None else numerator, n,
                    _pct(ci.low), _pct(ci.high)))
            out += ['']
            out += [line.strip() for line in block[1 + len(self.arms):]]
            out += ['']
        out += ['## Verdict', '',
                '**{}**'.format(' '.join(l.strip() for l in self._verdict_lines()))]
        out += [line.strip() for line in self._slice_lines() if line.strip()]
        return '\n'.join(out) + '\n'


# ------------------------------------------------------------------------ printing

_FANCY = {'dot': ' · ', 'times': '×', 'delta': 'Δ', 'minus': '−'}
_ASCII = {'dot': ' | ', 'times': 'x', 'delta': 'D', 'minus': '-'}


def _symbols():
    """Decorative characters only where the stream can carry them.

    Every sentence a reader acts on is plain ASCII, so a legacy console never changes
    what the verdict SAYS -- only how the header separators look.
    """
    enc = getattr(sys.stdout, 'encoding', None) or 'ascii'
    try:
        ''.join(_FANCY.values()).encode(enc)
    except (LookupError, UnicodeEncodeError, TypeError):
        return _ASCII
    return _FANCY


def _emit(lines):
    console = None
    try:
        from rich.console import Console
        console = Console(width=110, highlight=False, soft_wrap=True)
    except Exception:
        pass
    for line in lines:
        if console is None:
            print(line)
        else:
            # markup off: a confidence interval renders as [94%, 100%], which Rich would
            # otherwise read as a style tag and swallow.
            console.print(line, markup=False, highlight=False)


def _wrap(prefix, body, width=88):
    pad = ' ' * len(prefix)
    wrapped = textwrap.wrap(body, width=width - len(prefix)) or ['']
    return [prefix + wrapped[0]] + [pad + line for line in wrapped[1:]]


def _pct(value):
    if value is None or value != value:
        return 'n/a'
    return '{:.0f}%'.format(round(value * 100))


def _pp(value):
    if value is None or value != value:
        return 'n/a'
    return '{:+.0f}pp'.format(value * 100)
