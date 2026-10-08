import oryxflow
from oryxflow import core


class TaskManyLongParams(oryxflow.tasks.TaskCache):
    sector = oryxflow.Parameter(default='consumer_discretionary_and_staples')
    sectors = oryxflow.ListParameter(default=['energy', 'materials', 'industrials', 'utilities',
                                              'real_estate', 'health_care', 'financials'])
    subtypes = oryxflow.ListParameter(default=['residential', 'commercial', 'industrial', 'retail'])
    basis = oryxflow.Parameter(default='trailing_twelve_months_weighted_average')
    vintage = oryxflow.Parameter(default='2026-q3-final-revised-restated')

    def run(self):
        self.save(1)


def _summary(task_id, family):
    # strip "{family}_" and "_{hash}"
    return task_id[len(family) + 1:-(core.TASK_ID_TRUNCATE_HASH + 1)]


def test_long_params_summary_capped():
    t = TaskManyLongParams()
    assert len(_summary(t.task_id, 'TaskManyLongParams')) <= core.TASK_ID_TRUNCATE_SUMMARY
    assert len(t.task_id) <= len('TaskManyLongParams') + core.TASK_ID_TRUNCATE_SUMMARY \
        + core.TASK_ID_TRUNCATE_HASH + 2
    assert t.task_id.split('_')[0] == 'TaskManyLongParams'


def test_identical_truncated_summaries_get_different_ids():
    t1 = TaskManyLongParams(vintage='2026-q3-final-revised-restated-a')
    t2 = TaskManyLongParams(vintage='2026-q3-final-revised-restated-b')
    s1 = _summary(t1.task_id, 'TaskManyLongParams')
    s2 = _summary(t2.task_id, 'TaskManyLongParams')
    assert s1 == s2
    assert t1.task_id != t2.task_id


def test_short_summary_unchanged():
    assert core.task_id_str('Task1', {}) == 'Task1__99914b932b'
    tid = core.task_id_str('Task1', {'a': 'x', 'b': 'y'})
    assert tid.startswith('Task1_x_y_')
