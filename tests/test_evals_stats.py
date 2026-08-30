"""Confidence intervals over hand-built DataFrames.

Deliberately imports **no pydantic-evals object**: everything downstream of an
eval run reads only the per-case frame, so these are seam tests as much as unit
tests. Every bootstrap is seeded and uses a small ``n_resamples`` so the suite
stays deterministic and fast.
"""
import pandas as pd
import pytest

pytest.importorskip('pydantic_evals')  # only for the oryxflow.evals package guard
pytest.importorskip('scipy')

from oryxflow.evals.metric import Metric
from oryxflow.evals.stats import delta_ci, rate_ci
from oryxflow.evals.stats import corrected_rate, judge_alignment

N = 300  # resamples; enough for a stable interval, small enough to stay quick
ACC = Metric('accuracy', 'ok')


def frame(outcomes, reps=1, arm=None, arm_col='arm', distinct_cases=False):
    """One row per (case, rep). ``distinct_cases`` gives every row its own case
    name, which is what an un-clustered implementation effectively assumes."""
    rows = []
    for i, ok in enumerate(outcomes):
        for r in range(reps):
            row = {'case_name': 'case{}-{}'.format(i, r),
                   'source_case_name': 'case{}-{}'.format(i, r) if distinct_cases
                   else 'case{}'.format(i),
                   'rep': r, 'ok': bool(ok), 'error': ''}
            if arm is not None:
                row[arm_col] = arm
            rows.append(row)
    return pd.DataFrame(rows)


OUTCOMES = [1] * 14 + [0] * 8  # 22 cases, 14 hits


# --- 9. degenerate rates ----------------------------------------------------

def test_all_cases_agree_gives_a_clamped_interval_not_a_point():
    ci = rate_ci(frame([1] * 22, reps=3), ACC, n_resamples=N)
    assert ci.value == 1.0
    assert ci.method == 'wilson'          # BCa degenerates when every resample agrees
    assert ci.high == 1.0                 # clamped, never above 1
    assert ci.low < 1.0                   # and NOT [1.0, 1.0] from 22 cases
    assert 0.7 < ci.low < 0.95
    assert ci.n == 66 and ci.n_clusters == 22


def test_all_cases_fail_gives_a_clamped_interval_not_a_point():
    ci = rate_ci(frame([0] * 22), ACC, n_resamples=N)
    assert ci.value == 0.0
    assert ci.method == 'wilson'
    assert ci.low == 0.0                  # clamped, never below 0
    assert 0.0 < ci.high < 0.3


def test_wilson_fallback_uses_cases_not_rows():
    # 22 all-passing cases at 1 rep and at 3 reps must report the SAME interval:
    # 66 correlated rows are not 66 pieces of evidence.
    one = rate_ci(frame([1] * 22, reps=1), ACC, n_resamples=N)
    three = rate_ci(frame([1] * 22, reps=3), ACC, n_resamples=N)
    assert one.low == pytest.approx(three.low)


def test_ordinary_rate_uses_bca_and_brackets_the_value():
    ci = rate_ci(frame(OUTCOMES), ACC, n_resamples=N)
    assert ci.method == 'bca'
    assert ci.low <= ci.value <= ci.high
    assert 0.0 <= ci.low and ci.high <= 1.0
    assert (ci.n, ci.n_clusters, ci.numerator) == (22, 22, 14)


def test_empty_frame_reports_no_interval():
    ci = rate_ci(frame(OUTCOMES).iloc[:0], ACC, n_resamples=N)
    assert ci.method == 'empty'
    assert ci.n_clusters == 0
    assert ci.low != ci.low  # nan


# --- 10. the clustering property -------------------------------------------

def test_repeats_do_not_narrow_the_interval():
    """The test that catches an un-clustered implementation.

    Same 22 cases, same per-case outcome, run once vs three times. Repeats of one
    case are correlated, so three runs are not three independent samples and the
    interval must not tighten. An implementation that resampled ROWS would report
    it about sqrt(3) narrower -- see the positive control below, which measures
    exactly that on an otherwise identical 66-row frame.
    """
    one = rate_ci(frame(OUTCOMES, reps=1), ACC, n_resamples=N)
    three = rate_ci(frame(OUTCOMES, reps=3), ACC, n_resamples=N)

    assert one.n == 22 and three.n == 66          # three times the rows
    assert one.n_clusters == three.n_clusters == 22  # but the same 22 cases
    assert one.value == pytest.approx(three.value)

    w1, w3 = one.high - one.low, three.high - three.low
    # Tolerance: clustering makes the two bootstraps identical by construction (the
    # cluster ratio 3k/3n equals k/n), so this is really an equality check. 5% is
    # slack for float noise only -- an order of magnitude below the ~42% narrowing
    # (1 - 1/sqrt(3)) that an un-clustered implementation would produce.
    assert w3 == pytest.approx(w1, rel=0.05)


def test_positive_control_row_level_sampling_would_narrow_it():
    """Proof the test above has teeth: the same 66 outcomes as 66 INDEPENDENT
    cases -- what an un-clustered bootstrap effectively assumes -- do narrow the
    interval substantially."""
    clustered = rate_ci(frame(OUTCOMES, reps=3), ACC, n_resamples=N)
    independent = rate_ci(frame(OUTCOMES * 3, reps=1, distinct_cases=True), ACC,
                          n_resamples=N)
    w_clustered = clustered.high - clustered.low
    w_independent = independent.high - independent.low
    assert w_independent < 0.75 * w_clustered


def test_case_name_is_the_cluster_when_there_is_no_source_case_name():
    df = frame(OUTCOMES, reps=3).drop(columns=['source_case_name'])
    ci = rate_ci(df, ACC, n_resamples=N)
    assert ci.n_clusters == 66  # every row its own case, because that is all we know
    assert ci.n == 66


def test_blank_source_case_name_falls_back_to_case_name():
    df = frame(OUTCOMES, reps=1)
    df['source_case_name'] = None
    ci = rate_ci(df, ACC, n_resamples=N)
    assert ci.n_clusters == 22


def test_errored_rows_are_outside_the_clusters_too():
    df = frame(OUTCOMES, reps=1)
    df.loc[df.index[:4], 'error'] = 'RateLimitError: 429'
    ci = rate_ci(df, ACC, n_resamples=N)
    assert ci.n == 18 and ci.n_clusters == 18


# --- 11. the paired delta ---------------------------------------------------

def two_arms(a_outcomes, b_outcomes, reps=1, arm_col='arm'):
    return pd.concat([frame(a_outcomes, reps, 'new', arm_col),
                      frame(b_outcomes, reps, 'old', arm_col)], ignore_index=True)


def test_delta_contains_the_constant_difference():
    # 20/22 vs 11/22 -- the arms differ by a constant 9/22 on the same cases.
    constant = 9 / 22
    df = two_arms([1] * 20 + [0] * 2, [1] * 11 + [0] * 11, reps=3)
    d = delta_ci(df, ACC, 'new', 'old', n_resamples=N)
    assert d.value == pytest.approx(constant)
    assert d.low <= constant <= d.high
    assert d.method == 'bca'
    assert not d.spans_zero
    assert d.n_a == 66 and d.n_b == 66 and d.n_clusters == 22


def test_delta_is_clustered_by_case_as_well():
    one = delta_ci(two_arms([1] * 20 + [0] * 2, [1] * 11 + [0] * 11, reps=1),
                   ACC, 'new', 'old', n_resamples=N)
    three = delta_ci(two_arms([1] * 20 + [0] * 2, [1] * 11 + [0] * 11, reps=3),
                     ACC, 'new', 'old', n_resamples=N)
    assert (three.high - three.low) == pytest.approx(one.high - one.low, rel=0.05)


def test_delta_spans_zero_when_the_arms_barely_differ():
    df = two_arms([1] * 11 + [0] * 11, [1] * 12 + [0] * 10)
    d = delta_ci(df, ACC, 'new', 'old', n_resamples=N)
    assert d.spans_zero
    assert d.low <= 0 <= d.high


def test_identical_arms_do_not_report_a_zero_width_delta():
    df = two_arms([1] * 22, [1] * 22)
    d = delta_ci(df, ACC, 'new', 'old', n_resamples=N)
    assert d.value == 0.0
    assert d.method == 'newcombe'   # BCa degenerates; the fallback keeps a width
    assert d.high > 0 > d.low
    assert d.spans_zero


def test_delta_is_clamped_to_plus_minus_one():
    d = delta_ci(two_arms([1] * 22, [0] * 22), ACC, 'new', 'old', n_resamples=N)
    assert d.value == 1.0
    assert -1.0 <= d.low and d.high <= 1.0


def test_arm_column_is_inferred_from_the_arm_values():
    # A sweep tags rows with the arm PARAMETER's name, not a column called 'arm'.
    df = two_arms([1] * 20 + [0] * 2, [1] * 11 + [0] * 11, arm_col='prompt_version')
    d = delta_ci(df, ACC, 'new', 'old', n_resamples=N)
    assert d.value == pytest.approx(9 / 22)


def test_arm_column_can_be_named_explicitly():
    df = two_arms([1] * 20 + [0] * 2, [1] * 11 + [0] * 11, arm_col='prompt_version')
    d = delta_ci(df, ACC, 'new', 'old', arm_col='prompt_version', n_resamples=N)
    assert d.value == pytest.approx(9 / 22)
    with pytest.raises(KeyError):
        delta_ci(df, ACC, 'new', 'old', arm_col='model_id', n_resamples=N)


def test_ambiguous_arm_column_raises_rather_than_guessing():
    df = two_arms([1] * 20 + [0] * 2, [1] * 11 + [0] * 11, arm_col='prompt_version')
    df['model_id'] = df['prompt_version']
    with pytest.raises(KeyError) as e:
        delta_ci(df, ACC, 'new', 'old', n_resamples=N)
    assert 'arm_col' in str(e.value)


def test_delta_needs_shared_cases():
    a = frame([1] * 5, arm='new')
    b = frame([0] * 5, arm='old')
    b['source_case_name'] = ['other{}'.format(i) for i in range(len(b))]
    with pytest.raises(ValueError) as e:
        delta_ci(pd.concat([a, b], ignore_index=True), ACC, 'new', 'old', n_resamples=N)
    assert 'share no case' in str(e.value)


def test_delta_pairs_on_the_cases_both_arms_ran():
    # 'old' is missing two cases: the paired comparison uses the 20 shared ones.
    df = two_arms([1] * 20 + [0] * 2, [1] * 11 + [0] * 11)
    df = df[~((df['arm'] == 'old') & (df['source_case_name'].isin(['case0', 'case1'])))]
    d = delta_ci(df, ACC, 'new', 'old', n_resamples=N)
    assert d.n_clusters == 20
    assert d.n_a == 20 and d.n_b == 20


# --- callable columns -------------------------------------------------------

def test_callable_column_gets_an_interval_too():
    df = frame(OUTCOMES, reps=3)
    df['task_duration_s'] = [1.0 + (i % 5) for i in range(len(df))]
    latency = Metric('latency', lambda f: float(f['task_duration_s'].mean()),
                     higher_is_better=False)
    ci = rate_ci(df, latency, n_resamples=40)
    assert ci.method == 'bca'
    assert ci.low <= ci.value <= ci.high
    assert ci.high > 1.0          # not clamped to [0, 1]: it is not a rate
    assert ci.numerator is None


def test_callable_column_delta():
    df = two_arms(OUTCOMES, OUTCOMES)
    df['task_duration_s'] = [1.0] * 22 + [3.0] * 22
    latency = Metric('latency', lambda f: float(f['task_duration_s'].mean()))
    d = delta_ci(df, latency, 'new', 'old', n_resamples=40)
    assert d.value == pytest.approx(-2.0)


# ------------------------------- judging the judge

def test_alignment_reports_tpr_tnr_and_kappa():
    df = pd.DataFrame({'judge': [True] * 45 + [False] * 5 + [False] * 40 + [True] * 10,
                       'human': [True] * 50 + [False] * 50})
    a = judge_alignment(df, 'judge', 'human')
    assert a.tpr == 0.9 and a.tnr == 0.8
    assert a.n == 100 and a.informative


def test_raw_agreement_lies_under_class_imbalance_and_kappa_does_not():
    """A judge that says pass to everything scores 90% agreement on a 90%-pass set
    and has learnt nothing. This is why kappa is what gets read."""
    df = pd.DataFrame({'judge': [True] * 100, 'human': [True] * 90 + [False] * 10})
    a = judge_alignment(df, 'judge', 'human')
    assert a.agreement == 0.9
    assert abs(a.kappa) < 1e-9
    assert not a.informative


def test_an_uninformative_judge_is_never_corrected():
    """tpr + tnr <= 1 puts the estimator's denominator at or below zero; the
    'correction' would be noise, amplified."""
    df = pd.DataFrame({'judge': [True] * 100, 'human': [True] * 90 + [False] * 10})
    assert corrected_rate(0.7, judge_alignment(df, 'judge', 'human')) != \
        corrected_rate(0.7, judge_alignment(df, 'judge', 'human'))   # NaN


def test_correction_undoes_a_known_judge_error():
    """An imperfect judge pulls every rate toward its own error floor; correcting
    pushes it back."""
    df = pd.DataFrame({'judge': [True] * 45 + [False] * 5 + [False] * 40 + [True] * 10,
                       'human': [True] * 50 + [False] * 50})
    a = judge_alignment(df, 'judge', 'human')          # tpr .9, tnr .8
    # a true rate of 0.5 is observed by this judge as .5*.9 + .5*.2 = 0.55
    assert abs(corrected_rate(0.55, a) - 0.5) < 1e-9


def test_correction_is_clipped_to_a_real_rate():
    df = pd.DataFrame({'judge': [True] * 45 + [False] * 5 + [False] * 40 + [True] * 10,
                       'human': [True] * 50 + [False] * 50})
    a = judge_alignment(df, 'judge', 'human')
    assert corrected_rate(0.99, a) <= 1.0
    assert corrected_rate(0.01, a) >= 0.0


def test_a_small_label_set_says_so():
    df = pd.DataFrame({'judge': [True] * 8 + [False] * 2, 'human': [True] * 10})
    assert 'too noisy' in judge_alignment(df, 'judge', 'human').note


def test_labels_all_one_class_is_flagged():
    df = pd.DataFrame({'judge': [True] * 60, 'human': [True] * 60})
    a = judge_alignment(df, 'judge', 'human')
    assert 'all one class' in a.note


def test_rows_without_a_human_label_are_ignored():
    df = pd.DataFrame({'judge': [True, False, True, False],
                       'human': [True, False, None, None]})
    assert judge_alignment(df, 'judge', 'human').n == 2
