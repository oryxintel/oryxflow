"""One Parameter object shared by many task classes (the prod-defaults pattern), the warning when
two upstreams declare one parameter with different defaults, and the explicit-default rule for
exported tasks."""
import datetime
import warnings

import pandas as pd
import pytest

import oryxflow
from oryxflow import export as oexport
from oryxflow.core import ParameterDefaultConflictWarning

D5, D9 = datetime.date(2026, 11, 5), datetime.date(2026, 9, 1)


@pytest.fixture
def tmpdir_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(oryxflow.settings, 'dirpath', tmp_path / 'data')
    monkeypatch.setattr(oryxflow.settings, 'eventspath', tmp_path / '.oryxflow')
    oryxflow.state.clear_cache()
    oryxflow.core._code_warned.clear()
    oexport._warned.clear()
    return tmp_path


def _chain(refresh_a, refresh_b, refresh_c):
    class SharedA(oryxflow.tasks.TaskPqPandas):
        refresh = refresh_a

        def run(self):
            self.save(pd.DataFrame({'refresh': [str(self.refresh)]}))

    @oryxflow.requires(SharedA)
    class SharedB(oryxflow.tasks.TaskPqPandas):
        refresh = refresh_b

        def run(self):
            self.save(self.inputLoad())

    @oryxflow.requires(SharedB)
    class SharedC(oryxflow.tasks.TaskPqPandas):
        refresh = refresh_c

        def run(self):
            self.save(self.inputLoad())
    return SharedA, SharedB, SharedC


class TestSharedParameterObject:

    def test_one_object_on_three_classes(self, tmpdir_flow):
        refresh = oryxflow.DateParameter(default=D5)
        A, B, C = _chain(refresh, refresh, refresh)
        assert A.refresh is B.refresh is C.refresh
        c = C()
        assert c.refresh == D5
        assert c.requires().refresh == D5 and c.requires().requires().refresh == D5

        flow = oryxflow.Workflow(C)
        flow.run()
        assert flow.outputLoad(A)['refresh'].tolist() == [str(D5)]

        # a Workflow param still overrides the shared default, for every task
        flow9 = oryxflow.Workflow(C, {'refresh': D9})
        flow9.run()
        assert flow9.outputLoad(A)['refresh'].tolist() == [str(D9)]
        assert flow9.get_task().refresh == D9
        assert refresh._default == D5      # the shared object is not changed by a run

    def test_task_ids_match_per_class_declarations(self, tmpdir_flow):
        refresh = oryxflow.DateParameter(default=D5)
        shared = [t() for t in _chain(refresh, refresh, refresh)]
        own = [t() for t in _chain(*(oryxflow.DateParameter(default=D5) for _ in range(3)))]
        assert [t.task_id for t in shared] == [t.task_id for t in own]

    def test_requires_injects_the_shared_object(self, tmpdir_flow):
        refresh = oryxflow.DateParameter(default=D5)

        class Up(oryxflow.tasks.TaskPqPandas):
            refresh = oryxflow.Parameter()   # replaced below
        Up.refresh = refresh

        @oryxflow.requires(Up)
        class Down(oryxflow.tasks.TaskPqPandas):
            pass
        assert Down.refresh is refresh


def _two_upstreams(default_a, default_b):
    class UpA(oryxflow.tasks.TaskPqPandas):
        refresh = oryxflow.DateParameter(default=default_a)

    class UpB(oryxflow.tasks.TaskPqPandas):
        refresh = oryxflow.DateParameter(default=default_b)
    return UpA, UpB


class TestDefaultConflict:

    def test_different_defaults_warn_and_first_wins(self):
        UpA, UpB = _two_upstreams(D5, D9)
        with pytest.warns(ParameterDefaultConflictWarning) as rec:
            @oryxflow.requires(UpA, UpB)
            class Both(oryxflow.tasks.TaskPqPandas):
                pass
        assert len(rec) == 1
        msg = str(rec[0].message)
        assert 'UpA.refresh = 2026-11-05' in msg and 'UpB.refresh = 2026-09-01' in msg
        # what the warning describes: the first wins, and is passed to the second
        assert Both.refresh is UpA.refresh
        assert Both().requires()[1].refresh == D5

    def test_stacked_decorators_warn_once(self):
        UpA, UpB = _two_upstreams(D5, D9)
        with pytest.warns(ParameterDefaultConflictWarning) as rec:
            @oryxflow.requires(UpA)
            @oryxflow.requires(UpB)
            class Both(oryxflow.tasks.TaskPqPandas):
                pass
        assert len(rec) == 1
        assert Both.refresh is UpB.refresh   # bottom decorator applies first

    def test_no_warning_when_defaults_agree_or_declared(self):
        shared = oryxflow.DateParameter(default=D5)

        class UpA(oryxflow.tasks.TaskPqPandas):
            refresh = shared

        class UpB(oryxflow.tasks.TaskPqPandas):
            refresh = shared
        UpC, UpD = _two_upstreams(D5, D5)          # separate objects, same default
        UpE, UpF = _two_upstreams(D5, D9)
        with warnings.catch_warnings():
            warnings.simplefilter('error', ParameterDefaultConflictWarning)

            @oryxflow.requires(UpA, UpB)
            class Shared(oryxflow.tasks.TaskPqPandas):
                pass

            @oryxflow.requires(UpC, UpD)
            class Equal(oryxflow.tasks.TaskPqPandas):
                pass

            @oryxflow.requires(UpE, UpF)
            class Chosen(oryxflow.tasks.TaskPqPandas):
                refresh = oryxflow.DateParameter(default=D9)   # explicit choice


class TestExplicitExportedDefault:

    def _exported(self, tmp_path, debug):
        class Exp(oryxflow.tasks.TaskPqPandas):
            export = True
            path = str(tmp_path / 'data')
            pass
        Exp.debug = debug
        return Exp

    def test_implicit_bool_default_raises(self, tmpdir_flow):
        Exp = self._exported(tmpdir_flow, oryxflow.BoolParameter())
        assert Exp.debug.has_task_value()       # BoolParameter is False implicitly
        with pytest.raises(ValueError, match='need an explicit default'):
            oexport._check_defaults(Exp)

    def test_explicit_bool_default_passes(self, tmpdir_flow):
        oexport._check_defaults(self._exported(tmpdir_flow, oryxflow.BoolParameter(default=False)))

    def test_missing_default_message_points_to_the_fix(self, tmpdir_flow):
        Exp = self._exported(tmpdir_flow, oryxflow.DateParameter())
        with pytest.raises(ValueError, match='exported parameters need a default') as e:
            oexport._check_defaults(Exp)
        assert 'Workflow(params=...)' in str(e.value)
        assert 'debug = params_prod.debug' in str(e.value)
