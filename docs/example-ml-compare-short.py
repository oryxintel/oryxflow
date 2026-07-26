import oryxflow
import sklearn.datasets, sklearn.ensemble, sklearn.linear_model
import pandas as pd


# get training data and save it
class GetData(oryxflow.tasks.TaskPqPandas):
    persists = ['x','y']

    def run(self):
        ds = sklearn.datasets.load_diabetes()
        df_trainX = pd.DataFrame(ds.data, columns=ds.feature_names)
        df_trainY = pd.DataFrame(ds.target, columns=['target'])
        self.save({'x': df_trainX, 'y': df_trainY}) # persist/cache training data


# train different models to compare
@oryxflow.requires(GetData)  # define dependency
class ModelTrain(oryxflow.tasks.TaskPickle):
    model = oryxflow.Parameter()  # parameter for model selection

    def run(self):
        df_trainX, df_trainY = self.inputLoad()  # quickly load input data
        y = df_trainY['target']  # sklearn wants a 1d target

        if self.model=='ols':  # select model based on parameter
            model = sklearn.linear_model.LinearRegression()
        elif self.model=='gbm':
            model = sklearn.ensemble.GradientBoostingRegressor()

        # fit and save model with training score
        model.fit(df_trainX, y)
        self.save(model)  # persist/cache model
        self.saveMeta({'score': model.score(df_trainX, y)})  # save model score

# goal: compare performance of two models
# define workflow manager
flow = oryxflow.WorkflowMulti(ModelTrain, {'model1':{'model':'ols'}, 'model2':{'model':'gbm'}})
flow.reset_upstream(confirm=False) # DEMO ONLY: force re-run
flow.run()  # execute model training including all dependencies

'''
===== model1 =====
Scheduled 2 tasks of which:
* 0 complete ones were encountered
* 2 ran successfully:
    - GetData
    - ModelTrain(model=ols)
This progress looks :) because there were no failed tasks or missing dependencies

===== model2 =====
Scheduled 2 tasks of which:
* 1 complete ones were encountered:
    - GetData
* 1 ran successfully:
    - ModelTrain(model=gbm)
This progress looks :) because there were no failed tasks or missing dependencies
'''

scores = flow.outputLoadMeta()  # load model scores
print(scores)
# {'model1': {'score': 0.52}, 'model2': {'score': 0.80}}
