"""One cell of an eval matrix, in two cached stages: the model calls, then the scoring.

A cell is two tasks, split the standard oryxflow way -- the expensive, un-replayable
step apart from the cheap one that is safe to re-run:

    <Eval>Outputs (TaskJson)      calls case() for every case x repeat; saves the raw
                                  outputs. Identity: the arm (``code_version()``), the
                                  code ``case()`` runs, the case set.
      <Eval> (TaskPqPandas)       runs the evaluators on those stored outputs; saves the
                                  per-case frame. Identity: the evaluators
                                  (``scorer_version()``).

Edit a scorer -- or fix a judge rubric -- and only the scoring stage re-runs: zero model
calls. Edit a prompt and both re-run, because a dependency's new output moves its
dependents. You still write ONE class; the outputs task is generated from it.
"""

import ast
import asyncio
import copy
import dataclasses
import hashlib
import importlib
import inspect
import json
import re
import textwrap
import warnings

import oryxflow
import oryxflow.codehash
import oryxflow.tasks


# A retry policy is worth having by default -- a 429 is not a verdict -- but it is
# optional plumbing: `RetryConfig` lives in pydantic-ai-slim, not pydantic-evals, and
# that module raises ImportError when tenacity is absent. Neither may break
# `import oryxflow.evals`, so the default degrades to "no retries".
try:
    from pydantic_ai.retries import RetryConfig as _RetryConfig
    from tenacity import stop_after_attempt, wait_exponential

    DEFAULT_RETRY = _RetryConfig(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=20))
except Exception:
    _RetryConfig = None
    DEFAULT_RETRY = None


NAN = float('nan')

# Bump when the stored outputs record changes shape (forces model calls), or when
# _to_frame's schema changes (forces a re-score only -- never a model call).
OUTPUTS_VERSION = 1
FRAME_VERSION = 2

# Evaluators that read the OpenTelemetry span tree of the run. Spans are not stored,
# so these score in the outputs stage, while the spans still exist.
_TRACE_EVALUATORS = frozenset((
    'HasMatchingSpan', 'ToolCorrectness', 'TrajectoryMatch', 'ArgumentCorrectness',
    'MaxToolCalls', 'MaxModelRequests'))

_REP = re.compile(r'^(.*) \[(\d+)/(\d+)\]$')


class EvalFailureRateError(RuntimeError):
    """Too many cases raised: the run is not a measurement, so nothing was saved.

    A file of exceptions must never become a cache entry -- the next reader cannot
    tell it from a real result.
    """


async def _await(awaitable):
    return await awaitable


def _preview(value, chars):
    """Shorten a string for display, leaving the original untouched."""
    if not isinstance(value, str) or len(value) <= chars:
        return value
    return value[:max(chars - 3, 0)] + '...'


def _as_mapping(obj):
    """Best-effort dict view of metadata / output: pydantic model, dict, or nothing."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    dump = getattr(obj, 'model_dump', None)
    if callable(dump):
        try:
            return dict(dump())
        except Exception:
            pass
    if hasattr(obj, '__dict__') and not isinstance(obj, type):
        return {k: v for k, v in vars(obj).items() if not k.startswith('_')}
    return {}


def _value_of(result):
    """The value an evaluator recorded; evaluator outputs may be plain values."""
    return getattr(result, 'value', result)


# ------------------------------------------------------------- identity helpers

def _source_hash(fn):
    """md5 over a function's normalized source -- what makes an edit invalidate."""
    fn = inspect.unwrap(getattr(fn, '__func__', fn))
    try:
        src = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):
        return 'nosource:{}.{}'.format(getattr(fn, '__module__', '?'),
                                       getattr(fn, '__qualname__', repr(fn)))
    try:
        blob = ast.dump(oryxflow.codehash._strip_docstrings(ast.parse(src)))
    except SyntaxError:
        blob = src
    return hashlib.md5(blob.encode('utf-8')).hexdigest()[:16]


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


def _jsonable(obj):
    """A JSON-safe copy; anything JSON cannot carry becomes its str()."""
    try:
        return json.loads(json.dumps(obj, default=str))
    except (TypeError, ValueError):
        return str(obj)


def _type_name(obj):
    return type(obj).__qualname__


def _spec(evaluator):
    """An evaluator's CONFIGURATION, stably: a rubric edit must move the scorer identity,
    and a model object's repr (which carries a memory address) must not."""
    raw = None
    try:
        spec = evaluator.as_spec()
        raw = spec.model_dump(mode='json') if hasattr(spec, 'model_dump') else spec
        json.dumps(raw)
    except Exception:
        raw = None
    if raw is None:
        if dataclasses.is_dataclass(evaluator) and not isinstance(evaluator, type):
            raw = {f.name: getattr(evaluator, f.name, None)
                   for f in dataclasses.fields(evaluator)}
        else:
            raw = {}
    return json.loads(json.dumps(raw, sort_keys=True, default=_type_name))


def _evaluator_identity(evaluator):
    cls = type(evaluator)
    code = oryxflow.codehash.task_code_hash(cls)
    if code is None:
        # not project code: the installed distribution's version is its identity
        code = _dist_version((cls.__module__ or '').split('.')[0])
    return {'class': '{}.{}'.format(cls.__module__, cls.__qualname__),
            'code': code, 'spec': _spec(evaluator)}


def _dist_version(top):
    """``'pydantic_evals'`` -> ``'pydantic-evals==2.54.0'``; modules need not carry
    ``__version__`` (pydantic-evals does not), the installed distribution always does."""
    if not top:
        return '?'
    try:
        from importlib import metadata
        dists = metadata.packages_distributions().get(top) or [top]
        return '{}=={}'.format(dists[0], metadata.version(dists[0]))
    except Exception:
        return top


def _needs_trace(evaluator):
    """Does this evaluator read the run's span tree? Then it scores in the outputs stage."""
    flag = getattr(evaluator, 'needs_trace', None)
    if flag is not None:
        return bool(flag)
    return any(c.__name__ in _TRACE_EVALUATORS
               and (c.__module__ or '').startswith('pydantic_evals')
               for c in type(evaluator).__mro__)


# --------------------------------------------------------------- output records

def _type_path(cls):
    qual = getattr(cls, '__qualname__', '')
    if '<locals>' in qual:
        return None
    return '{}:{}'.format(cls.__module__, qual)


def _dump_output(obj):
    """``(json payload, type path or None)`` for one case output."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj, None
    if callable(getattr(obj, 'model_dump', None)):
        return obj.model_dump(mode='json'), _type_path(type(obj))
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _jsonable(dataclasses.asdict(obj)), _type_path(type(obj))
    return _jsonable(obj), None


_LOAD_WARNED = set()


def _load_output(payload, path):
    """Rebuild the typed output a scorer expects (``ctx.output.message``).

    Safe from drift: the output's type lives in the code the outputs stage hashes, so a
    changed model recomputes the outputs before anything rehydrates them.
    """
    if not path:
        return payload
    try:
        module, qual = path.split(':', 1)
        cls = importlib.import_module(module)
        for part in qual.split('.'):
            cls = getattr(cls, part)
    except Exception:
        if path not in _LOAD_WARNED:
            _LOAD_WARNED.add(path)
            warnings.warn('stored eval outputs name type {} but it cannot be imported; '
                          'scorers receive the stored dict instead'.format(path),
                          RuntimeWarning, stacklevel=2)
        return payload
    if callable(getattr(cls, 'model_validate', None)):
        return cls.model_validate(payload)
    if dataclasses.is_dataclass(cls) and isinstance(payload, dict):
        return cls(**payload)
    return payload


def _split_rep(name, source=None):
    """``('c1 [2/3]', 'c1')`` -> ``('c1', 1)``: pydantic-evals names repeat i of N
    ``"<name> [i/N]"`` and records the group as ``source_case_name``."""
    match = _REP.match(name or '')
    if match and (source is None or match.group(1) == source):
        return match.group(1), int(match.group(2)) - 1
    return (source or name), 0


def _results_of(case):
    out = {}
    for group in ('assertions', 'scores', 'labels'):
        for key, result in (getattr(case, group, None) or {}).items():
            out[key] = _jsonable(_value_of(result))
    return out


def _failure_messages(case):
    return [str(getattr(f, 'error_message', None) or f)
            for f in getattr(case, 'evaluator_failures', None) or []]


class _Failure:
    """A stored case failure, shaped like the ReportCaseFailure ``_to_frame`` reads."""

    def __init__(self, record, case):
        self.name = record['case_name']
        self.source_case_name = record['source_case_name']
        self.metadata = getattr(case, 'metadata', None)
        self.expected_output = getattr(case, 'expected_output', None)
        self.error_message = record.get('error') or 'error'
        self.error_stacktrace = record.get('error_stacktrace')


# ----------------------------------------------------------------- the outputs task

class EvalOutputs(oryxflow.tasks.TaskJson):
    """The model-call stage of one eval cell. Generated from a ``TaskEval``; never
    declared by hand. One JSON per cell, one record per case x repeat."""
    persists = ['outputs']
    _eval_cls = None

    def _eval(self):
        return self._eval_cls(**self.param_kwargs)

    def code_version(self):
        return self._eval()._outputs_version()

    def run(self):
        self.save({'outputs': self._eval()._run_outputs()})


# --------------------------------------------------------------------- the eval

class TaskEval(oryxflow.tasks.TaskPqPandas):
    """One cell of an eval matrix: one arm's cases, run, scored, cached.

    Declare the dataset, the parameters that define an arm, and one ``case()``
    coroutine. Everything else -- concurrency, retries, repeats, the success/
    failure split -- is pydantic-evals'.

    ``code_version()`` names what the ARM reads (prompt files, a git ref, a model id):
    it keys the model-call stage. ``scorer_version()`` keys the scoring stage and
    defaults to the evaluators' code and configuration, so editing a scorer re-scores
    the stored outputs without a single model call.

    SEAM: everything downstream of a run reads only the per-case DataFrame, never a
    pydantic-evals object. ``_evaluate()`` returns a report and ``_to_frame()`` turns it
    into rows; override either and nothing else needs to change.
    """
    persists = ['cases']

    dataset = None          # pydantic_evals.Dataset
    metric = None           # ev.Metric
    guardrail = None        # ev.Metric | None
    slices = ()             # metadata columns to break the metric down by

    repeats = oryxflow.IntParameter(default=1)
    concurrency = oryxflow.IntParameter(default=4, significant=False)
    max_failure_rate = 0.2

    # retry policy handed to pydantic-evals; None disables retries entirely
    retry_task = DEFAULT_RETRY

    # display cap for the `<field>_preview` columns; raise it, or set it to None
    # to emit no preview columns at all. It never affects `<field>` itself.
    preview_chars = 200

    # Set by the function form when the target is a plain `def`. The generated
    # `case()` is always a coroutine, so it cannot be inspected for this.
    _sync_target = False

    # what the arm reads -- set from a subclass's `code_version` (see __init_subclass__)
    _arm_version = None

    def __init_subclass__(cls, **kwargs):
        """A subclass's ``code_version`` names the ARM, so it keys the outputs stage.

        The engine reads ``code_version`` as a task's own identity; left in place, a
        prompt hash there would also become the SCORING stage's identity and a scorer
        edit would change nothing. So it is moved to ``_arm_version`` and the scoring
        stage keeps the library's own ``code_version``.
        """
        super().__init_subclass__(**kwargs)
        own = cls.__dict__.get('code_version', None)
        if own is not None and own is not TaskEval.__dict__['code_version']:
            cls._arm_version = own
            cls.code_version = TaskEval.__dict__['code_version']

    async def case(self, inputs):
        """Run ONE case and return its output. Override this.

        Write it `async` whenever the call it makes is async -- which for an LLM
        SDK it almost always is. A plain `def` works and returns the same numbers,
        but it BLOCKS the event loop, so the cases run one at a time and
        `concurrency` silently does nothing: 8 half-second cases at concurrency=8
        take 4.9s sync and 1.0s async. Nothing errors; you just wait.

        Let exceptions RAISE. Catching them here and returning an error field
        makes pydantic-evals record a *successful* case: ``report.failures``
        comes back empty, the failure rate reads as zero, every evaluator scores
        an error object as if it were an answer, and filtering them out becomes
        the reader's manual job downstream. Raising is what puts the case in
        ``report.failures``, keeps it out of every rate, and lets ``retry_task``
        do its work.

        Return what the scorers should see, including the tool calls you made if a
        scorer needs them: outputs are stored and re-scored later, the span tree is not.
        """
        raise NotImplementedError(
            '{}: implement `async def case(self, inputs)`'.format(self.task_family))

    def preflight(self):
        """Run ``case()`` once, on the first case, before the model-call stage starts.

        A missing credential or a wrong working directory then costs ONE call
        instead of the whole matrix. Override to probe something else; set
        ``preflight = None`` on the class to skip it.
        """
        cases = getattr(self.dataset, 'cases', None)
        if not cases:
            return
        result = self.case(cases[0].inputs)
        if inspect.isawaitable(result):
            asyncio.run(_await(result))

    # ---- identity

    def code_version(self):
        """The SCORING stage's identity -- see ``scorer_version()``. Subclasses that
        define ``code_version`` are naming the arm instead (``__init_subclass__``)."""
        return {'scorers': self.scorer_version(), 'frame': FRAME_VERSION}

    def scorer_version(self):
        """What decides the scores, given stored outputs: every evaluator's code and
        configuration. Override to fold in something else the scorers read (a rubric
        file, a judge model id resolved at run time)."""
        return [_evaluator_identity(e) for e in self._evaluators(trace=False)]

    def _outputs_version(self):
        """The model-call stage's identity: the arm, the code case() runs, the cases."""
        arm = self._arm_version
        if callable(arm):
            arm = arm()
        fns = self._case_callables()
        code = oryxflow.codehash.callable_code_hash(*fns)
        if code is None:
            code = [_source_hash(f) for f in fns]
        return {'arm': arm, 'code': code, 'cases': _case_digest(self.dataset),
                'trace': [_evaluator_identity(e) for e in self._evaluators(trace=True)],
                'outputs': OUTPUTS_VERSION}

    def _case_callables(self):
        """The methods a subclass defines -- case() and its helpers -- minus the scoring
        side. Class attributes (the dataset and its evaluators) are deliberately out."""
        skip = {'code_version', 'scorer_version'}
        fns, seen = [], set()
        for klass in type(self).__mro__:
            if klass is TaskEval:
                break
            for name, value in vars(klass).items():
                if name in skip or name in seen or name.startswith('__'):
                    continue
                func = getattr(value, '__func__', value)
                if inspect.isfunction(func):
                    seen.add(name)
                    fns.append(func)
        return fns

    def _evaluators(self, trace):
        out, seen = [], set()
        pool = list(getattr(self.dataset, 'evaluators', None) or [])
        for case in getattr(self.dataset, 'cases', None) or []:
            pool += list(getattr(case, 'evaluators', None) or [])
        for ev in pool:
            if id(ev) in seen or _needs_trace(ev) != trace:
                continue
            seen.add(id(ev))
            out.append(ev)
        return out

    # ---- wiring

    @classmethod
    def _outputs_cls(cls):
        """The generated model-call task for this eval, with the same parameters."""
        existing = cls.__dict__.get('_ev_outputs')
        if existing is not None:
            return existing
        body = {'_eval_cls': cls, '__module__': cls.__module__,
                '__doc__': 'Model-call stage of {}.'.format(cls.__name__)}
        for name, param in cls.get_params():
            body[name] = copy.copy(param)
        out = type(cls.__name__ + 'Outputs', (EvalOutputs,), body)
        cls._ev_outputs = out
        return out

    def requires(self):
        return {'outputs': self._outputs_cls()(**self.param_kwargs)}

    def run(self):
        self.save({'cases': self._to_frame(self._evaluate())})

    # ---- stage 1: model calls

    def _run_outputs(self):
        """Call case() for every case x repeat; return the records the JSON stores."""
        if self.preflight is not None:
            self.preflight()
        from pydantic_evals import Case, Dataset
        trace = self._evaluators(trace=True)
        trace_ids = {id(e) for e in trace}
        cases = [Case(name=c.name, inputs=c.inputs, metadata=c.metadata,
                      expected_output=c.expected_output,
                      evaluators=[e for e in (c.evaluators or []) if id(e) in trace_ids])
                 for c in self.dataset.cases]
        dataset = Dataset(name=getattr(self.dataset, 'name', None) or 'cases',
                          cases=cases,
                          evaluators=[e for e in (self.dataset.evaluators or [])
                                      if id(e) in trace_ids])
        self._warn_if_serial()
        report = asyncio.run(dataset.evaluate(
            self.case, repeat=int(self.repeats), max_concurrency=int(self.concurrency),
            progress=False, retry_task=self.retry_task))
        total = len(report.cases) + len(report.failures)
        if total and len(report.failures) / total > self.max_failure_rate:
            first = report.failures[0]
            raise EvalFailureRateError(
                '{}: {}/{} cases failed ({:.0%}), above max_failure_rate={:.0%}. '
                'Nothing was saved -- a file of exceptions is not a measurement. '
                'First failure:\n{}'.format(
                    self.task_family, len(report.failures), total,
                    len(report.failures) / total, self.max_failure_rate,
                    first.error_stacktrace or first.error_message))
        records = []
        for case in report.cases:
            source, rep = _split_rep(case.name, getattr(case, 'source_case_name', None))
            payload, type_path = _dump_output(case.output)
            records.append({
                'case_name': case.name, 'source_case_name': source, 'rep': rep,
                'inputs': _dump_output(case.inputs)[0], 'output': payload,
                'output_type': type_path,
                'metrics': _jsonable(dict(case.metrics or {})),
                'attributes': _jsonable(dict(case.attributes or {})),
                'task_duration_s': case.task_duration,
                'total_duration_s': case.total_duration,
                'trace_results': _results_of(case),
                'trace_failures': _failure_messages(case),
                'error': ''})
        for failure in report.failures:
            source, rep = _split_rep(failure.name,
                                     getattr(failure, 'source_case_name', None))
            records.append({
                'case_name': failure.name, 'source_case_name': source, 'rep': rep,
                'inputs': _dump_output(failure.inputs)[0],
                'error': failure.error_message or 'error',
                'error_stacktrace': failure.error_stacktrace})
        return records

    def _warn_if_serial(self):
        """A sync `case()` with concurrency>1 is a silent 5x+ slowdown, not an error.

        pydantic-evals honours `max_concurrency` by scheduling coroutines; a
        blocking `case()` holds the loop, so the cases run one at a time while the
        knob still reads as set. Worth a word, since the numbers are identical and
        nothing else would ever tell you.
        """
        if int(self.concurrency) <= 1:
            return
        if self._sync_target or not inspect.iscoroutinefunction(
                getattr(type(self), 'case', None)):
            warnings.warn(
                '{}: case() is not async, so it blocks the event loop and the cases '
                'run one at a time -- concurrency={} is being ignored. Make it '
                '`async def` (and await the call inside) to get the concurrency '
                'back.'.format(self.task_family, self.concurrency),
                RuntimeWarning, stacklevel=2)

    # ---- stage 2: scoring

    def _outputs_records(self):
        """The stored records, computing them first if this instance runs on its own.

        Inside a sweep the engine has already built the outputs task (it is a
        dependency); this only computes when ``run()`` / ``_evaluate()`` is called
        directly on an instance.
        """
        task = self.requires()['outputs']
        if not task.complete():
            task.run()
        return task.outputLoad(keys='outputs')

    def _evaluate(self):
        """Score the stored outputs and return a report. OVERRIDE POINT: a different runner.

        Everything downstream reads only ``_to_frame()``'s DataFrame, so a replacement
        runner only has to return something ``_to_frame()`` accepts.
        """
        from pydantic_evals import Case, Dataset
        from pydantic_evals.lifecycle import CaseLifecycle

        records = self._outputs_records()
        by_source = {c.name: c for c in self.dataset.cases}
        scored = [r for r in records if not r.get('error')]
        by_name = {r['case_name']: r for r in scored}
        self._records = {r['case_name']: r for r in records}
        self._stored_failures = [_Failure(r, by_source.get(r['source_case_name']))
                                 for r in records if r.get('error')]

        scorer_ids = {id(e) for e in self._evaluators(trace=False)}
        cases = []
        for r in scored:
            src = by_source.get(r['source_case_name'])
            if src is None:
                continue
            cases.append(Case(name=r['case_name'], inputs=src.inputs,
                              metadata=src.metadata,
                              expected_output=src.expected_output,
                              evaluators=[e for e in (src.evaluators or [])
                                          if id(e) in scorer_ids]))
        dataset = Dataset(name=getattr(self.dataset, 'name', None) or 'cases',
                          cases=cases,
                          evaluators=[e for e in (self.dataset.evaluators or [])
                                      if id(e) in scorer_ids])

        class _Replay(CaseLifecycle):
            """Hand the evaluators the STORED output instead of a fresh call."""

            async def prepare_context(self, ctx):
                rec = by_name[self.case.name]
                ctx.output = _load_output(rec.get('output'), rec.get('output_type'))
                ctx.duration = rec.get('task_duration_s') or 0.0
                ctx.metrics = dict(rec.get('metrics') or {})
                ctx.attributes = dict(rec.get('attributes') or {})
                return ctx

        async def _stored(inputs):
            return None

        return asyncio.run(dataset.evaluate(
            _stored, max_concurrency=int(self.concurrency), progress=False,
            lifecycle=_Replay))

    # ---- the frame

    def _to_frame(self, report):
        """One row per case. OVERRIDE POINT: a different report shape.

        This frame is the seam -- metrics, slices, intervals, the verdict and the
        report read it and nothing else. Columns:

        ===========================  ===========================================
        ``case_name``                ``case.name``
        ``source_case_name``         the case before repeats
        ``rep``                      0-based repeat index within that group
        one column per metadata key  ``case.metadata``
        one column per assertion     ``case.assertions[name].value``
        one column per score         ``case.scores[name].value``
        one column per label         ``case.labels[name].value``
        one column per metric        ``case.metrics`` (``increment_eval_metric``)
        one column per attribute     ``case.attributes`` (``set_eval_attribute``)
        ``expected`` / ``expected_*``  ``case.expected_output``, flattened
        ``total_duration_s``         ``case.total_duration``
        ``evaluator_errors``         how many evaluators RAISED on this case
        ``evaluator_error``          their messages, joined
        ``task_duration_s``          ``case.task_duration``
        ``error``                    ``''`` for a case, the message for a failure
        output fields                flattened from ``case.output``
        ===========================  ===========================================

        Both successes and failures become rows, told apart by ``error``; failures
        keep their metadata so a per-slice failure count is possible.

        TRUNCATION: a value is never shortened in place. A long text column is
        emitted twice -- ``<field>`` in full (parquet handles long strings, and
        this is what any later re-analysis reads) and ``<field>_preview`` capped
        for display -- because a cap applied where a scorer can see it turns the
        harness into the thing being measured.
        """
        import pandas as pd

        failures = list(report.failures) + list(getattr(self, '_stored_failures', []))
        rows = [self._row_case(c) for c in report.cases]
        rows += [self._row_failure(f) for f in failures]
        broken = list(getattr(report, 'report_evaluator_failures', None) or [])
        self._assign_reps(rows)
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        if 'task_duration_s' not in df.columns:
            df['task_duration_s'] = NAN
        if 'error' not in df.columns:
            df['error'] = ''
        if broken:
            # A report-level evaluator (a confusion matrix, a PR curve) that raised
            # is as silent as a case-level one that did: its analysis is simply
            # absent. Same rule -- say so rather than leave a gap.
            df['evaluator_errors'] = df.get('evaluator_errors', 0)
            first = str(getattr(broken[0], 'error_message', None) or broken[0])[:200]
            df.loc[df.index[0], 'evaluator_errors'] = (
                int(df['evaluator_errors'].iloc[0] or 0) + len(broken))
            df.loc[df.index[0], 'evaluator_error'] = (
                'report evaluator: ' + first)
        return self._finalize_frame(df)

    def _record(self, case):
        return (getattr(self, '_records', None) or {}).get(case.name)

    def _row_common(self, case):
        """The columns a success and a failure share, in schema order."""
        rec = self._record(case)
        if rec is not None:
            source, rep = rec['source_case_name'], rec['rep']
        else:
            source, rep = _split_rep(case.name, getattr(case, 'source_case_name', None))
        row = {'case_name': case.name, 'source_case_name': source, 'rep': rep}
        row.update(_as_mapping(getattr(case, 'metadata', None)))
        # The answer key, on the frame rather than only on the case list. Without
        # it every comparison against `expected` -- a confusion matrix, a per-label
        # breakdown -- has to join back by case name, which is work the reader
        # should not be doing to reach something the report already carried.
        self._add_expected(row, getattr(case, 'expected_output', None))
        return row

    @staticmethod
    def _add_expected(row, expected):
        if expected is None:
            return
        fields = _as_mapping(expected)
        if fields:
            for name, value in fields.items():
                row['expected_' + name] = value
        else:
            row['expected'] = expected

    def _row_case(self, case):
        row = self._row_common(case)
        rec = self._record(case) or {}
        for group in ('assertions', 'scores', 'labels'):
            for name, result in (getattr(case, group, None) or {}).items():
                row[name] = _value_of(result)
        # span-reading evaluators scored in the outputs stage, while spans existed
        for name, value in (rec.get('trace_results') or {}).items():
            row.setdefault(name, value)
        # `increment_eval_metric` / `set_eval_attribute` are how a task reports what
        # a call actually cost -- tokens, retries, the resolved model snapshot. They
        # were being discarded, which is why a sweep could only ever estimate cost
        # as calls x a number you supplied.
        for group in ('metrics', 'attributes'):
            for name, value in (_as_mapping(getattr(case, group, None)) or {}).items():
                row[name if name not in row else group[:-1] + '_' + name] = value
        row['task_duration_s'] = getattr(case, 'task_duration', None)
        row['total_duration_s'] = rec.get('total_duration_s',
                                          getattr(case, 'total_duration', None))
        row['error'] = ''
        # An evaluator that RAISED leaves no column at all, which is
        # indistinguishable from one that declined to score -- so a judge broken
        # on every case reads exactly like a judge with nothing to judge. Count
        # them; `EvalResult` says so above the rate.
        failures = _failure_messages(case) + list(rec.get('trace_failures') or [])
        row['evaluator_errors'] = len(failures)
        row['evaluator_error'] = '; '.join(failures)[:400]
        for name, value in _as_mapping(getattr(case, 'output', None)).items():
            # an output field never overwrites a structural or metadata column
            row[name if name not in row else 'output_' + name] = value
        return row

    def _row_failure(self, failure):
        row = self._row_common(failure)
        row['task_duration_s'] = NAN
        row['total_duration_s'] = NAN
        row['error'] = failure.error_message or failure.error_stacktrace or 'error'
        row['evaluator_errors'] = 0
        row['evaluator_error'] = ''
        return row

    def _assign_reps(self, rows):
        """Kept as an override point. ``rep`` is no longer derived here: it is read
        from pydantic-evals' own ``"<name> [i/N]"`` naming when the outputs are stored
        (see ``_split_rep``), so it survives failures and re-scoring unchanged."""

    def _finalize_frame(self, df):
        """Normalize dtypes for parquet, then add the capped preview columns."""
        from pandas.api.types import is_object_dtype, is_string_dtype
        cap = self.preview_chars
        for col in list(df.columns):
            # pandas 3 gives text its own `str` dtype; pandas 2 leaves it `object`
            if not (is_object_dtype(df[col]) or is_string_dtype(df[col])):
                continue
            vals = df[col].dropna()
            if len(vals) and all(isinstance(v, bool) for v in vals):
                df[col] = df[col].astype('boolean')
                continue
            texts = [v for v in vals if isinstance(v, str)]
            if cap and texts and max(len(v) for v in texts) > cap:
                df[col + '_preview'] = df[col].map(lambda v: _preview(v, cap))
        return df
