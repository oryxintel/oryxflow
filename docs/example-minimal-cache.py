import oryxflow
import pandas as pd


# define 2 tasks that load raw data
class Task1(oryxflow.tasks.TaskCache):
    p1 = oryxflow.Parameter(default='1'*20)
    p2 = oryxflow.Parameter(default='2'*20, significant=False)

    def run(self):
        print(self.task_id)
        df = pd.DataFrame({'a':range(3)})
        self.save(df) # quickly save dataframe


class Task2(Task1):
    pass

# define another task that depends on data from task1 and task2
@oryxflow.requires(Task1, Task2)
class Task3(oryxflow.tasks.TaskCache):
    multiplier = oryxflow.IntParameter(default=2)

    def run(self):
        df1, df2 = self.inputLoad()  # quickly load input data
        df = df1.join(df2, lsuffix='1', rsuffix='2')
        df['b'] = df['a1'] * self.multiplier  # use task parameter
        self.save(df)


# Execute task including all its dependencies
flow = oryxflow.Workflow(Task3)
flow.run()

flow.outputLoad()

flow2 = oryxflow.Workflow(Task3, {'multiplier':3})
flow2.preview()
