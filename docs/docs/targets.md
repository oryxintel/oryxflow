# Task I/O Targets

The format your task output is saved in matters more than it first appears: it decides how fast your pipeline reads and writes between steps, whether a result survives a restart or lives only for the session, and whether a teammate can open the file directly. oryxflow lets you pick that format by **choosing a parent class** — you never write save/load code, and you can switch a task from parquet to CSV (or to an in-memory cache while you iterate) by changing one base class.

## How is task data saved and loaded?

Task data is saved in a file or memory (cache). You control the format by choosing the right parent class for a task. In the example below, data is saved as parquet and loaded as a pandas dataframe because the parent class is `TaskPqPandas`.

<!--phmdoctest-skip-->
```python
class YourTask(oryxflow.tasks.TaskPqPandas):
```

**Your output does not have to be a dataframe.** A task can save a plain python dict as JSON, any python object as pickle, or a report as markdown, just as easily. Pick the class that matches the object you already have — you should never have to reshape a dict into a dataframe to get it saved.

### Task Output Location

By default file-based task output is saved in `data/`. You can customize where task output is saved.

<!--phmdoctest-skip-->
```python
oryxflow.set_dir('../data')
```

## Which task class should I use?

Start from the object you want to save and read across:

| What you're saving | Use | Saved as | You get back |
| --- | --- | --- | --- |
| A dataframe | `TaskPqPandas` | `.parquet` | dataframe |
| A dataframe someone needs to open | `TaskCSVPandas` | `.csv` | dataframe |
| A large dataframe as CSV | `TaskCSVGZPandas` | `.csv.gz` | dataframe |
| Several dataframes for one Excel file | `TaskExcelPandas` | one `.xlsx`, one sheet per output | dataframe |
| Several dataframes as separate files | `TaskExcelPandasSingle` | one `.xlsx` per output | dataframe |
| A dict, list, or config | `TaskJson` | `.json` | the same dict/list |
| A trained model or any python object | `TaskPickle` | `.pkl` | the same object |
| A written report | `TaskMarkdown` | `.md` **and** `.html` | markdown string |
| A parameterized report notebook | `TaskNotebook` ([guide](reports.md)) | `.ipynb` **and** `.html` | path of the `.html` |
| Anything, but only for this session | `TaskCache` | memory | the same object |
| A dataframe, only for this session | `TaskCachePandas` | memory | dataframe |
| A dataframe to a database | `TaskSQLPandas` | SQL table | dataframe (premium, see below) |

A rough guide: reach for **parquet** (`TaskPqPandas`) for most dataframes — it's fast and compact and keeps dtypes; **CSV/Excel** when a human needs to open the file; **JSON** for anything dict-shaped you'd like to be able to read and diff; **pickle** for trained models or python objects JSON can't express; and the in-memory **cache** targets (`TaskCache*`) for intermediate results you don't need on disk between runs (fastest, but gone when the process exits).

dask, SQL and pyspark are premium features, see below.

## Saving dicts and JSON

`TaskJson` saves anything JSON-serializable — a dict, a list, nested combinations — and gives you back exactly that object. It's the natural choice for configs, parameter sets, summary metrics, API responses and label maps, and the file stays readable and diffable in git.

<!--phmdoctest-share-names-->
```python
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
```

Multiple outputs work the same way as for dataframes — declare `persists` and save a dict keyed by those names (see [saving more than one output](tasks.md#save-output-data)):

<!--phmdoctest-share-names-->
```python
class ScoreModel(oryxflow.tasks.TaskJson):
    persists = ['metrics', 'warnings']

    def run(self):
        self.save({'metrics': {'auc': 0.81}, 'warnings': ['few samples in fold 3']})

flow_score = oryxflow.Workflow(ScoreModel)
flow_score.run()
metrics = flow_score.outputLoad(keys='metrics')
```

## Saving models and other python objects

If JSON can't express it — a fitted model, a scaler, a set, a custom class — use `TaskPickle`. It takes any picklable object and hands the same object back.

<!--phmdoctest-share-names-->
```python
class TrainModel(oryxflow.tasks.TaskPickle):

    def run(self):
        from sklearn.linear_model import LogisticRegression
        self.save(LogisticRegression())

flow_model = oryxflow.Workflow(TrainModel)
flow_model.run()
model = flow_model.outputLoad()
```

**NB**: don't save a dict of dataframes as pickle — save them as multiple outputs instead, see "save more than one output" in [Tasks](tasks.md#save-output-data).

## Saving reports

`TaskMarkdown` takes a markdown string and writes both a `.md` file and a styled `.html` file next to it, so a summary task can produce something you can send to someone.

<!--phmdoctest-share-names-->
```python
class WriteReport(oryxflow.tasks.TaskMarkdown):

    def run(self):
        self.save('# Results\n\nThe model scored 0.81 AUC.\n')

flow_report = oryxflow.Workflow(WriteReport)
flow_report.run()
flow_report.outputPath()   # data/WriteReport/...-data.md (plus the .html alongside)
```

## Excel files people will open

An Excel file that goes to a colleague should look finished: a styled header with filter arrows, percentages that read `5.3%`, dates without a time stamp, columns wide enough to read. Pass `formatted=True` when you save and oryxflow does that for you. The numbers underneath are untouched, so loading the file back gives the same data as an unformatted save.

<!--phmdoctest-share-names-->
```python
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
```

Decimal columns get thousands separators and two decimals, whole-number columns stay as typed (use `'int'` for separators), and date columns show as dates, unless you say otherwise with `formats`. Its keys are column names or patterns such as `'*_pct'`, and its values are one of the names below. Outside a task, write a file directly with `oryxflow.utils.excel_write`:

<!--phmdoctest-share-names-->
```python
oryxflow.utils.excel_write('report.xlsx', {'summary': df_perf})
```

| Name | Shows `1234.56` (or `0.053` for the percent names) as |
| --- | --- |
| `int` | `1,235` |
| `int_plain` | `1235` |
| `int_signed` | `+1235` (with a sign) |
| `float`, `num2` | `1,234.56` |
| `float4` | `1234.5600` |
| `num1` | `1,234.6` |
| `pct` | `5.3%` |
| `pct0` | `5%` |
| `pct2` | `5.30%` |
| `pct_signed` | `+5.3%` |
| `multiple` | `1234.56x` |
| `thousands` | `1k` |
| `date` | `2026-01-01` |
| `datetime` | `2026-01-01 09:30` |
| `text` | shown as typed |

You can also pass a Python format such as `'{:,.0f}'` or an Excel number format such as `'0.000'`. A misspelled name (`'pctt'`) raises an error that lists the valid names, instead of writing a file Excel would offer to "repair".

### A finished deliverable

For a file that goes straight to a client or manager, give each sheet a spec: the dataframe plus a title, notes and highlighting.

<!--phmdoctest-share-names-->
```python
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
```

The title and notes sit above the table, so the header row moves down. Read the data back by skipping those rows; ID columns are stored as text so codes like `01001` keep their leading zeros, and `dtype=` makes pandas keep them too:

<!--phmdoctest-share-names-->
```python
path_rank = flow_rank.outputPath()
back = pd.read_excel(path_rank, sheet_name='retail', skiprows=3, dtype={'fips': str})
```

Other sheet options: `colorscale` shades a column from low to high, `banded=True` stripes alternate rows, `wrap_columns` wraps long text, `group_starts` draws a divider before a column, `freeze=2` keeps the first two columns in view while scrolling, and `filters=False` turns the filter arrows off. Highlight colours are `'green'`, `'yellow'`, `'red'` and `'grey'`.

Options given to the whole save apply to every sheet, and a sheet's own options override them. A sheet's `formats` adds to the shared `formats` rather than replacing it: shared `formats={'*_pct': 'pct'}` plus a sheet's `{'*': 'num1'}` formats that sheet's percent columns as percents and the rest with one decimal. Set a column to `None` in the sheet's `formats` to drop a shared format for it, or `formats=False` for no number formats on that sheet.

`excel_write` also takes an open file instead of a path. To replace a shared file without anyone ever opening a half-written one, write to a temporary file and swap it in:

```python
import os

with open('report.xlsx.tmp', 'wb') as f:
    oryxflow.utils.excel_write(f, {'summary': df_perf})
os.replace('report.xlsx.tmp', 'report.xlsx')
```

Formatting styles each cell, so very large frames (hundreds of thousands of rows) write noticeably slower than an unformatted save; column widths are sized from the first 1,000 rows. Excel may show a small green "number stored as text" marker on ID columns; that is expected and harmless.

## Premium Targets (Dask, SQL, Pyspark)

### Database Targets

oryxflow premium has database targets.

### Dask Targets

oryxflow premium has dask targets.

### Pyspark Targets

oryxflow premium has pyspark targets.

## Community Targets

### Keras Model Targets

For saving Keras model targets

<!--phmdoctest-skip-->
```python
from oryxflow.tasks.h5 import TaskH5Keras
```

## Writing Your Own Targets

This is often relatively simple since you mostly need to implement <span class="title-ref">load()</span> and <span class="title-ref">save()</span> functions. For more advanced cases you also have to implement <span class="title-ref">exist()</span> and <span class="title-ref">invalidate()</span> functions. Check the source code for details or raise an issue.
