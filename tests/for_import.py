import oryxflow
import pandas as pd
import pathlib

# Vars
df = pd.DataFrame({'a': range(10)})
cfg_write_dir = pathlib.Path('tests')
cfg_write_filename_tasks = 'tasks_export.py'

class Task1A0(oryxflow.tasks.TaskCache):
    pass

class Task1A(Task1A0):
    persist=['df','df2']
    # path = "usr/test/ok.com"
    idx=oryxflow.IntParameter(default=1)
    idx2=oryxflow.Parameter(default='test')
    def run(self):
        self.save({'df': df, 'df2': df})

class Task1B(oryxflow.tasks.TaskCache):
    persist=['df','df2']
    idx3=oryxflow.Parameter(default='test3')
    def run(self):
        self.save({'df': df,'df2': df})

class Task1C(oryxflow.tasks.TaskCache):
    persist=['df','df2']
    idx3=oryxflow.Parameter(default='test3')
    export = False
    def run(self):
        self.save({'df': df,'df2': df})

@oryxflow.requires(Task1A, Task1B, Task1C)
class Task1All(oryxflow.tasks.TaskCache):
    task_group = "upwork"
    def run(self):
        self.save(df)

# Tests
def test_task():
    flow = oryxflow.Workflow(Task1All, path='data/data2/wf_change', env='prod')
    e = oryxflow.FlowExport(flows=Task1All())
    e.generate()

