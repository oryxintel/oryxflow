"""pytest file built from docs/docs/targets.md"""
import pytest

from phmdoctest.fixture import managenamespace


def test_code_54(managenamespace):
    import oryxflow

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


def test_code_77(managenamespace):
    class ScoreModel(oryxflow.tasks.TaskJson):
        persists = ['metrics', 'warnings']

        def run(self):
            self.save({'metrics': {'auc': 0.81}, 'warnings': ['few samples in fold 3']})

    flow_score = oryxflow.Workflow(ScoreModel)
    flow_score.run()
    metrics = flow_score.outputLoad(keys='metrics')

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_94(managenamespace):
    class TrainModel(oryxflow.tasks.TaskPickle):

        def run(self):
            from sklearn.linear_model import LogisticRegression
            self.save(LogisticRegression())

    flow_model = oryxflow.Workflow(TrainModel)
    flow_model.run()
    model = flow_model.outputLoad()

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_113(managenamespace):
    class WriteReport(oryxflow.tasks.TaskMarkdown):

        def run(self):
            self.save('# Results\n\nThe model scored 0.81 AUC.\n')

    flow_report = oryxflow.Workflow(WriteReport)
    flow_report.run()
    flow_report.outputPath()   # data/WriteReport/...-data.md (plus the .html alongside)

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_129(managenamespace):
    import pandas as pd

    df_perf = pd.DataFrame({
        'name': ['Alpha', 'Beta'],
        'revenue': [1234567.891, 98765.4],
        'growth_pct': [0.053, -0.021],
        'as_of': pd.to_datetime(['2026-01-01', '2026-01-01']),
    })

    class WritePerformance(oryxflow.tasks.TaskExcelPandas):
        persists = ['summary']

        def run(self):
            self.save({'summary': df_perf}, formatted=True, formats={'*_pct': 'pct'})

    flow_perf = oryxflow.Workflow(WritePerformance)
    flow_perf.run()

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_152(managenamespace):
    oryxflow.utils.excel_write('report.xlsx', {'summary': df_perf})

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_181(managenamespace):
    df_rank = pd.DataFrame({
        'name': ['Alpha County', 'Beta County'],
        'rating': ['Green', 'Red'],
        'ret_1y': [0.081, -0.034],
        'rank_chg': [3, -2],
        'fips': ['01001', '06037'],
    })

    class WriteRankings(oryxflow.tasks.TaskExcelPandas):
        persists = ['retail']

        def run(self):
            sheets = {'retail': {
                'df': df_rank,
                'title': 'Market Rankings - Retail',
                'notes': ['Green = top quintile, Red = bottom quintile'],
                'formats': {'ret_*': 'pct', 'rank_chg': 'int_signed'},
                'highlight': {'rating': {'Green': 'green', 'Red': 'red'}},
                'colorscale': ['rank_chg'],
                'id_columns': ['fips'],
                'freeze': 2,
            }}
            self.save(sheets, formatted=True)

    flow_rank = oryxflow.Workflow(WriteRankings)
    flow_rank.run()

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())


def test_code_213(managenamespace):
    path_rank = flow_rank.outputPath()
    back = pd.read_excel(path_rank, sheet_name='retail', skiprows=3, dtype={'fips': str})

    # Caution- no assertions.
    managenamespace(operation="update", additions=locals())
