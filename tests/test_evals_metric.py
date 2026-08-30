"""Metric over hand-built DataFrames.

Deliberately imports **no pydantic-evals object**. Everything downstream of an
eval run reads only the per-case frame (the seam), so proving Metric works on a
frame someone typed out by hand is also proving the seam holds.
"""
import pandas as pd
import pytest

pytest.importorskip('pydantic_evals')  # only for the oryxflow.evals package guard

from oryxflow.evals.metric import Metric


def frame(rows):
    return pd.DataFrame(rows)


CASES = frame([
    {'case_name': 'a', 'wrote': True, 'needs_web': True, 'error': ''},
    {'case_name': 'b', 'wrote': False, 'needs_web': True, 'error': ''},
    {'case_name': 'c', 'wrote': True, 'needs_web': True, 'error': ''},
    {'case_name': 'd', 'wrote': True, 'needs_web': False, 'error': ''},
    {'case_name': 'e', 'wrote': False, 'needs_web': False, 'error': ''},
])


def test_compute_plain_rate():
    value, n, numerator = Metric('wrote', 'wrote').compute(CASES)
    assert (value, n, numerator) == (0.6, 5, 3)


def test_compute_where_column():
    value, n, numerator = Metric('wrote', 'wrote', where='needs_web').compute(CASES)
    assert (value, n, numerator) == (2 / 3, 3, 2)


def test_compute_where_negated_column():
    value, n, numerator = Metric('wrote', 'wrote', where='~needs_web').compute(CASES)
    assert (value, n, numerator) == (0.5, 2, 1)


def test_where_and_negation_partition_the_frame():
    _, n_yes, k_yes = Metric('m', 'wrote', where='needs_web').compute(CASES)
    _, n_no, k_no = Metric('m', 'wrote', where='~needs_web').compute(CASES)
    _, n_all, k_all = Metric('m', 'wrote').compute(CASES)
    assert n_yes + n_no == n_all
    assert k_yes + k_no == k_all


def test_where_missing_column_names_the_metric():
    with pytest.raises(KeyError) as e:
        Metric('yield', 'wrote', where='nope').compute(CASES)
    assert 'yield' in str(e.value) and 'nope' in str(e.value)


def test_callable_column():
    # Not a rate: a mean over a numeric column, computed on the metric's subset.
    df = CASES.assign(task_duration_s=[1.0, 2.0, 3.0, 10.0, 20.0])
    metric = Metric('latency', lambda f: float(f['task_duration_s'].mean()),
                    higher_is_better=False)
    value, n, numerator = metric.compute(df)
    assert value == pytest.approx(7.2)
    assert n == 5
    assert numerator is None


def test_callable_column_sees_the_where_subset():
    df = CASES.assign(task_duration_s=[1.0, 2.0, 3.0, 10.0, 20.0])
    metric = Metric('latency', lambda f: float(f['task_duration_s'].mean()),
                    where='needs_web')
    value, n, _ = metric.compute(df)
    assert value == pytest.approx(2.0)
    assert n == 3


def test_errors_are_excluded_from_the_denominator():
    # A case that raised is not evidence either way: counting it as a miss would
    # let a credential outage read as a quality regression.
    df = frame([
        {'case_name': 'a', 'wrote': True, 'error': ''},
        {'case_name': 'b', 'wrote': True, 'error': ''},
        {'case_name': 'c', 'wrote': False, 'error': 'RateLimitError: 429'},
        {'case_name': 'd', 'wrote': False, 'error': 'RateLimitError: 429'},
    ])
    value, n, numerator = Metric('wrote', 'wrote').compute(df)
    assert (value, n, numerator) == (1.0, 2, 2)


def test_errors_excluded_before_where_is_applied():
    df = frame([
        {'case_name': 'a', 'wrote': True, 'needs_web': True, 'error': ''},
        {'case_name': 'b', 'wrote': False, 'needs_web': True, 'error': 'boom'},
        {'case_name': 'c', 'wrote': False, 'needs_web': False, 'error': ''},
    ])
    value, n, numerator = Metric('wrote', 'wrote', where='needs_web').compute(df)
    assert (value, n, numerator) == (1.0, 1, 1)


def test_subset_without_an_error_column():
    df = CASES.drop(columns=['error'])
    assert len(Metric('wrote', 'wrote').subset(df)) == 5


def test_empty_subset_is_nan_not_zero():
    df = CASES[CASES['case_name'] == 'zzz']
    value, n, numerator = Metric('wrote', 'wrote').compute(df)
    assert value != value  # nan: no evidence, which is not the same as a rate of 0
    assert (n, numerator) == (0, 0)


def test_over_budget_direction():
    guardrail = Metric('false positives', 'fp', higher_is_better=False, budget=0.05)
    assert guardrail.over_budget(0.08)
    assert not guardrail.over_budget(0.05)
    assert not guardrail.over_budget(0.0)

    floor = Metric('coverage', 'wrote', higher_is_better=True, budget=0.9)
    assert floor.over_budget(0.8)
    assert not floor.over_budget(0.95)

    assert not Metric('no budget', 'wrote').over_budget(0.0)
    assert not guardrail.over_budget(float('nan'))


# ------------------------------- unmeasured rows are not failures

# An LLM judge that grades only some rows -- or was switched off -- leaves nulls
# behind. Counting those as misses deflates the rate AND pads the denominator, so
# a prompt scoring 2/2 on the rows anyone actually judged reads as 33%.
JUDGED = frame([
    {'case_name': 'a', 'pairing': True, 'scored': True, 'error': ''},
    {'case_name': 'b', 'pairing': True, 'scored': True, 'error': ''},
    {'case_name': 'c', 'pairing': None, 'scored': True, 'error': ''},
    {'case_name': 'd', 'pairing': None, 'scored': True, 'error': ''},
])


def test_unmeasured_rows_leave_the_rate_alone():
    value, n, numerator = Metric('pairing', 'pairing').compute(JUDGED)
    assert (value, n, numerator) == (1.0, 2, 2)


def test_unmeasured_rows_are_counted_and_reportable():
    assert Metric('pairing', 'pairing').unmeasured(JUDGED) == 2


def test_a_wholly_unmeasured_metric_is_nan_not_zero():
    """Switching the judge off must not read as 'the model failed every case'."""
    blank = frame([{'case_name': 'a', 'pairing': None, 'error': ''},
                   {'case_name': 'b', 'pairing': None, 'error': ''}])
    value, n, numerator = Metric('pairing', 'pairing').compute(blank)
    assert value != value          # NaN -> renders as n/a
    assert (n, numerator) == (0, 0)


def test_unmeasured_is_counted_within_the_where_subset_only():
    rows = frame([
        {'case_name': 'a', 'pairing': None, 'scored': True, 'error': ''},
        {'case_name': 'b', 'pairing': None, 'scored': False, 'error': ''},
    ])
    assert Metric('pairing', 'pairing', where='scored').unmeasured(rows) == 1


def test_an_errored_row_is_not_also_counted_as_unmeasured():
    """It is already out of the rate for being an error; counting it twice would
    overstate how much went unjudged."""
    rows = frame([{'case_name': 'a', 'pairing': None, 'error': 'boom'},
                  {'case_name': 'b', 'pairing': True, 'error': ''}])
    assert Metric('pairing', 'pairing').unmeasured(rows) == 0
    assert Metric('pairing', 'pairing').compute(rows) == (1.0, 1, 1)


def test_false_is_still_a_miss_not_an_unmeasured_row():
    rows = frame([{'case_name': 'a', 'pairing': False, 'error': ''},
                  {'case_name': 'b', 'pairing': True, 'error': ''}])
    assert Metric('pairing', 'pairing').unmeasured(rows) == 0
    assert Metric('pairing', 'pairing').compute(rows) == (0.5, 2, 1)
