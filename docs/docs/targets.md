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
