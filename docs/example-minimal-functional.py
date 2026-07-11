import pandas as pd

import oryxflow
from oryxflow.functional import Workflow

flow = Workflow()


@flow.task(oryxflow.tasks.TaskPqPandas)
def Task1(task):
    df = pd.DataFrame({'a': range(3)})
    task.save(df)


@flow.task(oryxflow.tasks.TaskPqPandas)
def Task2(task):
    df = pd.DataFrame({'b': range(3)})
    task.save(df)


@flow.task(oryxflow.tasks.TaskPqPandas)
@flow.params(multiplier=oryxflow.IntParameter(default=2))
@flow.requires({'input1': Task1, 'input2': Task2})
def Task3(task):
    df1 = task.input()['input1'].load()  # quickly load input data
    df2 = task.input()['input2'].load()  # quickly load input data
    df = df1.join(df2, lsuffix='1', rsuffix='2')
    df['c'] = df['a'] * task.multiplier  # use task parameter
    task.save(df)


flow.run(Task3)
print(flow.outputLoad(Task3))
'''
   a  b  c
0  0  0  0
1  1  1  2
2  2  2  4
'''

# You can rerun the flow with different parameters
flow.run(Task3, params={'multiplier': 3})
print(flow.outputLoad(Task3))

'''
   a  b  c
0  0  0  0
1  1  1  3
2  2  2  6
'''