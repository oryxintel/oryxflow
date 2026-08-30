"""One number a sweep is judged on."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Union


@dataclass
class Metric:
    """One number a sweep is judged on.

    ``column`` is a boolean column averaged into a rate -- accuracy, write rate,
    false-positive rate, pass rate are all this shape. Pass a callable taking the
    frame instead when it is not.

    ``where`` filters rows: a column name, or ``~column`` for its negation.
    ``budget`` (guardrails) is the value that must not be exceeded in the
    ``higher_is_better=False`` direction; a winner that breaks it is reported as
    not a clean win.

    ``human`` names a column of HUMAN labels for the same question this metric
    asks -- filled for a sample of cases, blank elsewhere. When present, the
    verdict reports how well the judge agrees with those labels (TPR, TNR,
    Cohen's kappa) and a Rogan-Gladen corrected rate. A judge nobody checked is
    an opinion with a confidence interval printed around it.

    ``coverage`` names a companion Metric answering "how often was there any
    output at all". When present it is rendered FIRST, because a quality rate
    computed over a shrinking subset improves as the subset shrinks -- read the
    coverage metric first, and never a quality rate alone.

    Rows carrying an ``error`` are excluded from every rate: a case that raised
    is not evidence either way, and counting it as a miss would let an outage
    read as a quality regression.

    A row whose metric column is NULL is excluded for the same reason -- it was
    not measured, which is not the same as measured-and-failed. An LLM judge that
    only grades some rows (or was switched off) leaves nulls behind, and folding
    them in as misses silently deflates the rate while padding the denominator.
    A metric with no measured row at all reports ``n/a``, never ``0%``.
    """
    label: str
    column: Union[str, Callable]
    where: Optional[str] = None
    higher_is_better: bool = True
    budget: Optional[float] = None
    coverage: Optional['Metric'] = None
    human: Optional[str] = None

    def eligible(self, df):
        """Rows this metric applies to: `where` applied, errored rows dropped.

        The denominator BEFORE asking whether each row carries a verdict, which
        is what makes `unmeasured()` countable.
        """
        out = df
        if 'error' in out.columns:
            out = out[out['error'].fillna('') == '']
        if self.where:
            col = self.where
            negate = col.startswith('~')
            if negate:
                col = col[1:]
            if col not in out.columns:
                raise KeyError(
                    "metric '{}': where column '{}' is not in the frame (have {})".format(
                        self.label, col, sorted(out.columns)))
            mask = out[col].astype('boolean').fillna(False)
            out = out[~mask] if negate else out[mask]
        return out

    def subset(self, df):
        """The rows this metric is computed over: eligible AND actually measured."""
        out = self.eligible(df)
        if isinstance(self.column, str) and self.column in out.columns:
            out = out[out[self.column].notna()]
        return out

    def unmeasured(self, df):
        """How many eligible rows carry no verdict in the metric column.

        Reported beside the rate: a quality number over a shrinking MEASURED
        subset is the same trap as one over a shrinking `where` subset, and an
        LLM judge that grades only some rows leaves exactly that behind.
        """
        if callable(self.column):
            return 0
        try:
            eligible = self.eligible(df)
        except KeyError:
            return 0
        if self.column not in getattr(eligible, 'columns', ()):
            return 0
        return int(eligible[self.column].isna().sum())

    def compute(self, df):
        """Return ``(value, n, numerator)`` -- numerator is None for a callable column.

        ``n`` counts MEASURED rows only, so a rate is never diluted by rows that
        carry no verdict.
        """
        sub = self.subset(df)
        n = len(sub)
        if callable(self.column):
            return (self.column(sub) if n else float('nan')), n, None
        if self.column not in sub.columns:
            raise KeyError(
                "metric '{}': column '{}' is not in the frame (have {})".format(
                    self.label, self.column, sorted(sub.columns)))
        if not n:
            return float('nan'), 0, 0
        series = sub[self.column].astype('boolean').fillna(False)
        numerator = int(series.sum())
        return numerator / n, n, numerator

    def over_budget(self, value):
        """True when `value` breaks this metric's budget in the bad direction."""
        if self.budget is None or value != value:
            return False
        return value < self.budget if self.higher_is_better else value > self.budget
