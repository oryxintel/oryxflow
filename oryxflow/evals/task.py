"""One cell of an eval matrix: one arm's cases, scored, cached as parquet."""

import asyncio
import inspect

import oryxflow
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


class EvalFailureRateError(RuntimeError):
    """Too many cases raised: the run is not a measurement, so nothing was saved.

    A parquet of exceptions must never become a cache entry -- the next reader
    cannot tell it from a real result.
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


class TaskEval(oryxflow.tasks.TaskPqPandas):
    """One cell of an eval matrix: one arm's cases, scored, cached as parquet.

    Declare the dataset, the parameters that define an arm, and one ``case()``
    coroutine. Everything else -- concurrency, retries, repeats, the success/
    failure split -- is pydantic-evals'.

    SEAM: everything downstream of a run reads only the per-case DataFrame, never
    a pydantic-evals object. To use a different runner or report shape later,
    override ``_evaluate()`` and ``_to_frame()``; nothing else needs to change.
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
        """
        raise NotImplementedError(
            '{}: implement `async def case(self, inputs)`'.format(self.task_family))

    def preflight(self):
        """Run ``case()`` once, on the first case, before the sweep starts.

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

    def run(self):
        if self.preflight is not None:
            self.preflight()
        report = self._evaluate()
        total = len(report.cases) + len(report.failures)
        if total and len(report.failures) / total > self.max_failure_rate:
            first = report.failures[0]
            raise EvalFailureRateError(
                '{}: {}/{} cases failed ({:.0%}), above max_failure_rate={:.0%}. '
                'Nothing was saved -- a parquet of exceptions is not a measurement. '
                'First failure:\n{}'.format(
                    self.task_family, len(report.failures), total,
                    len(report.failures) / total, self.max_failure_rate,
                    first.error_stacktrace or first.error_message))
        self.save({'cases': self._to_frame(report)})

    def _warn_if_serial(self):
        """A sync `case()` with concurrency>1 is a silent 5x+ slowdown, not an error.

        pydantic-evals honours `max_concurrency` by scheduling coroutines; a
        blocking `case()` holds the loop, so the cases run one at a time while the
        knob still reads as set. Worth a word, since the numbers are identical and
        nothing else would ever tell you.
        """
        import warnings
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

    def _evaluate(self):
        """Run the dataset and return a report. OVERRIDE POINT: a different runner.

        Everything downstream reads only ``_to_frame()``'s DataFrame, so a
        replacement runner only has to return something ``_to_frame()`` accepts.
        """
        self._warn_if_serial()
        return asyncio.run(self.dataset.evaluate(
            self.case, repeat=int(self.repeats), max_concurrency=int(self.concurrency),
            progress=False, retry_task=self.retry_task))

    def _to_frame(self, report):
        """One row per case. OVERRIDE POINT: a different report shape.

        This frame is the seam -- metrics, slices, intervals, the verdict and the
        report read it and nothing else. Columns:

        ===========================  ===========================================
        ``case_name``                ``case.name``
        ``source_case_name``         the case before repeats
        ``rep``                      0-based occurrence within that group
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

        Both ``report.cases`` and ``report.failures`` become rows, told apart by
        ``error``; failures keep their metadata so a per-slice failure count is
        possible.

        TRUNCATION: a value is never shortened in place. A long text column is
        emitted twice -- ``<field>`` in full (parquet handles long strings, and
        this is what any later re-analysis reads) and ``<field>_preview`` capped
        for display -- because a cap applied where a scorer can see it turns the
        harness into the thing being measured.
        """
        import pandas as pd

        rows = [self._row_case(c) for c in report.cases]
        rows += [self._row_failure(f) for f in report.failures]
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

    def _row_common(self, case):
        """The columns a success and a failure share, in schema order."""
        row = {
            'case_name': case.name,
            'source_case_name': getattr(case, 'source_case_name', None) or case.name,
            'rep': 0,
        }
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
        for group in ('assertions', 'scores', 'labels'):
            for name, result in (getattr(case, group, None) or {}).items():
                row[name] = _value_of(result)
        # `increment_eval_metric` / `set_eval_attribute` are how a task reports what
        # a call actually cost -- tokens, retries, the resolved model snapshot. They
        # were being discarded, which is why a sweep could only ever estimate cost
        # as calls x a number you supplied.
        for group in ('metrics', 'attributes'):
            for name, value in (_as_mapping(getattr(case, group, None)) or {}).items():
                row[name if name not in row else group[:-1] + '_' + name] = value
        row['task_duration_s'] = getattr(case, 'task_duration', None)
        row['total_duration_s'] = getattr(case, 'total_duration', None)
        row['error'] = ''
        # An evaluator that RAISED leaves no column at all, which is
        # indistinguishable from one that declined to score -- so a judge broken
        # on every case reads exactly like a judge with nothing to judge. Count
        # them; `EvalResult` says so above the rate.
        failures = getattr(case, 'evaluator_failures', None) or []
        row['evaluator_errors'] = len(failures)
        row['evaluator_error'] = '; '.join(
            str(getattr(f, 'error_message', None) or f) for f in failures)[:400]
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
        """`rep` is derived, not reported: pydantic-evals carries no repeat index.

        Occurrences are numbered within each ``source_case_name`` in report order
        (successes first, then failures), which is what makes a repeat group
        addressable without parsing the ``name [i/n]`` suffix.
        """
        seen = {}
        for row in rows:
            key = row['source_case_name']
            row['rep'] = seen.get(key, 0)
            seen[key] = row['rep'] + 1

    def _finalize_frame(self, df):
        """Normalize dtypes for parquet, then add the capped preview columns."""
        cap = self.preview_chars
        for col in list(df.columns):
            if df[col].dtype != object:
                continue
            vals = df[col].dropna()
            if len(vals) and all(isinstance(v, bool) for v in vals):
                df[col] = df[col].astype('boolean')
                continue
            texts = [v for v in vals if isinstance(v, str)]
            if cap and texts and max(len(v) for v in texts) > cap:
                df[col + '_preview'] = df[col].map(lambda v: _preview(v, cap))
        return df
