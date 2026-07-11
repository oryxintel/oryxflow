import pytest
import os
import stat
import shutil
import pandas as pd
import warnings
import oryxflow


def _rmtree_robust(path):
    """Remove a directory tree, tolerating Windows read-only/locked files.

    On Windows (esp. OneDrive-synced dirs) shutil.rmtree can raise
    PermissionError [WinError 5]; clear the read-only bit and retry, and
    never let cleanup itself fail the test run.
    """
    def onerror(func, p, exc_info):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except Exception:
            pass
    shutil.rmtree(path, onerror=onerror)



class TestMain:
    # Vars
    pathdata = oryxflow.set_dir('data/')
    oryxflow.settings.log_level = 'WARNING'

    # Data
    df = pd.DataFrame({'a': range(10)})
    dfc2 = df.copy()
    dfc2['a']=dfc2['a'] * 2
    dfc4 = df.copy()
    dfc4['a']=dfc4['a'] * 2 * 2

    @pytest.fixture
    def cleanup(self, scope="module"):
        _rmtree_robust(self.pathdata)
        self.pathdata.mkdir(exist_ok=True)
        yield True
        _rmtree_robust(self.pathdata)

    def test_cleanup(self, cleanup):
        pass

    def dfhelper(self, obj, df_, file):
        fname = self.pathdata / file
        t = obj(fname)
        assert t.save(df_)==fname
        df_c = t.load()
        assert df_c.equals(df_)
        if oryxflow.settings.cached:
            assert oryxflow.data[fname].equals(df_)

    @pytest.fixture
    def load_targets(self, scope="module"):
        oryxflow.settings.cached=True
        self.dfhelper(oryxflow.targets.CSVPandasTarget, self.df, 'test.csv')
        oryxflow.settings.cached=False
        self.dfhelper(oryxflow.targets.PqPandasTarget, self.df, 'test.parquet')

    def test_targets(self, cleanup, load_targets):
        pass

    def test_cache(self, cleanup, load_targets):
        assert oryxflow.data[self.pathdata / 'test.csv'].equals(self.df)
        assert self.pathdata / 'test.parquet' not in oryxflow.data

        oryxflow.settings.cached=False
        self.dfhelper(oryxflow.targets.PqPandasTarget, self.df, 'test2.parquet')
        assert self.pathdata / 'test2.parquet' not in oryxflow.data
        oryxflow.settings.cached=True

    class Task1(oryxflow.tasks.TaskPqPandas):
        def run(self):
            self.save(TestMain.df)

    def df2fun(task):
        df2 = task.input().load()
        df2['a'] = df2['a'] * 2
        df4 = task.input().load()
        df4['a'] = df4['a'] * 2 * 2
        task.save({'df2': df2, 'df4': df4})
        task.save([df2, df4],from_list=True)

    @oryxflow.requires(Task1)
    class Task2(oryxflow.tasks.TaskPqPandas):
        persist = ['df2','df4']
        def run(self):
            TestMain.df2fun(self)

    @oryxflow.requires(Task2)
    class Task3(oryxflow.tasks.TaskPqPandas):
        do_preprocess = oryxflow.BoolParameter(default=True)
        def run(self):
            if self.do_preprocess:
                pass
            df2, df4 = self.inputLoad()
            self.save(self.input()['df2'].load())

    def test_tasks(self, cleanup):
        t1 = self.Task1()
        t2 = self.Task2()
        assert not t1.complete()
        assert not t2.complete()

        t1.run()
        assert t1.complete()
        assert t1.reset(confirm=False)
        assert not t1.complete()

        assert oryxflow.run([self.Task2()])
        assert t1.complete(); assert t2.complete()
        assert (self.pathdata / 'Task1'/'Task1__99914b932b-data.parquet').exists()
        assert (self.pathdata / 'Task2'/'Task2__99914b932b-df2.parquet').exists()

        # load outputs
        t1.output().load().equals(self.df)
        t1.outputLoad(as_dict=True).equals(self.df)
        t1.outputLoad().equals(self.df)

        t2.output()['df2'].load().equals(self.dfc2)
        t2.outputLoad(as_dict=True)['df2'].equals(self.dfc2)
        df2, df4 = t2.outputLoad()
        df2.equals(self.dfc2)
        df2 = t2.outputLoad(keys=['df2'])[0]
        df2.equals(self.dfc2)

        # test inputs
        class TaskMultiInput(oryxflow.tasks.TaskCache):
            def requires(self):
                return TestMain.Task1()
            def run(self):
                dft1 = self.inputLoad()
                assert dft1.equals(TestMain.df)
        TaskMultiInput().run()

        @oryxflow.requires(self.Task1, self.Task1)
        class TaskMultiInput(oryxflow.tasks.TaskCache):
            def run(self):
                data = self.inputLoad()
                assert data[0].equals(data[1])
        TaskMultiInput().run()

        @oryxflow.requires({1:self.Task1, 2:self.Task1})
        class TaskMultiInput(oryxflow.tasks.TaskCache):
            def run(self):
                input = self.inputLoad()
                assert input[1].equals(input[2])
        TaskMultiInput().run()

        @oryxflow.requires({'in1': self.Task2,'in2': self.Task2})
        class TaskMultiInput2(oryxflow.tasks.TaskCache):
            def run(self):
                input = self.inputLoad(task='in1',as_dict=True)
                assert input['df2'].equals(TestMain.dfc2) and input['df4'].equals(TestMain.dfc4)
        TaskMultiInput2().run()

        # check downstream incomplete
        t1.reset(confirm=False)
        assert not t2.complete()
        oryxflow.settings.check_dependencies=False
        assert t2.complete()
        oryxflow.settings.check_dependencies=True

    def test_task_overrides(self, cleanup):
        t1 = self.Task1()
        t1.target_dir = 'test'
        t1.target_ext = 'pq'

        assert not t1.complete()
        t1.run()
        assert (self.pathdata / t1.target_dir / f'Task1__99914b932b-data.{t1.target_ext}').exists()

        t1.save_attrib = False
        t1.run()
        assert (self.pathdata / t1.target_dir / f'data.{t1.target_ext}').exists()

    def test_formats(self, cleanup):
        def helper(data, TaskClass, format=None):
            class TestTask(TaskClass):
                def run(self):
                    self.save(data)

            TestTask().run()
            if format=='pd':
                assert TestTask().output().load().equals(data)
            else:
                assert TestTask().output().load()==data

        helper(self.df, oryxflow.tasks.TaskCachePandas, 'pd')
        helper({'test': 1}, oryxflow.tasks.TaskJson)
        helper({'test': 1}, oryxflow.tasks.TaskPickle)

        from oryxflow.tasks.h5 import TaskH5Pandas
        helper(self.df, TaskH5Pandas, 'pd')

        try:
            from oryxflow.tasks.dt import TaskDatatable
            import datatable as dt
            dt = dt.Frame(self.df)
            helper(dt, TaskH5Pandas)
        except:
            warnings.warn('datatable failed')

        if 0==1: # todo:
            import dask.dataframe as dd
            t1 = self.Task1()
            t1.run()
            ddf = dd.read_parquet(t1.output().path)
            from oryxflow.tasks.dask import TaskPqDask
            helper(ddf, TaskPqDask, 'pd')
            t1.reset(confirm=False)

    def test_requires(self):
        class Task1(oryxflow.tasks.TaskCache):
            def run(self):
                df = pd.DataFrame({'a': range(3)})
                self.save(df)  # quickly save dataframe
        class Task2(Task1):
            pass
        # define another task that depends on data from task1 and task2
        @oryxflow.requires({'a': Task1, 'b': Task2})
        class Task3(oryxflow.tasks.TaskCache):
            def run(self):
                df1 = self.input()['a'].load()  # quickly load input data
                df2 = self.input()['b'].load()  # quickly load input data
                
                assert(df1.equals(pd.DataFrame({'a': range(3)})))
        task3 = Task3()
        oryxflow.run(task3)

    def test_flow(self):
        class Task1(oryxflow.tasks.TaskCache):
            persist = ['a1','a2']
            def run(self):
                df = pd.DataFrame({'a':range(3)})
                self.save({'a1':df,'a2':df}) 
        class Task2(oryxflow.tasks.TaskCache):
            def run(self):
                df = pd.DataFrame({'a':range(3)})
                self.save(df) 
                
        @oryxflow.requires(Task1,Task2)
        class Task3(oryxflow.tasks.TaskCache):
            multiplier = oryxflow.IntParameter()
            def run(self):
                df1 = self.input()[0]['a1'].load()
                df2 = self.input()[1].load()
                assert df2.equals(df1)
                df = df1.join(df2, lsuffix='1', rsuffix='2')
                df['b']=df['a1']*self.multiplier # use task parameter
                self.save(df)

        params = dict(multiplier=2)
        dag = oryxflow.Workflow(Task3, params=params)

        dag.run(forced_all_upstream=True,confirm=False)
        assert dag.outputLoad(Task1,as_dict=True)['a1'].equals(dag.outputLoad(Task2))
        dfc = dag.outputLoad(Task3)
        assert (dfc['b']==dfc['a1']*params['multiplier']).all()

    def test_multiple_deps_on_input_load(self):
        # define 2 tasks that load raw data
        class Task1(oryxflow.tasks.TaskCache):
            persist = ['a1','a2']
            def run(self):
                df = pd.DataFrame({'a':range(3)})
                self.save({'a1':df,'a2':df}) # quickly save dataframe

        class Task2(oryxflow.tasks.TaskCache):
            def run(self):
                df = pd.DataFrame({'a':range(3)})
                self.save(df) # quickly save dataframe

        # define another task that depends on data from task1 and task2
        @oryxflow.requires(Task1,Task2)
        class Task3(oryxflow.tasks.TaskCache):
            def run(self):
                data = self.inputLoad(as_dict=True)
                df1 = data[0]['a1']
                assert df1.equals(data[0]['a2'])
                df2 = data[1]
                assert df2.equals(df1)
                df = df1.join(df2, lsuffix='1', rsuffix='2')
                self.save(df)

        # Execute task including all its dependencies
        oryxflow.run(Task3(),forced_all_upstream=True,confirm=False)

    def test_functional_Flow(self):
        import oryxflow
        import pandas as pd

        from oryxflow.functional import Workflow
        flow = Workflow()


        @flow.task(oryxflow.tasks.TaskCache)
        @flow.persists(['a1', 'a2'])
        def get_data0(task):
            df = pd.DataFrame({'a':range(3)})
            task.save({'a1':df,'a2':df})

        @flow.task(oryxflow.tasks.TaskCache)
        @flow.persists(['a1', 'a2'])
        def get_data1(task):
            df = pd.DataFrame({'a':range(3)})
            task.save({'a1':df,'a2':df})

        @flow.task(oryxflow.tasks.TaskCache)
        @flow.requires(get_data0)
        def get_data2(task):
            df0 = task.inputLoad(as_dict=True)
            df = pd.DataFrame({'a':range(3)})
            task.save({'b1':df,'b2':df0})

        @flow.task(oryxflow.tasks.TaskCache)
        @flow.requires({"a":get_data1, "b":get_data2})
        @flow.persists(['aa'])
        def use_data(task):
            df0 = task.inputLoad(as_dict=True)
            df = pd.DataFrame({'a':range(3)})
            assert df0["a"]["a1"].equals(df) and df0["a"]["a2"].equals(df)
            assert df0["b"]["b1"].equals(df) and df0["b"]["b2"]["a1"].equals(df)
            assert task.multiplier == 42
            output = pd.DataFrame({'a':range(4)})
            task.save({'aa':output})

        flow.add_global_params(multiplier=oryxflow.IntParameter(default=0))
        flow.run([use_data, get_data0], forced_all_upstream=True, confirm=False, params={'multiplier':42})
        flow.run(use_data, forced_all_upstream=True, confirm=False, params={'multiplier':42})
        dfo = pd.DataFrame({'a':range(4)})
        assert flow.outputLoad(use_data)[0].equals(dfo)
        
    def test_params(self, cleanup):
        class TaskParam(oryxflow.tasks.TaskCache):
            nrows = oryxflow.IntParameter(default=10)
            def run(self):
                self.save(pd.DataFrame({'a':range(self.nrows)}))

        t1 = TaskParam(); t2 = TaskParam(nrows=20)
        assert not t1.complete(); assert not t2.complete()

        t1.run()
        assert t1.complete(); assert not t2.complete()

    def test_external(self, cleanup):
        class Task2(oryxflow.tasks.TaskPqPandas):
            external = True

    def test_execute(self, cleanup):
        # execute
        t1 = self.Task1()
        t2 = self.Task2()
        t3 = self.Task3()
        [t.reset(confirm=False) for t in [t1,t2,t3]]
        oryxflow.run(t3)
        assert all(t.complete() for t in [t1,t2,t3])
        t1.reset(confirm=False); t2.reset(confirm=False)
        assert not t3.complete() # cascade upstream
        oryxflow.settings.check_dependencies=False
        assert t3.complete() # no cascade upstream
        oryxflow.run([t3])
        assert t3.complete() and not t1.complete()
        oryxflow.settings.check_dependencies=True
        oryxflow.run([t3])
        assert all(t.complete() for t in [t1,t2,t3])

        # forced single
        class TaskTest(oryxflow.tasks.TaskCachePandas):
            def run(self):
                self.save(TestMain.df)

        oryxflow.run(TaskTest())
        assert TaskTest().output().load().equals(TestMain.df)
        class TaskTest(oryxflow.tasks.TaskCachePandas):
            def run(self):
                self.save(TestMain.df * 2)

        oryxflow.run(TaskTest())
        assert TaskTest().output().load().equals(TestMain.df)
        oryxflow.run(TaskTest(),forced=TaskTest(),confirm=False)
        assert TaskTest().output().load().equals(TestMain.df * 2)
        oryxflow.run([TaskTest()],forced=[TaskTest()],confirm=False)

        # forced flow
        mtimes = [t1.output().path.stat().st_mtime,t2.output()['df2'].path.stat().st_mtime]
        oryxflow.run(t3,forced=t1,confirm=False)
        assert t1.output().path.stat().st_mtime>mtimes[0]
        assert t2.output()['df2'].path.stat().st_mtime>mtimes[1]

        # forced_all => run task3 only
        mtimes = [t1.output().path.stat().st_mtime,t2.output()['df2'].path.stat().st_mtime,t3.output().path.stat().st_mtime]
        oryxflow.run(t3,forced_all=True,confirm=False)
        assert t1.output().path.stat().st_mtime==mtimes[0]
        assert t2.output()['df2'].path.stat().st_mtime==mtimes[1]
        assert t3.output().path.stat().st_mtime>mtimes[2]

        # forced_all_upstream => run all tasks
        mtimes = [t1.output().path.stat().st_mtime,t2.output()['df2'].path.stat().st_mtime,t3.output().path.stat().st_mtime]
        oryxflow.run(t3,forced_all_upstream=True,confirm=False)
        assert t1.output().path.stat().st_mtime>mtimes[0]
        assert t2.output()['df2'].path.stat().st_mtime>mtimes[1]
        assert t3.output().path.stat().st_mtime>mtimes[2]

        # downstream
        assert oryxflow.run(t3)
        oryxflow.invalidate_downstream(t2, t3, confirm=False)
        assert not (t2.complete() and t3.complete()) and t1.complete()

        # upstream
        assert oryxflow.run(t3)
        oryxflow.invalidate_upstream(t3, confirm=False)
        assert not all(t.complete() for t in [t1,t2,t3])

    def test_preview(self):
        t1 = self.Task1()
        t2 = self.Task2()
        t3 = self.Task3()
        oryxflow.invalidate_upstream(t3, confirm=False)

        import io
        from contextlib import redirect_stdout

        with io.StringIO() as buf, redirect_stdout(buf):
            oryxflow.preview(t3)
            output = buf.getvalue()
            assert output.count('PENDING')==3
            assert output.count('COMPLETE')==0

        with io.StringIO() as buf, redirect_stdout(buf):
            oryxflow.run(t3)
            oryxflow.preview(t3)
            output = buf.getvalue()
            assert output.count('PENDING')==0
            assert output.count('COMPLETE')==3

        with io.StringIO() as buf, redirect_stdout(buf):
            oryxflow.preview(self.Task3(do_preprocess=False))
            output = buf.getvalue()
            assert output.count('PENDING')==1
            assert output.count('COMPLETE')==2

    def test_dynamic(self):

        class TaskCollector(oryxflow.tasks.TaskAggregator):
            def run(self):
                yield TestMain.Task1()
                yield TestMain.Task2()

        oryxflow.run(TaskCollector())
        assert self.Task1().complete() and self.Task2().complete() and TaskCollector().complete()
        assert TaskCollector().outputLoad()[0].equals(self.Task1().outputLoad())
        assert TaskCollector().outputLoad()[1][0].equals(self.Task2().outputLoad()[0])
        TaskCollector().reset(confirm=False)
        assert not (self.Task1().complete() and self.Task2().complete() and TaskCollector().complete())

    def tests_params(self):
        class Task1(oryxflow.tasks.TaskCache):
            param = oryxflow.IntParameter(significant=False)

            def run(self):
                self.save({1: 1})

        Task1(param=1, param2=1) # pass insignifcant and non-existing param

    def test_excel_default_persist(self, cleanup):
        df1 = pd.DataFrame({'a': range(5)})

        class TaskExcelDefault(oryxflow.tasks.TaskExcelPandas):
            def run(self):
                self.save(df1)

        t = TaskExcelDefault()
        assert not t.complete()
        t.run()
        assert t.complete()

        # outputLoad returns single df
        data = t.outputLoad()
        assert data.equals(df1)

        # invalidate
        t.invalidate(confirm=False)
        assert not t.complete()

    def test_excel_sheets(self, cleanup):
        df1 = pd.DataFrame({'a': range(5)})
        df2 = pd.DataFrame({'b': range(5)})

        class TaskSheets(oryxflow.tasks.TaskExcelPandas):
            persist = ['customers', 'orders']
            def run(self):
                self.save({'customers': df1, 'orders': df2})

        t = TaskSheets()
        assert not t.complete()

        # run and check complete
        t.run()
        assert t.complete()

        # single file created (not one per persist key)
        output_path = t.output().path
        assert output_path.exists()

        # outputLoad - list (default)
        data = t.outputLoad()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0].equals(df1)
        assert data[1].equals(df2)

        # outputLoad - as_dict
        data = t.outputLoad(as_dict=True)
        assert isinstance(data, dict)
        assert data['customers'].equals(df1)
        assert data['orders'].equals(df2)

        # outputLoad - single key (str)
        data = t.outputLoad(keys='customers')
        assert data.equals(df1)

        # outputLoad - key list
        data = t.outputLoad(keys=['orders'])
        assert isinstance(data, list)
        assert data[0].equals(df2)

        # outputLoad - invalid key
        with pytest.raises(IndexError):
            t.outputLoad(keys='bad_key')

        # save with from_list
        class TaskSheetsList(oryxflow.tasks.TaskExcelPandas):
            persist = ['customers', 'orders']
            def run(self):
                self.save([df1, df2], from_list=True)

        TaskSheetsList().run()
        data = TaskSheetsList().outputLoad(as_dict=True)
        assert data['customers'].equals(df1)
        assert data['orders'].equals(df2)

        # save validation - mismatched keys
        class TaskSheetsBad(oryxflow.tasks.TaskExcelPandas):
            persist = ['customers', 'orders']
            def run(self):
                self.save({'wrong_key': df1, 'orders': df2})

        with pytest.raises(ValueError):
            TaskSheetsBad().run()

        # invalidate
        t.invalidate(confirm=False)
        assert not t.complete()
        assert not output_path.exists()

        # inputLoad from downstream
        t.run()

        @oryxflow.requires(TaskSheets)
        class TaskDownstream(oryxflow.tasks.TaskCache):
            def run(self):
                data = self.inputLoad()
                assert isinstance(data, dict)
                assert data['customers'].equals(df1)
                assert data['orders'].equals(df2)

        TaskDownstream().run()

    def test_path(self):
        class Task1(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({1: 1})

        class Task2(oryxflow.tasks.TaskPickle):
            def run(self):
                self.save({1: 1})

        path = 'data/data2/'
        assert 'data2' in str(Task1(path=path).output().path)
        flow = oryxflow.Workflow(Task1, path=path)
        assert 'data2' in str(flow.get_task().output().path)
        flow2 = oryxflow.WorkflowMulti(Task2, params={0:{}}, path=path)
        assert 'data2' in str(flow2.get_task()[0].output().path)

    def test_concat_iter_default(self):
        import oryxflow.utils
        df = pd.DataFrame({'v': range(3)})
        # default tagging: params become columns
        items = [('a', {'state': 'CA'}, df), ('b', {'state': 'NY'}, df)]
        out = oryxflow.utils.concat_iter(items)
        assert list(out['state']) == ['CA', 'CA', 'CA', 'NY', 'NY', 'NY']
        assert len(out) == 6
        # data as dict of frames (multi-persist)
        items = [('a', {'state': 'CA'}, {'x': df, 'y': df})]
        out = oryxflow.utils.concat_iter(items)
        assert len(out) == 6 and set(out['state']) == {'CA'}
        # concat_fn overrides default tagging
        def fn(ident, params, d):
            d = d.copy(); d['ident'] = ident; return d
        out = oryxflow.utils.concat_iter([('a', {'state': 'CA'}, df)], concat_fn=fn)
        assert 'ident' in out.columns and 'state' not in out.columns
        # keys subset
        out = oryxflow.utils.concat_iter([('a', {'state': 'CA', 'country': 'US'}, df)], keys=['state'])
        assert 'state' in out.columns and 'country' not in out.columns

    def test_inputLoadConcat_dict(self, cleanup):
        class CState(oryxflow.tasks.TaskCachePandas):
            state = oryxflow.Parameter()
            def run(self):
                self.save(pd.DataFrame({'v': range(3)}))

        class CCountry(oryxflow.tasks.TaskCachePandas):
            def requires(self):
                return {s: CState(state=s) for s in ['CA', 'NY']}
            def run(self):
                self.save(self.inputLoadConcat())

        oryxflow.run(CCountry())
        df = CCountry().outputLoad()
        assert set(df['state']) == {'CA', 'NY'}
        assert len(df) == 6

    def test_inputLoadConcat_list(self, cleanup):
        class LState(oryxflow.tasks.TaskCachePandas):
            state = oryxflow.Parameter()
            def run(self):
                self.save(pd.DataFrame({'v': range(2)}))

        class LCountry(oryxflow.tasks.TaskCachePandas):
            def requires(self):
                return [LState(state='CA'), LState(state='NY')]
            def run(self):
                self.save(self.inputLoadConcat())

        oryxflow.run(LCountry())
        df = LCountry().outputLoad()
        assert set(df['state']) == {'CA', 'NY'}
        assert len(df) == 4

    def test_aggregator_reset_cascades(self, cleanup):
        class RDataLoad(oryxflow.tasks.TaskCachePandas):
            country = oryxflow.Parameter()
            state = oryxflow.Parameter()
            def run(self):
                self.save(pd.DataFrame({'v': range(3)}))

        @oryxflow.requires(RDataLoad)
        class RProcess(oryxflow.tasks.TaskCachePandas):
            def run(self):
                self.save(self.inputLoad().assign(processed=1))

        class RCountry(oryxflow.tasks.TaskCachePandas):
            country = oryxflow.Parameter()
            def requires(self):
                return {s: RProcess(country=self.country, state=s) for s in ['CT', 'NY']}
            def run(self):
                self.save(self.inputLoadConcat())

        oryxflow.run(RCountry(country='US'))
        assert all(RDataLoad(country='US', state=s).complete() for s in ['CT', 'NY'])

        oryxflow.invalidate_upstream(RCountry(country='US'), confirm=False)
        assert not any(RDataLoad(country='US', state=s).complete(cascade=False) for s in ['CT', 'NY'])

    def test_reset_upstream_only(self, cleanup):
        class ODataLoad(oryxflow.tasks.TaskCachePandas):
            country = oryxflow.Parameter()
            state = oryxflow.Parameter()
            def run(self):
                self.save(pd.DataFrame({'v': range(3)}))

        @oryxflow.requires(ODataLoad)
        class OProcess(oryxflow.tasks.TaskCachePandas):
            def run(self):
                self.save(self.inputLoad().assign(processed=1))

        class OCountry(oryxflow.tasks.TaskCachePandas):
            country = oryxflow.Parameter()
            def requires(self):
                return {s: OProcess(country=self.country, state=s) for s in ['CT', 'NY']}
            def run(self):
                self.save(self.inputLoadConcat())

        flow = oryxflow.Workflow(OCountry, {'country': 'US'})
        flow.run()

        flow.reset_upstream(OCountry, only=ODataLoad)
        # only the DataLoad family is invalidated
        assert not ODataLoad(country='US', state='CT').complete(cascade=False)
        # sibling family output still present on disk/cache
        assert OProcess(country='US', state='CT').complete(cascade=False)
        # recursive complete() sees upstream gone -> downstream recomputes
        assert not OCountry(country='US').complete()
        flow.run()
        assert OCountry(country='US').complete()

    def test_workflowMulti_reset_single_flow(self, cleanup):
        class MDataLoad(oryxflow.tasks.TaskCachePandas):
            country = oryxflow.Parameter()
            state = oryxflow.Parameter()
            def run(self):
                self.save(pd.DataFrame({'v': range(3)}))

        @oryxflow.requires(MDataLoad)
        class MProcess(oryxflow.tasks.TaskCachePandas):
            def run(self):
                self.save(self.inputLoad().assign(processed=1))

        class MCountry(oryxflow.tasks.TaskCachePandas):
            country = oryxflow.Parameter()
            def requires(self):
                return {s: MProcess(country=self.country, state=s) for s in ['CT', 'NY']}
            def run(self):
                self.save(self.inputLoadConcat())

        flow = oryxflow.WorkflowMulti(MCountry, params={'US': {'country': 'US'},
                                                       'UK': {'country': 'UK'}})
        flow.run()
        # reset upstream of only the US flow
        flow.reset_upstream(MCountry, flow='US')
        assert not MDataLoad(country='US', state='CT').complete(cascade=False)
        # the other flow's upstream is untouched (regression guard: old code called plain reset)
        assert MDataLoad(country='UK', state='CT').complete(cascade=False)

    def test_reset_downstream_family(self, cleanup):
        # reset_downstream(family) invalidates that family + everything downstream of it,
        # by family (so it reaches DAG-internal-param tasks), explicitly (no cascade reliance),
        # leaving the upstream leaf intact.
        ran = []

        class DLeaf(oryxflow.tasks.TaskCachePandas):
            grp = oryxflow.Parameter(); sub = oryxflow.Parameter()
            def run(self):
                ran.append(('DLeaf', self.grp, self.sub))
                self.save(pd.DataFrame({'v': range(2)}))

        class DMid(oryxflow.tasks.TaskCachePandas):
            grp = oryxflow.Parameter()
            def requires(self):
                return {s: DLeaf(grp=self.grp, sub=s) for s in ['a', 'b']}
            def run(self):
                ran.append(('DMid', self.grp))
                self.save(self.inputLoadConcat())

        class DTop(oryxflow.tasks.TaskCachePandas):
            def requires(self):
                return {g: DMid(grp=g) for g in ['x', 'y']}
            def run(self):
                ran.append(('DTop',))
                self.save(self.inputLoadConcat())

        flow = oryxflow.Workflow(DTop)
        flow.run()

        # name only the changed family; cascade OFF so downstream must be reset explicitly.
        # endpoint (task_downstream) defaults to the flow's default task (DTop).
        oryxflow.settings.check_dependencies = False
        try:
            ran.clear()
            flow.reset_downstream(DMid)
            flow.run()
        finally:
            oryxflow.settings.check_dependencies = True

        assert not any(r[0] == 'DLeaf' for r in ran)                 # expensive leaf preserved
        assert ('DTop',) in ran                                      # downstream recomputed, no cascade
        assert sorted(r for r in ran if r[0] == 'DMid') == [('DMid', 'x'), ('DMid', 'y')]

    def test_reset_downstream_family_list(self, cleanup):
        # reset_downstream accepts a list of families: resets each family + its downstream.
        class FLeaf(oryxflow.tasks.TaskCachePandas):
            grp = oryxflow.Parameter()
            def run(self): self.save(pd.DataFrame({'v': range(2)}))

        class FMidA(oryxflow.tasks.TaskCachePandas):
            def requires(self): return {g: FLeaf(grp=g) for g in ['x', 'y']}
            def run(self): self.save(self.inputLoadConcat())

        class FMidB(oryxflow.tasks.TaskCachePandas):
            def requires(self): return {g: FLeaf(grp=g) for g in ['x', 'y']}
            def run(self): self.save(self.inputLoadConcat() * 2)

        class FTop(oryxflow.tasks.TaskCachePandas):
            def requires(self): return {'a': FMidA(), 'b': FMidB()}
            def run(self): self.save(self.inputLoadConcat())

        flow = oryxflow.Workflow(FTop)
        flow.run()
        # reset two non-contiguous mid families + downstream in one call
        flow.reset_downstream([FMidA, FMidB])
        assert not FMidA().complete(cascade=False)
        assert not FMidB().complete(cascade=False)
        assert not FTop().complete(cascade=False)         # downstream band reset explicitly
        assert FLeaf(grp='x').complete(cascade=False)      # shared leaf preserved

    def test_reset_upstream_full_and_only(self, cleanup):
        # base reset_upstream resets the WHOLE upstream cone (leaf included); only= narrows it,
        # and only= accepts a list of families.
        class ULeaf(oryxflow.tasks.TaskCachePandas):
            grp = oryxflow.Parameter(); sub = oryxflow.Parameter()
            def run(self): self.save(pd.DataFrame({'v': range(2)}))

        class UMid(oryxflow.tasks.TaskCachePandas):
            grp = oryxflow.Parameter()
            def requires(self): return {s: ULeaf(grp=self.grp, sub=s) for s in ['a', 'b']}
            def run(self): self.save(self.inputLoadConcat())

        class UTop(oryxflow.tasks.TaskCachePandas):
            def requires(self): return {g: UMid(grp=g) for g in ['x', 'y']}
            def run(self): self.save(self.inputLoadConcat())

        flow = oryxflow.Workflow(UTop)
        flow.run()

        # no filter -> whole cone incl leaf and top
        flow.reset_upstream(UTop)
        assert not ULeaf(grp='x', sub='a').complete(cascade=False)
        assert not UMid(grp='x').complete(cascade=False)
        assert not UTop().complete(cascade=False)

        flow.run()  # rebuild
        # only=UMid -> only that family; leaf preserved, top's own file untouched (recomputes via cascade)
        flow.reset_upstream(UTop, only=UMid)
        assert ULeaf(grp='x', sub='a').complete(cascade=False)
        assert not UMid(grp='x').complete(cascade=False)
        assert UTop().complete(cascade=False)
        assert not UTop().complete()               # recursive complete() sees UMid gone

        flow.run()  # rebuild
        # only=[list] -> multiple families
        flow.reset_upstream(UTop, only=[ULeaf, UMid])
        assert not ULeaf(grp='x', sub='a').complete(cascade=False)
        assert not UMid(grp='x').complete(cascade=False)
        assert UTop().complete(cascade=False)      # not in the list

