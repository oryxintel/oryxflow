"""pytest file built from docs/docs/quickstart.md"""
import pytest

from phmdoctest.fixture import managenamespace


def test_code_24(managenamespace):
    import oryxflow
    import pandas as pd

    oryxflow.set_dir('data/')                       # where task outputs are cached

    class GetData(oryxflow.tasks.TaskPqPandas):     # output saved as parquet
        def run(self):
            df = pd.DataFrame({'x': range(10)})
            self.save(df)                           # cache this task's output

    @oryxflow.requires(GetData)                     # declare the dependency on GetData
    class ProcessData(oryxflow.tasks.TaskPqPandas):
        def run(self):
            df = self.inputLoad()                   # GetData's output, already loaded
            df['x2'] = df['x'] ** 2
            self.save(df)

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_46(managenamespace):
    flow = oryxflow.Workflow(ProcessData)

    flow.preview()          # show what will run, without running it
    flow.run()              # runs GetData, then ProcessData

    df = flow.outputLoad()  # load ProcessData's result by referencing the flow
    print(df.head())

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_63(managenamespace):
    @oryxflow.requires(GetData)
    class ProcessData(oryxflow.tasks.TaskPqPandas):
        power = oryxflow.IntParameter(default=2)    # a knob to experiment with
        def run(self):
            df = self.inputLoad()
            df['x_pow'] = df['x'] ** self.power
            self.save(df)

    # run with a non-default parameter
    flow = oryxflow.Workflow(ProcessData, {'power': 3})
    flow.run()              # GetData is already complete and is skipped; only ProcessData runs
    df = flow.outputLoad()

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_85(managenamespace):
    import oryxflow
    import pandas as pd
    import sklearn.datasets, sklearn.preprocessing
    import sklearn.linear_model, sklearn.ensemble

    oryxflow.set_dir('data/')

    class GetDiabetes(oryxflow.tasks.TaskPqPandas):
        def run(self):
            ds = sklearn.datasets.load_diabetes()
            df = pd.DataFrame(ds.data, columns=ds.feature_names)
            df['y'] = ds.target
            self.save(df)

    @oryxflow.requires(GetDiabetes)                 # inherits GetDiabetes's params, wires the dependency
    class ModelData(oryxflow.tasks.TaskPqPandas):
        do_preprocess = oryxflow.BoolParameter(default=True)   # preprocessing on/off
        def run(self):
            df = self.inputLoad()
            if self.do_preprocess:
                df.iloc[:, :-1] = sklearn.preprocessing.scale(df.iloc[:, :-1])
            self.save(df)

    @oryxflow.requires(ModelData)                   # parameters flow upstream automatically
    class ModelTrain(oryxflow.tasks.TaskPickle):    # a model object → saved as pickle
        model = oryxflow.Parameter(default='ols')   # which model to train
        def run(self):
            df = self.inputLoad()
            X, y = df.drop(columns='y'), df['y']
            if self.model == 'ols':
                m = sklearn.linear_model.LinearRegression()
            elif self.model == 'gbm':
                m = sklearn.ensemble.GradientBoostingRegressor()
            else:
                raise ValueError('invalid model selection')
            m.fit(X, y)
            self.save(m)
            self.saveMeta({'score': m.score(X, y)})   # save a small metadata sidecar

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_129(managenamespace):
    flow = oryxflow.WorkflowMulti(ModelTrain, {
        'ols': {'do_preprocess': True,  'model': 'ols'},
        'gbm': {'do_preprocess': False, 'model': 'gbm'},
    })
    flow.run()      # GetDiabetes runs once and is shared — the 'gbm' flow reuses it, it doesn't refetch

    print(flow.outputLoadMeta())          # scores from the metadata sidecars
    # {'ols': {'score': 0.52}, 'gbm': {'score': 0.80}}

    models = flow.outputLoad(ModelTrain)  # {'ols': <fitted model>, 'gbm': <fitted model>}

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())
