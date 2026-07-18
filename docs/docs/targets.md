# Task I/O Targets

The format your task output is saved in matters more than it first appears: it decides how fast your pipeline reads and writes between steps, whether a result survives a restart or lives only for the session, and whether a teammate can open the file directly. oryxflow lets you pick that format by **choosing a parent class** — you never write save/load code, and you can switch a task from parquet to CSV (or to an in-memory cache while you iterate) by changing one base class.

## How is task data saved and loaded?

Task data is saved in a file, database table or memory (cache). You can control how task output data is saved by chosing the right parent class for a task. In the example below, data is saved as parquet and loaded as a pandas dataframe because the parent class is `TaskPqPandas`. The python object you want to save determines how you can save the data.

```python
class YourTask(oryxflow.tasks.TaskPqPandas):
```

### Task Output Location

By default file-based task output is saved in `data/`. You can customize where task output is saved.

```python
oryxflow.set_dir('../data')
```

## Core task targets (Pandas)

What kind of object you want to save determines which Task class you need to use. A rough guide: reach for **parquet** (`TaskPqPandas`) for most dataframes — it's fast and compact and keeps dtypes; **CSV/Excel** when a human needs to open the file; the in-memory **cache** targets (`TaskCache*`) for intermediate results you don't need on disk between runs (fastest, but gone when the process exits); and **pickle** for trained models or arbitrary python objects.

- pandas  
  - `oryxflow.tasks.TaskPqPandas`: save to parquet, load as pandas
  - `oryxflow.tasks.TaskCachePandas`: save to memory, load as pandas
  - `oryxflow.tasks.TaskCSVPandas`: save to CSV, load as pandas
  - `oryxflow.tasks.TaskExcelPandas`: save to Excel, load as pandas
  - `oryxflow.tasks.TaskSQLPandas`: save to SQL, load as pandas (premium, see below)

- dicts  
  - `oryxflow.tasks.TaskJson`: save to JSON, load as python dict
  - `oryxflow.tasks.TaskPickle`: save to pickle, load as python list
  - **NB**: don't save a dict of pandas dataframes as pickle, instead save as multiple outputs, see "save more than one output" in [Tasks](tasks.md)

- any python object (eg trained models)  
  - `oryxflow.tasks.TaskPickle`: save to pickle, load as python list
  - `oryxflow.tasks.TaskCache`: save to memory, load as python object

- dask, SQL, pyspark: premium features, see below

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

```python
from oryxflow.tasks.h5 import TaskH5Keras
```

## Writing Your Own Targets

This is often relatively simple since you mostly need to implement <span class="title-ref">load()</span> and <span class="title-ref">save()</span> functions. For more advanced cases you also have to implement <span class="title-ref">exist()</span> and <span class="title-ref">invalidate()</span> functions. Check the source code for details or raise an issue.
