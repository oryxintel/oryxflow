"""pytest file built from docs/index.md"""
import pytest

from phmdoctest.fixture import managenamespace


def test_code_24(managenamespace):
    import oryxflow
    import pandas as pd

    oryxflow.set_dir('data/')

    class GetData(oryxflow.tasks.TaskPqPandas):        # output saved as parquet
        def run(self):
            self.save(pd.DataFrame({'x': range(10)}))

    @oryxflow.requires(GetData)                        # declare the dependency
    class ProcessData(oryxflow.tasks.TaskPqPandas):
        def run(self):
            df = self.inputLoad()                      # GetData's output, already loaded
            df['x2'] = df['x'] ** 2
            self.save(df)

    flow = oryxflow.Workflow(ProcessData)
    flow.run()                                         # runs GetData, then ProcessData
    df = flow.outputLoad()                             # load the result by name

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())
