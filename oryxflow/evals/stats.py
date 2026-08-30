"""Confidence intervals for eval rates, clustered by case.

Every interval here resamples **case names**, not rows. With ``repeats=3`` the
three runs of one case are correlated, so treating 66 rows as 66 independent
samples reports an interval roughly ``sqrt(3)`` too narrow -- a made-up gain in
confidence that a reader then acts on. Clustering keeps the interval honest: at
``repeats=1`` and ``repeats=3`` over the same cases with the same per-case
outcome, the interval is the same width.

``scipy.stats.bootstrap`` does the resampling and computes the interval (BCa);
the functions here only build the clusters and pick the fallback.

Both functions return a named tuple whose ``method`` field says how the interval
was produced, so a caller can render the caveat rather than guess:

* ``'bca'``      -- the normal path, scipy's bias-corrected accelerated bootstrap.
* ``'wilson'``   -- BCa degenerated (every case agreed, so every resample gives
  the same rate) and a Wilson interval on the number of *cases* was used instead.
  Reporting ``[1.0, 1.0]`` from 22 cases would claim a certainty the data does
  not carry, so this fallback exists to avoid exactly that.
* ``'newcombe'`` -- the same degeneracy in a delta; a conservative square-and-add
  interval built from each arm's Wilson interval.
* ``'empty'``    -- nothing to compute over; the bounds are NaN.
"""

from __future__ import annotations

import warnings
from collections import namedtuple

import numpy as np
import pandas as pd
from scipy.stats import binomtest, bootstrap

RateCI = namedtuple('RateCI', 'value low high n n_clusters numerator method')
DeltaCI = namedtuple('DeltaCI', 'value low high n_a n_b n_clusters method spans_zero')

NAN = float('nan')


def rate_ci(df, metric, confidence=0.95, n_resamples=2000, random_state=0):
    """Bootstrap confidence interval for a rate, clustered by case.

    Resamples CASE NAMES with replacement, not rows: with ``repeat=3`` the three
    runs of one case are correlated, and treating them as independent samples
    reports an interval roughly sqrt(3) too narrow. Delegates to
    ``scipy.stats.bootstrap`` (BCa); this function only builds the clusters.

    The cluster is ``source_case_name`` when the frame carries it (that is what
    pydantic-evals groups repeats by), otherwise ``case_name``.

    Returns ``RateCI(value, low, high, n, n_clusters, numerator, method)``. Rate
    bounds are clamped to [0, 1]. ``method`` is ``'wilson'`` when every case
    agreed and BCa degenerated -- see the module docstring.

    ``random_state`` is seeded by default so the same frame always reports the
    same interval; pass ``None`` for a fresh draw.
    """
    value, n, numerator = metric.compute(df)
    is_rate = not callable(metric.column)

    if is_rate:
        names, num, cnt = _rate_clusters(df, metric)
    else:
        names, groups = _callable_clusters(df, metric)
    k = len(names)

    if k == 0:
        return RateCI(value, NAN, NAN, n, 0, numerator, 'empty')

    ci = None
    if k > 1:
        if is_rate:
            ci = _bca((num, cnt), _ratio, confidence, n_resamples, random_state)
        else:
            ci = _bca((np.arange(k, dtype=float),),
                      lambda ii: _apply(metric.column, groups, ii),
                      confidence, n_resamples, random_state, vectorized=False)

    if ci is not None:
        low, high = ci
        method = 'bca'
    elif is_rate:
        # Every case agreed, so every resample returns the same rate. Fall back to
        # Wilson over the number of CASES (not rows), which keeps the clustering.
        low, high = _wilson(int(round(value * k)) if value == value else 0, k, confidence)
        method = 'wilson'
    else:
        return RateCI(value, NAN, NAN, n, k, numerator, 'empty')

    if is_rate:
        low, high = _clamp(low, 0.0, 1.0), _clamp(high, 0.0, 1.0)
    return RateCI(value, float(low), float(high), n, k, numerator, method)


def delta_ci(df, metric, arm_a, arm_b, arm_col=None, confidence=0.95,
             n_resamples=2000, random_state=0):
    """Interval for (arm_a - arm_b), resampling the SAME case names for both arms
    so the pairing is preserved -- a paired comparison is materially tighter than
    differencing two independent intervals, and both arms ran the same cases.

    ``arm_col`` is the column holding the arm label. A sweep tags each flow's
    rows with that flow's parameters, so it is the arm parameter's own name
    (``'prompt_version'``, ``'model_id'``). Left as ``None`` it uses a column
    literally named ``arm`` when there is one, otherwise the single column
    containing both ``arm_a`` and ``arm_b``; ambiguity raises rather than guesses.

    Only cases present in BOTH arms are used -- that is what pairing means -- so
    the point estimate can differ slightly from the difference of the two arms'
    separately reported rates when an arm is missing a case.

    Returns ``DeltaCI(value, low, high, n_a, n_b, n_clusters, method, spans_zero)``.
    ``spans_zero`` is the "inside noise" test: True means no winner may be named.
    """
    arm_col = _resolve_arm_col(df, arm_a, arm_b, arm_col)
    da = df[df[arm_col] == arm_a]
    db = df[df[arm_col] == arm_b]
    is_rate = not callable(metric.column)

    if is_rate:
        na, numa, cnta = _rate_clusters(da, metric)
        nb, numb, cntb = _rate_clusters(db, metric)
        ia = {x: i for i, x in enumerate(na.tolist())}
        ib = {x: i for i, x in enumerate(nb.tolist())}
        names = [x for x in na.tolist() if x in ib]
        numa, cnta = numa[[ia[x] for x in names]], cnta[[ia[x] for x in names]]
        numb, cntb = numb[[ib[x] for x in names]], cntb[[ib[x] for x in names]]
        n_a, n_b = int(cnta.sum()), int(cntb.sum())
    else:
        na, ga = _callable_clusters(da, metric)
        nb, gb = _callable_clusters(db, metric)
        names = [x for x in na if x in set(nb)]
        ga = [ga[na.index(x)] for x in names]
        gb = [gb[nb.index(x)] for x in names]
        n_a = int(sum(len(g) for g in ga))
        n_b = int(sum(len(g) for g in gb))

    k = len(names)
    if k == 0:
        raise ValueError(
            "delta_ci: arms '{}' and '{}' share no case in column '{}' -- a paired "
            "comparison needs the same cases in both arms".format(arm_a, arm_b, arm_col))

    if is_rate:
        pa = float(numa.sum() / cnta.sum()) if cnta.sum() else NAN
        pb = float(numb.sum() / cntb.sum()) if cntb.sum() else NAN
    else:
        pa = float(metric.column(pd.concat(ga)))
        pb = float(metric.column(pd.concat(gb)))
    value = pa - pb

    ci = None
    if k > 1:
        if is_rate:
            ci = _bca((numa, cnta, numb, cntb), _ratio_delta,
                      confidence, n_resamples, random_state)
        else:
            ci = _bca((np.arange(k, dtype=float),),
                      lambda ii: _apply(metric.column, ga, ii) - _apply(metric.column, gb, ii),
                      confidence, n_resamples, random_state, vectorized=False)

    if ci is not None:
        low, high = ci
        method = 'bca'
    elif is_rate:
        # Both arms agreed on every case, so every resample gives the same delta.
        # Square-and-add the two Wilson intervals: conservative for positively
        # correlated arms, which a paired eval always is, and far better than
        # reporting a zero-width delta from a handful of cases.
        la, ua = _wilson(int(round(pa * k)) if pa == pa else 0, k, confidence)
        lb, ub = _wilson(int(round(pb * k)) if pb == pb else 0, k, confidence)
        low = value - np.sqrt((pa - la) ** 2 + (ub - pb) ** 2)
        high = value + np.sqrt((ua - pa) ** 2 + (pb - lb) ** 2)
        method = 'newcombe'
    else:
        return DeltaCI(value, NAN, NAN, n_a, n_b, k, 'empty', True)

    if is_rate:
        low, high = _clamp(low, -1.0, 1.0), _clamp(high, -1.0, 1.0)
    spans = True if not (np.isfinite(low) and np.isfinite(high)) else bool(low <= 0 <= high)
    return DeltaCI(value, float(low), float(high), n_a, n_b, k, method, spans)


def cluster_labels(df):
    """The case identity of each row: ``source_case_name`` when present, else ``case_name``.

    Exposed because the clustering, not the bootstrap, is what makes these
    intervals right -- a caller wanting per-case aggregates should group by this.
    """
    return _cluster_labels(df)


def _cluster_labels(df):
    cn = df['case_name'].tolist() if 'case_name' in df.columns else [None] * len(df)
    if 'source_case_name' in df.columns:
        sc = df['source_case_name'].tolist()
        out = [b if (_blank(a)) else a for a, b in zip(sc, cn)]
    else:
        out = list(cn)
    return np.asarray([str(i) if x is None else str(x) for i, x in enumerate(out)],
                      dtype=object)


def _blank(x):
    try:
        if pd.isna(x):
            return True
    except (TypeError, ValueError):
        return False
    return str(x).strip() == ''


def _rate_clusters(df, metric):
    sub = metric.subset(df)
    if not len(sub):
        return np.asarray([], dtype=object), np.asarray([]), np.asarray([])
    ok = sub[metric.column].astype('boolean').fillna(False).astype(int).to_numpy()
    labels = _cluster_labels(sub)
    g = pd.Series(ok).groupby(labels)
    num, cnt = g.sum(), g.size()
    return (num.index.to_numpy(dtype=object), num.to_numpy(dtype=float),
            cnt.to_numpy(dtype=float))


def _callable_clusters(df, metric):
    sub = metric.subset(df)
    if not len(sub):
        return [], []
    labels = _cluster_labels(sub)
    names = sorted(set(labels.tolist()))
    return names, [sub.iloc[np.flatnonzero(labels == nm)] for nm in names]


def _resolve_arm_col(df, arm_a, arm_b, arm_col):
    if arm_col is not None:
        if arm_col not in df.columns:
            raise KeyError("delta_ci: arm column '{}' is not in the frame (have {})".format(
                arm_col, sorted(df.columns)))
        return arm_col
    if 'arm' in df.columns:
        return 'arm'
    found = []
    for col in df.columns:
        try:
            vals = set(df[col].dropna().tolist())
        except TypeError:
            continue
        if arm_a in vals and arm_b in vals:
            found.append(col)
    if len(found) == 1:
        return found[0]
    raise KeyError(
        "delta_ci: pass arm_col -- {} column(s) hold both '{}' and '{}' ({})".format(
            len(found), arm_a, arm_b, found or sorted(df.columns)))


def _apply(fn, groups, ii):
    return float(fn(pd.concat([groups[int(i)] for i in np.ravel(ii)])))


def _ratio(a, b, axis=-1):
    tot = b.sum(axis=axis)
    return np.divide(a.sum(axis=axis), tot,
                     out=np.full(np.shape(tot), NAN, dtype=float), where=tot > 0)


def _ratio_delta(a, b, c, d, axis=-1):
    return _ratio(a, b, axis=axis) - _ratio(c, d, axis=axis)


def _bca(data, statistic, confidence, n_resamples, random_state, vectorized=True):
    """scipy's BCa interval, or None when it degenerates (every case agreed)."""
    kw = dict(statistic=statistic, confidence_level=confidence, n_resamples=n_resamples,
              method='BCa', paired=True, vectorized=vectorized)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            try:
                res = bootstrap(data, rng=random_state, **kw)
            except TypeError:
                res = bootstrap(data, random_state=random_state, **kw)
        low = float(res.confidence_interval.low)
        high = float(res.confidence_interval.high)
    except Exception:
        # scipy signals degeneracy by warning-and-NaN in some versions and by
        # raising in others; both mean the same thing here.
        return None
    if not (np.isfinite(low) and np.isfinite(high)):
        return None
    return low, high


def _wilson(k, n, confidence):
    """Wilson score interval, via scipy's binomtest."""
    if n <= 0:
        return NAN, NAN
    k = int(min(max(k, 0), n))
    ci = binomtest(k, n).proportion_ci(confidence_level=confidence, method='wilson')
    return float(ci.low), float(ci.high)


def _clamp(x, lo, hi):
    if x != x:
        return x
    return float(min(max(x, lo), hi))


# --------------------------------------------------------------- judging the judge

JudgeAlignment = namedtuple(
    'JudgeAlignment', 'tpr tnr kappa agreement n n_pos n_neg informative note')


def judge_alignment(df, judge, human):
    """How well an LLM judge agrees with human labels, on the rows that have both.

    A judge nobody checked is an opinion with a confidence interval printed round
    it. The standard practice is to hand-label a sample, measure the judge against
    it, and only then read anything the judge produced at scale.

    Returns ``tpr`` (of the cases a human passed, the share the judge passed),
    ``tnr`` (same for fails), ``kappa`` and raw ``agreement``.

    **Read kappa, not agreement.** Raw agreement inflates whenever one label
    dominates: if 90% of outputs really do pass, a judge that says "pass" to
    everything scores 90% agreement and has learnt nothing. Cohen's kappa nets out
    the agreement you would get by chance, so that judge scores ~0.

    ``informative`` is False when ``tpr + tnr <= 1`` -- the judge is doing no
    better than a coin weighted to its own base rate, and nothing it says at scale
    can be corrected into an estimate.
    """
    import pandas as pd

    pair = df[[judge, human]].dropna() if judge in df.columns and human in df.columns else None
    if pair is None:
        raise KeyError(
            "judge_alignment: need both '{}' and '{}' in the frame (have {})".format(
                judge, human, sorted(getattr(df, 'columns', []))))
    j = pair[judge].astype('boolean').fillna(False).astype(bool)
    h = pair[human].astype('boolean').fillna(False).astype(bool)
    n = len(pair)
    if not n:
        return JudgeAlignment(NAN, NAN, NAN, NAN, 0, 0, 0, False,
                              'no row has both a judge verdict and a human label')

    n_pos, n_neg = int(h.sum()), int((~h).sum())
    tpr = float((j & h).sum()) / n_pos if n_pos else NAN
    tnr = float((~j & ~h).sum()) / n_neg if n_neg else NAN
    agreement = float((j == h).sum()) / n

    # Cohen's kappa: observed agreement, minus what chance would give.
    pj, ph = float(j.sum()) / n, float(h.sum()) / n
    chance = pj * ph + (1 - pj) * (1 - ph)
    kappa = (agreement - chance) / (1 - chance) if chance < 1 else NAN

    notes = []
    if n < 50:
        notes.append('only {} labelled row(s) -- below ~50 the rates are too noisy '
                     'to act on'.format(n))
    if not n_pos or not n_neg:
        notes.append('the human labels are all one class, so one of TPR/TNR is '
                     'undefined and the judge is untested on the other')
    informative = bool(tpr == tpr and tnr == tnr and (tpr + tnr) > 1)
    if not informative and tpr == tpr and tnr == tnr:
        notes.append('TPR + TNR <= 1: the judge carries no usable signal')
    return JudgeAlignment(tpr, tnr, kappa, agreement, n, n_pos, n_neg, informative,
                          '; '.join(notes))


def corrected_rate(observed, alignment):
    """The Rogan-Gladen correction: an observed rate, adjusted for a known-imperfect
    judge.

    ``(observed + tnr - 1) / (tpr + tnr - 1)`` -- the standard epidemiological
    estimator for prevalence measured with an imperfect test, which is exactly what
    an LLM judge is. A judge that passes 8% of good answers wrongly and fails 5% of
    bad ones does not report the rate you want; this recovers it.

    Returns NaN when the alignment is not informative (``tpr + tnr <= 1``), because
    the formula's denominator goes to zero or negative there and the "correction"
    would be noise amplified. Clipped to [0, 1]: the estimator can land outside it
    when the observed rate is near the judge's own error floor, and a rate above
    100% is not a number to hand anyone.
    """
    if observed != observed or not getattr(alignment, 'informative', False):
        return NAN
    value = (observed + alignment.tnr - 1) / (alignment.tpr + alignment.tnr - 1)
    return _clamp(value, 0.0, 1.0)
