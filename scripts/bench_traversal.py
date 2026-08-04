"""Traversal cost of a fan-out DAG: how many times does the engine ask each question?

    python scripts/bench_traversal.py --no-memo      # baseline
    python scripts/bench_traversal.py --memo         # with the traversal-scoped memos

Reports calls/executions for the three recursive engine questions per traversal. Executions
are the metric: they are deterministic, where wall-clock on the same machine varies ~30% for
identical work. The invariant after the change is ONE execution per unique task.
"""
import sys
import time
import shutil
import collections

import pandas as pd

import oryxflow
from oryxflow import core
from oryxflow.tasks import TaskData

MARKETS = ['m{}'.format(i) for i in range(41)]     # 41 branches, as reported in the field
LEAVES = list(range(32))                           # one shared aggregator over 32 leaves
DATA = 'data-bench-traversal/'


class BLeaf(oryxflow.tasks.TaskPqPandas):
    i = oryxflow.IntParameter(default=0)
    def run(self):
        self.save(pd.DataFrame({'v': [self.i]}))


@oryxflow.requires_each(BLeaf, i=LEAVES)
class BAgg(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(self.inputLoadConcat())


@oryxflow.requires(BAgg)
class BNarr(oryxflow.tasks.TaskPqPandas):
    market = oryxflow.Parameter(default='m0')
    def run(self):
        self.save(self.inputLoad().assign(market=self.market))


@oryxflow.requires({'input': BAgg})                # the diamond: shared dep + fan-out
@oryxflow.requires_each(BNarr, market=MARKETS)
class BReport(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(self.inputLoadConcat(task='BNarr'))


# --- instrumentation: count invocations at the seam, executions at the uncached body. Before
# the change the split names don't exist, so executions == invocations and the table still lines
# up row for row.
C = collections.Counter()
_complete = TaskData.complete
_check = getattr(TaskData, '_complete_check', None)
_resolve = core._resolve_requires
_resolve_un = getattr(core, '_resolve_requires_uncached', None)
_fp = core.Task._code_fingerprint.fget
_fp_un = getattr(core.Task, '_code_fingerprint_compute', None)


def _install():
    def complete(self, cascade=True):
        C['c_call'] += 1
        return _complete(self, cascade=cascade)
    TaskData.complete = complete

    def resolve(task):
        C['r_call'] += 1
        return _resolve(task)
    core._resolve_requires = resolve
    core._spec_requires.__globals__['_resolve_requires'] = resolve

    def fingerprint(self):
        C['f_call'] += 1
        return _fp(self)
    core.Task._code_fingerprint = property(fingerprint)

    if _check is not None:
        def check(self, cascade):
            C['c_exec'] += 1
            return _check(self, cascade)
        TaskData._complete_check = check
    if _resolve_un is not None:
        def resolve_un(task):
            C['r_exec'] += 1
            return _resolve_un(task)
        core._resolve_requires_uncached = resolve_un
    if _fp_un is not None:
        def fp_un(self):
            C['f_exec'] += 1
            return _fp_un(self)
        core.Task._code_fingerprint_compute = fp_un


def measure(label, fn):
    C.clear()
    t = time.perf_counter()
    fn()
    elapsed = time.perf_counter() - t
    print('  {:<24} {:>6.2f}s  complete {:>6,}/{:>5,}  requires {:>5,}/{:>4,}  '
          'fingerprint {:>7,}/{:>5,}'.format(
              label, elapsed,
              C['c_call'], C['c_exec'] or C['c_call'],
              C['r_call'], C['r_exec'] or C['r_call'],
              C['f_call'], C['f_exec'] or C['f_call']))


def _recursive_complete():
    with core.traversal_scope():
        return BReport().complete()


def main():
    memo = '--no-memo' not in sys.argv
    oryxflow.settings.log_level = 'WARNING'
    core._traversal_memo_enabled = memo
    shutil.rmtree(DATA, ignore_errors=True)
    oryxflow.set_dir(DATA)
    _install()
    flow = oryxflow.Workflow(BReport)
    n = 1 + len(MARKETS) + 1 + len(LEAVES)
    print('traversal_memo={}   {} tasks   calls/executions'.format(memo, n))
    try:
        measure('cold run()', flow.run)
        measure('no-op run()', flow.run)
        # a recursive complete() TRAVERSAL -- the scoped entry a user hits via flow.complete().
        # A bare BReport().complete() opens no scope (design decision 1: unmemoized outside a
        # traversal, so user code calling complete() in a loop never reads a stale answer); the
        # scope is a no-op when --no-memo, so this stays a faithful baseline in both modes.
        measure('recursive complete()', _recursive_complete)
        measure('preview()', lambda: oryxflow.preview(BReport(), print_it=False))
        BAgg.code_version = '2'                    # invalidate one shared upstream
        measure('partial rerun', flow.run)
    finally:
        shutil.rmtree(DATA, ignore_errors=True)


if __name__ == '__main__':
    main()
