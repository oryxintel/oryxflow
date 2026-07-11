import sklearn, sklearn.datasets, sklearn.svm, sklearn.linear_model
from pathlib import Path
import pandas as pd
import numpy as np
import pytest
import unittest
import oryxflow

# Test Requires
class Task1(oryxflow.tasks.TaskCache):  
    def run(self):
        df = pd.DataFrame({'a':range(3)})
        self.save(df) # quickly save dataframe

class Task2(Task1):
    pass

# Define another task that depends on data from task1 and task2
@oryxflow.requires({'a':Task1,'b':Task2})
class Task3(oryxflow.tasks.TaskCache):
    
    def run(self):
        df1 = self.input()['a'].load() # quickly load input data
        df2 = self.input()['b'].load() # quickly load input data

        self.test.assertTrue(df1.equals(pd.DataFrame({'a':range(3)})))

class TestRequires(unittest.TestCase):
    def test_requires(self):
        task3 = Task3()
        task3.test = self
        
        oryxflow.run(task3)


# Test Workflow
# Save dataframe as parquet
class TaskGetData(oryxflow.tasks.TaskPqPandas):

    def run(self):
        ds = sklearn.datasets.load_breast_cancer()
        df_train = pd.DataFrame(ds.data, columns=ds.feature_names)
        df_train['y'] = ds.target
        self.save(df_train) # quickly save dataframe

@oryxflow.requires(TaskGetData) # define dependency
class TaskPreprocess(oryxflow.tasks.TaskPqPandas):
    do_preprocess = oryxflow.BoolParameter(default=True) # parameter for preprocessing yes/no

    def run(self):
        df_train = self.input().load() # quickly load required data
        if self.do_preprocess:
            df_train.iloc[:,:-1] = sklearn.preprocessing.scale(df_train.iloc[:,:-1])
        self.save(df_train)

@oryxflow.requires(TaskPreprocess) # automatically pass parameters upstream
class TaskTrain(oryxflow.tasks.TaskPickle): # save output as pickle
    model = oryxflow.Parameter(default='ols') # parameter for model selection
    scale = oryxflow.BoolParameter(default=False)

    def run(self):
        df_train = self.input().load()
        if self.model=='ols':
            model = sklearn.linear_model.LogisticRegression()
        elif self.model=='svm':
            model = sklearn.svm.SVC()
        else:
            raise ValueError('invalid model selection')
        model.fit(df_train.drop('y',axis=1), df_train['y'])
        self.save(model)

class Task1(oryxflow.tasks.TaskCache):
    param1 = oryxflow.IntParameter(default=0)

    def run(self):
        self.save({'hello':self.param1})

class TestWorkflow:

    def test_single_flow_preview(self):
        flow = oryxflow.Workflow(params = {'do_preprocess': False})
        # Reset
        flow.reset(TaskGetData)
        flow.reset(TaskPreprocess)
        flow.reset(TaskTrain)

        import io
        from contextlib import redirect_stdout

        with io.StringIO() as buf, redirect_stdout(buf):
            flow.preview(TaskTrain)
            output = buf.getvalue()
            assert output.count('PENDING') == 3
            assert output.count('COMPLETE') == 0
            assert output.count("'do_preprocess': 'False'") == 1


    def test_single_workflow_no_params_passes(self):
        flow = oryxflow.Workflow()
        import io
        from contextlib import redirect_stdout

        with io.StringIO() as buf, redirect_stdout(buf):
            flow.preview(TaskTrain)
            output = buf.getvalue()
            assert output.count("'do_preprocess': 'True'")==1


    def test_single_flow_parameters_passes_properly(self):
        flow1 = oryxflow.Workflow(params = {'do_preprocess': False})
        flow2 = oryxflow.Workflow(params = {'do_preprocess': True})

        import io
        from contextlib import redirect_stdout

        with io.StringIO() as buf, redirect_stdout(buf):
            flow1.preview(TaskTrain)
            output = buf.getvalue()
            assert output.count("'do_preprocess': 'False'")==1

            flow2.preview(TaskTrain)
            output2 = buf.getvalue()
            assert output2.count("'do_preprocess': 'True'")==1


    def test_task_runs_properly_and_gives_outputload(self):
        flow = oryxflow.Workflow(params = {'do_preprocess': False})
        flow.preview(TaskTrain)
        flow.run(TaskTrain)
        out1 = flow.outputLoad(TaskTrain)
        out2 = flow.outputLoad(TaskPreprocess)
        ds = sklearn.datasets.load_breast_cancer()
        df_train = pd.DataFrame(ds.data, columns=ds.feature_names)
        assert type(out1).__name__ == "LogisticRegression"
        assert np.all(out2.drop(['y'], axis=1) == df_train)


    def test_output_load_comparing_with_task_run(self):
        params = {'param1': 1}
        flow = oryxflow.Workflow(params = params, task=Task1)

        flow.run()
        assert Task1(**params).complete() == True
        assert Task1(**params).outputLoad()['hello'] == params['param1']
        assert flow.outputLoad() == Task1(**params).outputLoad()


    def test_output_load_all_comparing_with_task_run(self):
        params = {'param1': 1}
        flow = oryxflow.Workflow(params = params, task=Task1)

        flow.run()
        outputs = flow.outputLoadAll()
        assert outputs['Task1'] == Task1(**params).outputLoad()


    def test_no_default_expect_error(self):
        with pytest.raises(RuntimeError):
            flow = oryxflow.Workflow(params = {'do_preprocess': False})
            flow.run()


    def test_default_task(self):
        flow = oryxflow.Workflow({'do_preprocess': False})
        flow.set_default(TaskTrain)
        out_class = flow.get_task()
        type(out_class).__name__ == "TaskTrain"


    def test_tasks_with_dependencies_outputloadall(self):
        flow = oryxflow.Workflow(params = {'do_preprocess': False}, task=TaskTrain)
        flow.preview()
        flow.run()
        flow.run(TaskPreprocess)
        flow.outputLoad()
        flow.outputLoad(TaskPreprocess)

        # load data for all dependencies
        data = flow.outputLoadAll()
        assert 'TaskTrain' in data.keys()
        assert 'TaskPreprocess' in data.keys()
        assert 'TaskGetData' in data.keys()
        assert type(data['TaskTrain']).__name__ == "LogisticRegression"
        ds = sklearn.datasets.load_breast_cancer()
        df_train = pd.DataFrame(ds.data, columns=ds.feature_names)
        assert np.all(data['TaskPreprocess'].drop(['y'], axis=1) == df_train)
        assert np.all(data['TaskGetData'].drop(['y'], axis=1) == df_train)


    def test_get_task(self):
        flow0 = oryxflow.Workflow(task=Task1)
        assert flow0.get_task().param_kwargs['param1'] == 0
        params = {'param1': 1}
        flow = oryxflow.Workflow(params = params, task=Task1)
        assert flow.get_task().param_kwargs['param1'] == params['param1']

# Test Export
import pathlib
class TestFlowExports:
    # Vars
    df = pd.DataFrame({'a': range(10)})
    cfg_write_dir = pathlib.Path('tests')
    cfg_write_filename_tasks = Path('tasks_export.py')
    file_path = cfg_write_dir / cfg_write_filename_tasks

    class Task1A0(oryxflow.tasks.TaskCache):
        pass

    class Task1A(Task1A0):
        persist=['df','df2']
        idx=oryxflow.IntParameter(default=1)
        idx2=oryxflow.Parameter(default='test')
        def run(self):
            self.save({'df': self.df, 'df2': self.df})

    class Task1B(oryxflow.tasks.TaskCache):
        persist=['df','df2']
        idx3=oryxflow.Parameter(default='test3')
        def run(self):
            self.save({'df': self.df,'df2': self.df})

    class Task1C(oryxflow.tasks.TaskCache):
        persist=['df','df2']
        idx3=oryxflow.Parameter(default='test3')
        export = False
        def run(self):
            self.save({'df': self.df,'df2': self.df})

    @oryxflow.requires(Task1A, Task1B, Task1C)
    class Task1All(oryxflow.tasks.TaskCache):

        def run(self):
            self.save(self.df)

    @oryxflow.requires(Task1A, Task1B, Task1C)
    class Task2(oryxflow.tasks.TaskCache):
        def run(self):
            self.save(self.df)

    @oryxflow.requires(Task1A, Task1B, Task1C)
    class Task2Path(oryxflow.tasks.TaskCache):
        path = "usr/test/"
        def run(self):
            self.save(self.df)

    @oryxflow.requires(Task1A, Task1B, Task1C)
    class Task2Task_group(oryxflow.tasks.TaskCache):
        task_group = "upwork"
        def run(self):
            self.save(self.df)

    @oryxflow.requires(Task1A, Task1B, Task1C)
    class Task3Flow(oryxflow.tasks.TaskCache):
        task_group = "upwork"
        def run(self):
            self.save(self.df)

    # Helpers
    def setup_module(self):
        oryxflow.run(self.Task1All())
        oryxflow.invalidate_upstream(self.Task1All(), confirm=False)
        oryxflow.preview(self.Task1All())

    def readfile(self, file_dir):
        with open(file_dir, 'r') as f:
            file = f.read()
            file = file.replace("\n    \n","\n\n") # in order to match string format
        return file

    @pytest.fixture
    def cleanup(self):
        yield True
        (self.cfg_write_dir/self.cfg_write_filename_tasks).unlink()

    def teardown_module(self):
        # Reset
        self.Task1A0().reset()
        self.Task1A().reset()
        self.Task1B().reset()
        self.Task1C().reset()
        self.Task1All().reset()
        self.Task2().reset()
        self.Task2Path().reset()
        self.Task2Task_group().reset()
        self.Task3Flow().reset()

    # Tests
    def test_task(self, cleanup):

        e = oryxflow.FlowExport(tasks=self.Task1All(), save=True, path_export=self.file_path)
        e.generate()

        code = self.readfile(self.file_path)
        assert code == '''
import oryxflow
import datetime

class Task1All(oryxflow.tasks.TaskCache):
    external=True
    persists=['data']
    idx=oryxflow.parameter.IntParameter(default=1)
    idx2=oryxflow.parameter.Parameter(default='test')
    idx3=oryxflow.parameter.Parameter(default='test3')

'''

    def test_task2(self, cleanup):

        e = oryxflow.FlowExport(tasks=[self.Task1A(), self.Task1All()], 
                                        save=True, path_export=self.file_path)
        e.generate()

        code = self.readfile(self.file_path)
        assert code == '''
import oryxflow
import datetime

class Task1All(oryxflow.tasks.TaskCache):
    external=True
    persists=['data']
    idx=oryxflow.parameter.IntParameter(default=1)
    idx2=oryxflow.parameter.Parameter(default='test')
    idx3=oryxflow.parameter.Parameter(default='test3')

class Task1A(oryxflow.tasks.TaskCache):
    external=True
    persists=['df', 'df2']
    idx=oryxflow.parameter.IntParameter(default=1)
    idx2=oryxflow.parameter.Parameter(default='test')

'''

    def test_flow(self, cleanup):
        flow = oryxflow.Workflow(self.Task1All)

        e = oryxflow.FlowExport(flows=flow,
                                        save=True, path_export=self.file_path)
        e.generate()

        code = self.readfile(self.file_path)
        assert code == '''
import oryxflow
import datetime

class Task1A(oryxflow.tasks.TaskCache):
    external=True
    persists=['df', 'df2']
    idx=oryxflow.parameter.IntParameter(default=1)
    idx2=oryxflow.parameter.Parameter(default='test')

class Task1B(oryxflow.tasks.TaskCache):
    external=True
    persists=['df', 'df2']
    idx3=oryxflow.parameter.Parameter(default='test3')

class Task1All(oryxflow.tasks.TaskCache):
    external=True
    persists=['data']
    idx=oryxflow.parameter.IntParameter(default=1)
    idx2=oryxflow.parameter.Parameter(default='test')
    idx3=oryxflow.parameter.Parameter(default='test3')

'''

    # Test Path
    def test_task_path(self, cleanup):

        e = oryxflow.FlowExport(tasks=self.Task2Path(),
                                        save=True, path_export=self.file_path)
        e.generate()

        code = self.readfile(self.file_path)
        assert code == f'''
import oryxflow
import datetime

class Task2Path(oryxflow.tasks.TaskCache):
    external=True
    persists=['data']
    path="{Path("usr/test")}"
    idx=oryxflow.parameter.IntParameter(default=1)
    idx2=oryxflow.parameter.Parameter(default='test')
    idx3=oryxflow.parameter.Parameter(default='test3')

'''

    # Test Keep task_group
    def test_task_task_group(self, cleanup):

        e = oryxflow.FlowExport(tasks=self.Task2Task_group(),
                                        save=True, path_export=self.file_path)
        e.generate()

        code = self.readfile(self.file_path)
        assert code == '''
import oryxflow
import datetime

class Task2Task_group(oryxflow.tasks.TaskCache):
    external=True
    persists=['data']
    task_group="upwork"
    idx=oryxflow.parameter.IntParameter(default=1)
    idx2=oryxflow.parameter.Parameter(default='test')
    idx3=oryxflow.parameter.Parameter(default='test3')

'''

    # Test Flow Path Setting
    def test_task_flow_path(self, cleanup):
        flow = oryxflow.Workflow(self.Task3Flow, path='data/data2/wf_change', env='prod')
        e = oryxflow.FlowExport(flows=flow,
                                        save=True, path_export=self.file_path)
        e.generate()

        code = self.readfile(self.file_path)
        wf_path = Path("data/data2/wf_change/env=prod")
        assert code == f'''
import oryxflow
import datetime

class Task1A(oryxflow.tasks.TaskCache):
    external=True
    persists=['df', 'df2']
    path="{wf_path}"
    idx=oryxflow.parameter.IntParameter(default=1)
    idx2=oryxflow.parameter.Parameter(default='test')

class Task1B(oryxflow.tasks.TaskCache):
    external=True
    persists=['df', 'df2']
    path="{wf_path}"
    idx3=oryxflow.parameter.Parameter(default='test3')

class Task3Flow(oryxflow.tasks.TaskCache):
    external=True
    persists=['data']
    path="{wf_path}"
    task_group="upwork"
    idx=oryxflow.parameter.IntParameter(default=1)
    idx2=oryxflow.parameter.Parameter(default='test')
    idx3=oryxflow.parameter.Parameter(default='test3')

'''

# FlowImport Tests
class TestFlowImport:

    def test_import_file_not_found(self):
        with pytest.raises(Exception) as e_info:
            scraper = oryxflow.FlowImport(path='tests/', module='for_imports', path_data='data/')

    def test_import_dirpath(self):
        scraper = oryxflow.FlowImport(path='tests/', module='for_import', path_data='data/')
        assert scraper.dirpath == Path("tests/data")

    def test_import_tasks(self):
        scraper = oryxflow.FlowImport(path='tests/', module='for_import', path_data='data/')
        assert scraper.tasks.Task1All.task_group == "upwork"
        
    def test_import_to_flow(self):
        scraper = oryxflow.FlowImport(path='tests/', module='for_import', path_data='data/')
        flow_scrape = oryxflow.Workflow(path=scraper.dirpath, env='prod')	
        with pytest.raises(Exception) as e_info:
            flow_scrape.outputLoad(scraper.tasks.Task1C)

        flow_scrape.run(scraper.tasks.Task1C)
        output = flow_scrape.outputLoad(scraper.tasks.Task1C)
        assert output[0].equals(pd.DataFrame({'a': range(10)}))


# Test Input Load
class TaskSingleOutput(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(pd.DataFrame({'a': range(3)}))

class TaskSingleOutput1(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(pd.DataFrame({'b': range(3)}))

class TaskSingleOutput2(oryxflow.tasks.TaskPqPandas):
    def run(self):
        self.save(pd.DataFrame({'c': range(3)}))

class TaskMultipleOutput(oryxflow.tasks.TaskPqPandas):
    persist = ["output1", "output2"]

    def run(self):
        x = pd.DataFrame({'ax': range(3)})
        y = pd.DataFrame({'ay': range(3)})
        self.save({"output1": x, "output2": y})

class TaskMultipleOutput1(oryxflow.tasks.TaskPqPandas):
    persist = ["output1", "output2"]

    def run(self):
        x = pd.DataFrame({'ax': range(3)})
        y = pd.DataFrame({'ay': range(3)})
        self.save({"output1": x, "output2": y})

class TaskMultipleOutput2(oryxflow.tasks.TaskPqPandas):
    persist = ["output1", "output2"]

    def run(self):
        x = pd.DataFrame({'ax': range(3)})
        y = pd.DataFrame({'ay': range(3)})
        self.save({"output1": x, "output2": y})

class TestInputLoad():
    # Single dependency, Single output
    def test_task_inputLoad_single_single(self):
        @oryxflow.requires(TaskSingleOutput)
        class TaskSingleInput(oryxflow.tasks.TaskPqPandas):
            def run(self):
                data = self.inputLoad()
                assert data.equals(pd.DataFrame({'a': range(3)}))

        oryxflow.run(TaskSingleInput(), forced_all=True,
                    forced_all_upstream=True, confirm=False)

    # Single dependency, Multiple outputs
    def test_task_inputLoad_single_multiple(self):
        @oryxflow.requires(TaskMultipleOutput)
        class TaskSingleInput2(oryxflow.tasks.TaskPqPandas):
            def run(self):
                output1, output2 = self.inputLoad()
                assert output1.equals(pd.DataFrame({'ax': range(3)}))
                assert output2.equals(pd.DataFrame({'ay': range(3)}))

                outputDict = self.inputLoad(as_dict=True)
                assert outputDict['output1'].equals(pd.DataFrame({'ax': range(3)}))
                assert outputDict['output2'].equals(pd.DataFrame({'ay': range(3)}))

        oryxflow.run(TaskSingleInput2(), forced_all=True,
                    forced_all_upstream=True, confirm=False)

    # Multiple dependencies, Single output
    def test_task_inputLoad_multiple_single(self):
        @oryxflow.requires({'input1': TaskSingleOutput1, 'input2': TaskSingleOutput2})
        class TaskMultipleInput(oryxflow.tasks.TaskPqPandas):
            def run(self):
                data1 = self.inputLoad()['input1']
                assert(data1.equals(pd.DataFrame({'b': range(3)})))
                data2 = self.inputLoad()['input2']
                assert(data2.equals(pd.DataFrame({'c': range(3)})))

        oryxflow.run(TaskMultipleInput(), forced_all=True,
                    forced_all_upstream=True, confirm=False)

    # Multiple dependencies, Multiple outputs
    def test_task_inputLoad_multiple_multiple(self):
        @oryxflow.requires({'input1': TaskMultipleOutput1, 'input2': TaskMultipleOutput2})
        class TaskMultipleInput2(oryxflow.tasks.TaskPqPandas):
            def run(self):
                data = self.inputLoad(as_dict=True)
                assert data["input1"]["output1"].equals(data["input2"]["output1"])
                assert data["input1"]["output2"].equals(data["input2"]["output2"])

                data1a, data1b = self.inputLoad()["input1"]
                data2a, data2b = self.inputLoad()["input2"]

                assert data1a.equals(data2a)
                assert data1b.equals(data2b)

                data1a, data1b = self.inputLoad(task='input1')
                data2a, data2b = self.inputLoad(task='input2')
                assert data1a.equals(data2a)
                assert data1b.equals(data2b)

                data1 = self.inputLoad(task='input1', as_dict=True)
                data2 = self.inputLoad(task='input2', as_dict=True)
                assert data1["output1"].equals(data2["output1"])
                assert data1["output2"].equals(data2["output2"])

        oryxflow.run(TaskMultipleInput2(), forced_all=True,
                    forced_all_upstream=True, confirm=False)

    # Multiple dependencies, Multiple outputs (Tuples)
    def test_task_inputLoad_multiple_multiple_tuple(self):
        @oryxflow.requires(TaskMultipleOutput1, TaskMultipleOutput2)
        class TaskMultipleInput2(oryxflow.tasks.TaskPqPandas):
            def run(self):
                data = self.inputLoad(as_dict=True)
                assert data[0]["output1"].equals(data[1]["output1"])
                assert data[0]["output2"].equals(data[1]["output2"])

                assert isinstance(self.inputLoad(), list)
                data1a, data1b = self.inputLoad()[0]
                data2a, data2b = self.inputLoad()[1]

                assert data1a.equals(data2a)
                assert data1b.equals(data2b)

                data1a, data1b = self.inputLoad(task=0)
                data2a, data2b = self.inputLoad(task=1)
                assert data1a.equals(data2a)
                assert data1b.equals(data2b)

                data1 = self.inputLoad(task=0, as_dict=True)
                data2 = self.inputLoad(task=1, as_dict=True)
                assert data1["output1"].equals(data2["output1"])
                assert data1["output2"].equals(data2["output2"])

        oryxflow.run(TaskMultipleInput2(), forced_all=True,
                    forced_all_upstream=True, confirm=False)


# Test Output Load
import sklearn.datasets
# Get training data and save it
class GetData(oryxflow.tasks.TaskPqPandas):
    persist = ['xx','yy']

    def run(self):
        ds = sklearn.datasets.load_diabetes()
        df_trainX = pd.DataFrame(ds.data, columns=ds.feature_names)
        df_trainY = pd.DataFrame(ds.target, columns=['target'])
        self.save({'xx': df_trainX, 'yy': df_trainY}) # persist/cache training data

class TestOutputLoad:

    def test_outputLoad_correct(self):
        # Execute task including all its dependencies
        flow = oryxflow.Workflow(GetData)
        flow.run()

        # Complete
        is_complete = flow.complete()
        assert is_complete

        # Get Data
        df = GetData().outputLoad(keys='xx')
        assert (type(df)) is pd.DataFrame
        with pytest.raises(Exception):
            exeption_df = df[0]

        df = GetData().outputLoad(keys=['xx'])
        assert (type(df)) is list
        assert (type(df[0])) is pd.DataFrame

        df_2 = GetData().outputLoad(keys=['xx', 'yy'])
        assert (type(df_2)) is list
        assert (type(df_2[0])) is pd.DataFrame
        assert (type(df_2[1])) is pd.DataFrame

        df_3 = GetData().outputLoad()
        assert (type(df_3)) is list
        assert (type(df_3[0])) is pd.DataFrame
        assert (type(df_3[1])) is pd.DataFrame

        # Key not present
        with pytest.raises(Exception):
            GetData().outputLoad(keys='not-there')


# Test Output Path
class PathTask1(oryxflow.tasks.TaskCache):

    def run(self):
        df = pd.DataFrame({'a': range(3)})
        self.save(df)  # quickly save dataframe

@oryxflow.requires(PathTask1)
class PathTask2(oryxflow.tasks.TaskPqPandas):
    path = 'data/data2_changed'

    def run(self):
        df1 = self.inputLoad() * 2
        self.save(df1)

@oryxflow.requires(PathTask2)
class PathTask3(oryxflow.tasks.TaskPqPandas):

    def run(self):
        df2 = self.inputLoad() * 3
        self.save(df2)

class PathTaskMultiOutput(oryxflow.tasks.TaskPqPandas):
    persist = ['xx','yy']

    def run(self):
        ds = sklearn.datasets.load_diabetes()
        df_trainX = pd.DataFrame(ds.data, columns=ds.feature_names)
        df_trainY = pd.DataFrame(ds.target, columns=['target'])
        self.save({'xx': df_trainX, 'yy': df_trainY}) # persist/cache training data

class TestWorkflowOutput:
    '''
    tasks.output().path is correct
    task2.outputLoad() is task1.outputLoad() * 2
    after reset no output in 'data/data2_changed/task2
    '''
    def test_workflow_df_and_path_correct(self):
        # Execute task including all its dependencies
        flow = oryxflow.Workflow(PathTask3)
        flow.run()
        
        # DF
        df_1 = flow.outputLoad(PathTask1)
        df_2 = flow.outputLoad(PathTask2)
        df_3 = flow.outputLoad(PathTask3)
        assert df_1.equals(pd.DataFrame({'a': range(3)}))
        assert df_2.equals(pd.DataFrame({'a': range(3)}) * 2)
        assert df_2.equals(df_1 * 2)
        assert df_3.equals(pd.DataFrame({'a': range(3)}) * 6)

        # Complete
        is_complete = flow.complete()
        assert is_complete

        # Path
        path_1 = flow.output(PathTask1).path
        path_2 = flow.output(PathTask2).path
        path_3 = flow.output(PathTask3).path
        assert path_1 == Path("data/PathTask1/PathTask1__99914b932b-data.cache")
        assert path_2 == Path("data/data2_changed/PathTask2/PathTask2__99914b932b-data.parquet")
        assert path_3 == Path("data/PathTask3/PathTask3__99914b932b-data.parquet")
        # Dir is not Empty
        p = Path('.')
        dir = list(p.glob('data/data2_changed/PathTask2/*'))
        assert len(dir) == 1
        dir = list(p.glob('data/PathTask3/*'))
        # dir = os.listdir("data/PathTask3")
        assert len(dir) == 1

        # Reset
        flow.reset(PathTask2)
        flow.reset(PathTask3)
        dir = list(p.glob('data/data2_changed/PathTask2/*'))
        assert len(dir) == 0
        dir = list(p.glob('data/PathTask3/*'))
        assert len(dir) == 0

    def test_workflow_outputPath(self):
        # Execute task including all its dependencies
        flow = oryxflow.Workflow(PathTask3)
        flow.run()

        path_1 = flow.outputPath(PathTask1)
        path_2 = flow.outputPath(PathTask2)
        assert path_1 == Path("data/PathTask1/PathTask1__99914b932b-data.cache")
        assert path_2 == Path("data/data2_changed/PathTask2/PathTask2__99914b932b-data.parquet")

        # Test Multi Output
        # Execute task including all its dependencies
        flow = oryxflow.Workflow(PathTaskMultiOutput)
        flow.run()

        multi_path_1 = flow.outputPath(PathTaskMultiOutput)
        assert multi_path_1['xx'] == Path("data/PathTaskMultiOutput/PathTaskMultiOutput__99914b932b-xx.parquet")
        assert multi_path_1['yy'] == Path("data/PathTaskMultiOutput/PathTaskMultiOutput__99914b932b-yy.parquet")

    def test_workflow_set_path_and_env(self):
        # Execute task including all its dependencies
        flow = oryxflow.Workflow(PathTask3, env='prod')
        flow.run()

        path_2 = flow.outputPath(PathTask2)
        path_3 = flow.outputPath(PathTask3)
        assert path_2 == Path("data/env=prod/PathTask2/PathTask2__99914b932b-data.parquet")
        assert path_3 == Path("data/env=prod/PathTask3/PathTask3__99914b932b-data.parquet")
        flow.reset()

        # Execute task including all its dependencies
        flow = oryxflow.Workflow(PathTask3, path='data/data2/wf_change')
        flow.run()

        path_2 = flow.outputPath(PathTask2)
        path_3 = flow.outputPath(PathTask3)
        assert path_2 == Path("data/data2/wf_change/PathTask2/PathTask2__99914b932b-data.parquet")
        assert path_3 == Path("data/data2/wf_change/PathTask3/PathTask3__99914b932b-data.parquet")
        flow.reset()

        # Execute task including all its dependencies
        flow = oryxflow.Workflow(PathTask3, path='data/data2/wf_change', env='prod')
        flow.run()

        path_2 = flow.outputPath(PathTask2)
        path_3 = flow.outputPath(PathTask3)
        assert path_2 == Path("data/data2/wf_change/env=prod/PathTask2/PathTask2__99914b932b-data.parquet")
        assert path_3 == Path("data/data2/wf_change/env=prod/PathTask3/PathTask3__99914b932b-data.parquet")
        flow.reset()

        # Execute task including all its dependencies
        flow = oryxflow.Workflow(path='data/data2/wf_change_run')
        flow.run(PathTask3, forced_all=True)

        path_2 = flow.outputPath(PathTask2)
        path_3 = flow.outputPath(PathTask3)
        assert path_2 == Path("data/data2/wf_change_run/PathTask2/PathTask2__99914b932b-data.parquet")
        assert path_3 == Path("data/data2/wf_change_run/PathTask3/PathTask3__99914b932b-data.parquet")
        flow.reset(PathTask3)

# Test Meta
import pickle
class TestMeta():
    def test_metaSave_persists_after_run(self):
        class MetaSave(oryxflow.tasks.TaskCache):
            def run(self):
                df = pd.DataFrame({'a': range(3)})
                self.save(df)  # quickly save dataframe
                self.metaSave({'metadata': df})

        ms = MetaSave()
        oryxflow.run(ms)

        # From disk
        metadata = pickle.load(open(ms._get_meta_path(ms), "rb"))
        assert metadata['metadata'].equals(
            pd.DataFrame({'a': range(3)})
        )

        # From object
        assert ms.metadata['metadata'].equals(
            pd.DataFrame({'a': range(3)})
        )

    def test_metaLoad_single_input(self):
        class MetaSave(oryxflow.tasks.TaskCache):
            def run(self):
                df = pd.DataFrame({'a': range(3)})
                self.save(df)  # quickly save dataframe
                self.metaSave({'metadata': df})

        @oryxflow.requires(MetaSave)
        class MetaLoad(oryxflow.tasks.TaskCache):
            def run(self):
                meta = self.metaLoad()
                assert meta['metadata'].equals(
                    pd.DataFrame({'a': range(3)})
                )

        oryxflow.run(MetaLoad())

    def test_metaLoad_multiple_input(self):
        class MetaSave(oryxflow.tasks.TaskCache):
            def run(self):
                df = pd.DataFrame({'a': range(3)})
                self.save(df)  # quickly save dataframe
                self.metaSave({'metadata': df})

        class MetaSave2(MetaSave):
            pass

        @oryxflow.requires({'upstream1': MetaSave, 'upstream2': MetaSave2})
        class MetaLoad(oryxflow.tasks.TaskCache):
            def run(self):
                meta = self.metaLoad()
                assert meta['upstream1']['metadata'].equals(
                    pd.DataFrame({'a': range(3)})
                )
                assert meta['upstream2']['metadata'].equals(
                    pd.DataFrame({'a': range(3)})
                )

        oryxflow.run(MetaLoad())

    def test_metaLoad_multiple_input_tuple(self):
        class MetaSave(oryxflow.tasks.TaskCache):
            def run(self):
                df = pd.DataFrame({'a': range(3)})
                self.save(df)  # quickly save dataframe
                self.metaSave({'metadata': df})

        class MetaSave2(MetaSave):
            pass

        @oryxflow.requires(MetaSave, MetaSave2)
        class MetaLoad(oryxflow.tasks.TaskCache):
            def run(self):
                meta = self.metaLoad()
                assert meta[0]['metadata'].equals(
                    pd.DataFrame({'a': range(3)})
                )
                assert meta[1]['metadata'].equals(
                    pd.DataFrame({'a': range(3)})
                )

        oryxflow.run(MetaLoad())

    def test_outputLoadMeta(self):
        class MetaSave(oryxflow.tasks.TaskCache):
            def run(self):
                df = pd.DataFrame({'a': range(3)})
                self.save(df)  # quickly save dataframe
                self.metaSave({'metadata': df})

        oryxflow.run(MetaSave())
        df = pd.DataFrame({'a': range(3)})
        assert MetaSave().outputLoadMeta()["metadata"].equals(df)

    def test_outputLoadAllMeta(self):
        class OutputMetaSave1(oryxflow.tasks.TaskCache):

            def run(self):
                df = pd.DataFrame({'a': range(3)})
                self.save(df)  # quickly save dataframe
                self.metaSave({'columns': 42})

        class OutputMetaSave2(OutputMetaSave1):
            pass

        @oryxflow.requires({'upstream1': OutputMetaSave1, 'upstream2': OutputMetaSave2})
        class OutputMetaSave3(oryxflow.tasks.TaskCache):
            multiplier = oryxflow.IntParameter(default=2)

            def run(self):
                meta = self.metaLoad()['upstream1']
                print(meta)
                print(meta['columns'])

                df1 = self.input()['upstream1'].load()  # quickly load input data
                df2 = self.input()['upstream2'].load()  # quickly load input data
                df = df1.join(df2, lsuffix='1', rsuffix='2')
                df['b'] = df['a1']*self.multiplier  # use task parameter
                self.save(df)
                self.metaSave({'columns': 100})

        oryxflow.run(OutputMetaSave3())
        meta_all = OutputMetaSave3().outputLoadAllMeta()
        assert meta_all["OutputMetaSave1"]["columns"] == 42
        assert meta_all["OutputMetaSave2"]["columns"] == 42
        assert meta_all["OutputMetaSave3"]["columns"] == 100


# Test SubFlows
class SubTask1(oryxflow.tasks.TaskCache):

    def run(self):
        df = pd.DataFrame({'a': range(3)})
        self.save(df)  # quickly save dataframe

@oryxflow.requires(SubTask1)
class SubTask2(oryxflow.tasks.TaskPqPandas):
    path = 'data/data2_changed'

    def run(self):
        df1 = self.inputLoad() * 2
        self.save(df1)

class SubTask3(oryxflow.tasks.TaskPqPandas):
    
    def run(self):
        temp_flow_df = self.flows['flow'].outputLoad()
        self.save(temp_flow_df)

class TestSubFlows:

    def test_sub_flow_correct(self):
        # Assign Flows
        flow = oryxflow.Workflow(SubTask2)
        flow2 = oryxflow.Workflow(SubTask3)
        flow.run()

        flow2.attach_flow(flow, 'flow')
        flow2.run()

        df_flow_task_1 = flow.outputLoad(SubTask1)
        df_flow_task_2 = flow.outputLoad()
        df_flow_2 = flow2.outputLoad()
        assert df_flow_task_1.equals(pd.DataFrame({'a': range(3)}))
        assert df_flow_task_2.equals(pd.DataFrame({'a': range(3)}) * 2)
        assert df_flow_2.equals(pd.DataFrame({'a': range(3)}) * 2)
