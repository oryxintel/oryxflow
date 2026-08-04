"""Traversal-scoped memos (see oryxflow/core.py + docs/todo/20260730-engine-traversal-scope.md).

The invariant every traversal must hold: ONE completeness/fingerprint execution per unique task,
ONE requires() resolution per task that has a spec -- not one per PATH through the DAG. The miss
counts (deterministic, unlike wall-clock) are what these assert on. Plus the correctness cases a
memo could make silently pass by returning a stale True.
"""
import pandas as pd
import pytest

import oryxflow
import oryxflow.state
import oryxflow.cache
from oryxflow import core


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated data dir + in-memory cache per test; memo flag restored after."""
    datadir = tmp_path / 'data'
    datadir.mkdir()
    monkeypatch.setattr(oryxflow.settings, 'dir', str(datadir))
    monkeypatch.setattr(oryxflow.settings, 'dirpath', datadir)
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    oryxflow.state.clear_cache()
    oryxflow.cache.data.clear()
    oryxflow.core._code_warned.clear()
    yield tmp_path
    core._traversal_memo_enabled = True


# --------------------------------------------------------------------------------------------
# A diamond fan-out over TaskCache targets (fast enough for the suite): 6 leaves -> 1 shared
# aggregator -> 8 fanned narratives -> 1 report that also depends on the shared aggregator.
# --------------------------------------------------------------------------------------------
MARKETS = ['m{}'.format(i) for i in range(8)]
LEAVES = list(range(6))


class MLeaf(oryxflow.tasks.TaskCache):
    i = oryxflow.IntParameter(default=0)
    def run(self):
        self.save(pd.DataFrame({'v': [self.i]}))


@oryxflow.requires_each(MLeaf, i=LEAVES)
class MAgg(oryxflow.tasks.TaskCache):
    def run(self):
        self.save(self.inputLoadConcat())


@oryxflow.requires(MAgg)
class MNarr(oryxflow.tasks.TaskCache):
    market = oryxflow.Parameter(default='m0')
    def run(self):
        self.save(self.inputLoad().assign(market=self.market))


@oryxflow.requires({'input': MAgg})
@oryxflow.requires_each(MNarr, market=MARKETS)
class MReport(oryxflow.tasks.TaskCache):
    def run(self):
        self.save(self.inputLoadConcat(task='MNarr'))


def _unique_tasks():
    return oryxflow.utils.traverse(MReport())


def _n_unique():
    return len(_unique_tasks())


def _n_with_spec():
    return len([t for t in _unique_tasks()
                if getattr(type(t).requires, '_oryxflow_generated', False)])


# 1. The win, as a regression test -------------------------------------------------------------

def test_noop_run_is_one_execution_per_task(env):
    flow = oryxflow.Workflow(MReport)
    flow.run()                       # cold: materialize everything
    flow.run()                       # no-op: this is the traversal under test
    s = core.traversal_stats()
    assert s['complete_miss'] == _n_unique()
    assert s['fingerprint_miss'] == _n_unique()
    assert s['requires_miss'] == _n_with_spec()


def test_preview_is_one_execution_per_task(env):
    flow = oryxflow.Workflow(MReport)
    flow.run()
    oryxflow.preview(MReport(), print_it=False)
    s = core.traversal_stats()
    assert s['complete_miss'] == _n_unique()
    assert s['fingerprint_miss'] == _n_unique()
    assert s['requires_miss'] == _n_with_spec()


# 2. Hits actually happen ----------------------------------------------------------------------

def test_memo_actually_hits(env):
    flow = oryxflow.Workflow(MReport)
    flow.run()
    flow.run()
    s = core.traversal_stats()
    # a memo present but never hit would satisfy case 1 while doing nothing
    assert s['complete_hit'] > 0
    # the fingerprint is the deeper recursion, so it hits strictly more than completeness
    assert s['fingerprint_hit'] > s['complete_hit']


# 3. Equivalence with the memo off -------------------------------------------------------------

def test_preview_text_identical_memo_on_off(env):
    oryxflow.Workflow(MReport).run()
    core._traversal_memo_enabled = True
    on = oryxflow.preview(MReport(), print_it=False)
    core._traversal_memo_enabled = False
    off = oryxflow.preview(MReport(), print_it=False)
    assert on == off


def test_runresult_identical_memo_on_off(env):
    # a partially-complete DAG: materialize the leaves + aggregator, leave the rest
    oryxflow.run(MAgg())

    def counts():
        r = oryxflow.run(MReport())
        return (len(r.ran), len(r.complete), r.reasons, r.warnings)

    # reset downstream so the second run has the same starting state
    def fresh_counts(flag):
        oryxflow.cache.data.clear()
        oryxflow.state.clear_cache()
        oryxflow.run(MAgg())
        core._traversal_memo_enabled = flag
        return counts()

    on = fresh_counts(True)
    off = fresh_counts(False)
    assert on[0] == off[0]           # len(ran)
    assert on[1] == off[1]           # len(complete)
    assert on[2] == off[2]           # reasons
    assert on[3] == off[3]           # warnings


# 4. A dependency that reruns mid-build invalidates downstream ---------------------------------

def test_dependency_rerun_midbuild_reruns_downstream(env):
    runs = []

    class LA(oryxflow.tasks.TaskCache):
        code_version = '1'
        def run(self):
            runs.append('A'); self.save(pd.DataFrame({'v': [1]}))

    @oryxflow.requires(LA)
    class LB(oryxflow.tasks.TaskCache):
        code_version = '1'
        def run(self):
            runs.append('B'); self.save(self.inputLoad())

    @oryxflow.requires(LB)
    class LC(oryxflow.tasks.TaskCache):
        code_version = '1'
        def run(self):
            runs.append('C'); self.save(self.inputLoad())

    oryxflow.run(LC())               # all three run and complete
    runs.clear()
    LB.code_version = '2'            # B is now stale
    oryxflow.run(LC())
    # B must rerun, and C -- which was complete before the build began -- must rerun after it,
    # because the volatile memo was cleared when B materialized
    assert 'B' in runs and 'C' in runs


# 5. save()/materialization inside a run() body (flow-within-a-flow) ---------------------------

def test_flow_within_a_flow_invalidates_downstream(env):
    runs = []

    class FSource(oryxflow.tasks.TaskCache):
        def run(self):
            runs.append('S'); self.save(pd.DataFrame({'v': [1]}))

    @oryxflow.requires(FSource)
    class FConsumer(oryxflow.tasks.TaskCache):
        def run(self):
            runs.append('C'); self.save(self.inputLoad())

    class FDriver(oryxflow.tasks.TaskCache):
        # materializes FSource from inside its own run() (documented flow-within-a-flow)
        def run(self):
            runs.append('D')
            oryxflow.run(FSource())
            self.save(pd.DataFrame({'ok': [1]}))

    # one build with FDriver and FConsumer both present; FSource starts incomplete
    r = oryxflow.run([FDriver(), FConsumer()])
    assert r.scheduling_succeeded
    assert FSource().complete(cascade=False)     # driver materialized it
    assert 'C' in runs                            # consumer still ran, downstream of FSource


# 6. invalidate() clears an open scope ---------------------------------------------------------

def test_invalidate_clears_open_scope(env):
    oryxflow.run(MAgg())
    t = MAgg()
    with core.traversal_scope():
        assert t.complete()
        t.reset(confirm=False)
        assert not t.complete()      # a stale memo would still say True here


# 7. cascade=False is never memoized -----------------------------------------------------------

def test_cascade_false_never_memoized(env):
    class Solo(oryxflow.tasks.TaskCache):
        def run(self):
            self.save(pd.DataFrame({'v': [1]}))

    t = Solo()
    with core.traversal_scope():
        assert not t.complete(cascade=False)
        t.run()                                   # materialize via the in-memory cache
        assert t.complete(cascade=False)          # outputLoad's guard depends on this


# 8. Two flows, one task_id, two directories ---------------------------------------------------

def test_two_flows_two_directories(env, tmp_path):
    class PSource(oryxflow.tasks.TaskPickle):
        def run(self):
            self.save({'v': 1})

    dir_a = str(tmp_path / 'flow_a')
    dir_b = str(tmp_path / 'flow_b')
    flow_a = oryxflow.Workflow(PSource, path=dir_a)
    flow_b = oryxflow.Workflow(PSource, path=dir_b)

    flow_a.run()
    flow_b.run()                                  # must actually run, not read flow_a's output

    assert flow_a.complete()
    assert flow_b.complete()
    from oryxflow import targets  # noqa: F401
    import pathlib
    assert pathlib.Path(dir_a).exists()
    assert pathlib.Path(dir_b).exists()
    # the two outputs live in different directories despite an identical task_id
    assert flow_a.get_task().task_id == flow_b.get_task().task_id


# 9. A code_version bump between two traversals is seen -----------------------------------------

def test_code_version_bump_between_traversals(env):
    runs = []

    class VTask(oryxflow.tasks.TaskCache):
        code_version = '1'
        def run(self):
            runs.append(1); self.save(pd.DataFrame({'v': [1]}))

    oryxflow.run(VTask())
    assert runs == [1]
    VTask.code_version = '2'                       # bump BETWEEN traversals
    oryxflow.run(VTask())
    # the fingerprint memo is per-traversal, never per-instance, so the bump is seen
    assert runs == [1, 1]


# 10. Nesting ----------------------------------------------------------------------------------

def test_nested_scopes_share_one_memo(env):
    oryxflow.run(MAgg())
    t = MAgg()
    with core.traversal_scope():
        with core.traversal_scope():
            assert t.complete()
        # inner exit did NOT drop the shared memo
        assert core._traversal.complete is not None
        s = core.traversal_stats()
        assert s['complete_miss'] >= 1
    # only the outermost exit drops it
    assert core._traversal.complete is None
