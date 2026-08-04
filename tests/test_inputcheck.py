import os
import stat
import shutil
import warnings

import pytest
import pandas as pd

import oryxflow
from oryxflow import inputcheck


# ---- fixture tasks (module level so inspect.getsource can retrieve them) -----------------

class SrcA(oryxflow.tasks.TaskCachePandas):
    def run(self): self.save(pd.DataFrame({'v': [1]}))


class SrcB(oryxflow.tasks.TaskCachePandas):
    def run(self): self.save(pd.DataFrame({'v': [2]}))


class SrcC(oryxflow.tasks.TaskCachePandas):
    def run(self): self.save(pd.DataFrame({'v': [3]}))


@oryxflow.requires(SrcA, SrcB, SrcC)
class LoadsThreeUsesTwo(oryxflow.tasks.TaskCachePandas):
    def run(self):
        df_a, df_b, df_slow = self.inputLoad()          # df_slow never used again
        self.save(pd.concat([df_a, df_b]))


@oryxflow.requires(SrcA, SrcB)
class TopUnderscore(oryxflow.tasks.TaskCachePandas):
    def run(self):
        df_a, _ = self.inputLoad()
        self.save(df_a)


@oryxflow.requires(SrcA, SrcB)
class NestedUnderscoreOK(oryxflow.tasks.TaskCachePandas):
    def run(self):
        (df_a, _), df_b = self.inputLoad()               # inner _ is a persist key, not a finding
        self.save(pd.concat([df_a, df_b]))


@oryxflow.requires(SrcA, SrcB)
class ArityMismatch(oryxflow.tasks.TaskCachePandas):
    def run(self):
        data = self.inputLoad()
        self.save(data[0])


class NoRunAggregator(oryxflow.tasks.TaskAggregator):
    def requires(self): return [SrcA(), SrcB()]


@oryxflow.requires(SrcA, SrcB)
class KeyedNamesOne(oryxflow.tasks.TaskCachePandas):
    def run(self):
        df = self.inputLoad(task='SrcA')                 # SrcB never named
        self.save(df)


@oryxflow.requires(SrcA, SrcB)
class KeyedNamesBoth(oryxflow.tasks.TaskCachePandas):
    def run(self):
        a = self.inputLoad(task='SrcA')
        b = self.inputLoad(task='SrcB')
        self.save(pd.concat([a, b]))


@oryxflow.requires(SrcA, SrcB)
class Suppressed(oryxflow.tasks.TaskCachePandas):
    def run(self):
        df_a, df_b = self.inputLoad()  # oryxflow: input-unused
        self.save(df_a)


def _verdicts(cls):
    return {f.dep_family: f.verdict for f in inputcheck.check_class(cls)}


class TestInputCheck:

    def test_motivating_shape(self):
        findings = inputcheck.check_class(LoadsThreeUsesTwo)
        unused = [f for f in findings if f.verdict == 'unused']
        assert len(unused) == 1
        assert unused[0].dep_family == 'SrcC'
        assert unused[0].binding == 'df_slow'

    def test_top_level_underscore(self):
        findings = inputcheck.check_class(TopUnderscore)
        unused = [f for f in findings if f.verdict == 'unused']
        assert [f.dep_family for f in unused] == ['SrcB']
        assert unused[0].binding == '_'

    def test_nested_underscore_is_not_a_finding(self):
        # the false-positive guard that decides whether the lint is usable
        assert all(f.verdict == 'clean' for f in inputcheck.check_class(NestedUnderscoreOK))

    def test_arity_mismatch_is_unanalyzed(self):
        findings = inputcheck.check_class(ArityMismatch)
        assert all(f.verdict == 'unanalyzed' for f in findings)
        assert all('arity' in f.reason for f in findings)

    def test_no_run_is_omitted(self):
        assert inputcheck.check_class(NoRunAggregator) == []

    def test_keyed_loads(self):
        assert _verdicts(KeyedNamesOne) == {'SrcA': 'clean', 'SrcB': 'unused'}
        assert _verdicts(KeyedNamesBoth) == {'SrcA': 'clean', 'SrcB': 'clean'}

    def test_suppression_comment(self):
        # marker turns the otherwise-unused SrcB into clean
        assert all(f.verdict == 'clean' for f in inputcheck.check_class(Suppressed))


class TestInputCheckIntegration:
    pathdata = oryxflow.set_dir('data/')

    @pytest.fixture
    def cleanup(self):
        def onerror(func, p, exc_info):
            try:
                os.chmod(p, stat.S_IWRITE); func(p)
            except Exception:
                pass
        shutil.rmtree(self.pathdata, onerror=onerror)
        self.pathdata.mkdir(exist_ok=True)
        yield True
        shutil.rmtree(self.pathdata, onerror=onerror)

    def test_check_inputs_raises(self, cleanup):
        flow = oryxflow.Workflow(LoadsThreeUsesTwo)
        with pytest.raises(ValueError) as ei:
            flow.check_inputs(raise_on_unused=True)
        msg = str(ei.value)
        assert 'LoadsThreeUsesTwo' in msg and 'SrcC' in msg and 'test_inputcheck.py:' in msg

    def test_check_inputs_returns_unused_only(self, cleanup):
        flow = oryxflow.Workflow(LoadsThreeUsesTwo)
        unused = flow.check_inputs(print_it=False)
        assert [f.dep_family for f in unused] == ['SrcC']
        allrecs = flow.check_inputs(include_clean=True, print_it=False)
        assert len(allrecs) == 3

    def test_run_does_not_lint(self, cleanup):
        # the unused-input lint is preview-only: run() keeps the execution path free and never
        # populates RunResult.warnings with a dead-dependency finding (explicit check_inputs()
        # and preview() are where it surfaces). Also proves run() can't be aborted by the lint
        # under -W error, since it emits no UnusedInputWarning at all.
        flow = oryxflow.Workflow(LoadsThreeUsesTwo)
        with warnings.catch_warnings():
            warnings.simplefilter('error', inputcheck.UnusedInputWarning)
            result = flow.run()
        hits = [w for w in result.warnings if 'SrcC' in w and 'never uses it' in w]
        assert len(hits) == 0

    def test_preview_shows_unused_block(self, cleanup):
        out = oryxflow.preview(LoadsThreeUsesTwo(), print_it=False)
        assert 'UNUSED INPUTS' in out
        assert 'LoadsThreeUsesTwo' in out and 'SrcC' in out
