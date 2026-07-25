"""pytest file built from docs/docs/targets.md"""
import pytest

from phmdoctest.fixture import managenamespace


def test_code_53(managenamespace):
    import oryxflow

    oryxflow.set_dir('data/')

    class GetConfig(oryxflow.tasks.TaskJson):

        def run(self):
            self.save({'features': ['x1', 'x2'], 'nrows': 100})

    @oryxflow.requires(GetConfig)
    class SummarizeConfig(oryxflow.tasks.TaskJson):

        def run(self):
            cfg = self.inputLoad()   # a plain python dict, no dataframe involved
            self.save({'nfeatures': len(cfg['features'])})

    flow = oryxflow.Workflow(SummarizeConfig)
    flow.run()
    flow.outputLoad()   # {'nfeatures': 2}

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_78(managenamespace):
    class ScoreModel(oryxflow.tasks.TaskJson):
        persists = ['metrics', 'warnings']

        def run(self):
            self.save({'metrics': {'auc': 0.81}, 'warnings': ['few samples in fold 3']})

    flow_score = oryxflow.Workflow(ScoreModel)
    flow_score.run()
    metrics = flow_score.outputLoad(keys='metrics')

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_95(managenamespace):
    class TrainModel(oryxflow.tasks.TaskPickle):

        def run(self):
            from sklearn.linear_model import LogisticRegression
            self.save(LogisticRegression())

    flow_model = oryxflow.Workflow(TrainModel)
    flow_model.run()
    model = flow_model.outputLoad()

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_114(managenamespace):
    class WriteReport(oryxflow.tasks.TaskMarkdown):

        def run(self):
            self.save('# Results\n\nThe model scored 0.81 AUC.\n')

    flow_report = oryxflow.Workflow(WriteReport)
    flow_report.run()
    flow_report.outputPath()   # data/WriteReport/...-data.md (plus the .html alongside)

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())
