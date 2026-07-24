# oryxflow

[![Socket Badge](https://socket.dev/api/badge/pypi/package/oryxflow)](https://socket.dev/pypi/package/oryxflow)
[![PyPI version](https://img.shields.io/pypi/v/oryxflow.svg)](https://pypi.org/project/oryxflow/)
[![License: MIT](https://img.shields.io/pypi/l/oryxflow.svg)](https://github.com/oryxintel/oryxflow/blob/main/LICENSE)

Vetting oryxflow for a corporate package firewall? See [Security & supply chain](https://docs.oryxflow.dev/docs/supply-chain/).

For data scientists and data engineers, `oryxflow` is a python library which makes building complex data science workflows easy, fast and intuitive. It is **primarily designed for data scientists to build better models faster**. For data engineers, it can also be a lightweight alternative and help productionize data science models faster. Unlike other data pipeline/workflow solutions, `oryxflow` focuses on managing data science research workflows instead of managing production data pipelines. 

## Why use oryxflow?

Data science workflows typically look like this.

![Sample Data Workflow](docs/oryxflow-docs-graph.png?raw=true "Sample Data Workflow")

The workflow involves chaining together parameterized tasks which pass multiple inputs and outputs between each other. The output data gets stored in multiple dataframes, files and databases but you have to manually keep track of where everything is. And often you want to rerun tasks with different parameters without inadvertently rerunning long-running tasks. The workflows get complex and your code gets messy, difficult to audit and doesn't scale well.

`oryxflow` to the rescue! **With oryxflow you can easily chain together complex data flows and execute them. You can quickly load input and output data for each task.** It makes your workflow very clear and intuitive.

#### Read more at:  
[4 Reasons Why Your Machine Learning Code is Probably Bad](https://github.com/d6t/d6t-python/blob/master/blogs/reasons-why-bad-ml-code.rst)  
[How oryxflow is different from airflow/luigi](https://github.com/d6t/d6t-python/blob/master/blogs/datasci-dags-airflow-meetup.md)

![Badge](https://www.kdnuggets.com/images/tkb-1904-p.png "Badge")
![Badge](https://www.kdnuggets.com/images/tkb-1902-g.png "Badge")

## When to use oryxflow?

* Data science: you want to build better models faster. Your workflow is EDA, feature engineering, model training and evaluation. oryxflow works with ANY ML library including sklearn, pytorch, keras
* Data engineering: you want to build robust data pipelines using a lightweight yet powerful library. You workflow is load, filter, transform, join data in pandas, dask, pyspark, sql, athena

## What can oryxflow do for you?

* Data science  
	* Experiment management: easily manage workflows that compare different models to find the best one
	* Scalable workflows: build an efficient data workflow that support rapid prototyping and iterations
	* Cache data: easily save/load intermediary calculations to reduce model training time
	* Model deployment: oryxflow workflows are easier to deploy to production
* Data engineering  
	* Build a data workflow made up of tasks with dependencies and parameters
	* Visualize task dependencies and their execution status
	* Execute tasks including dependencies
	* Intelligently continue workflows after failed tasks
	* Intelligently rerun workflow after changing parameters, code or data
	* Quickly share and hand off output data to others


## Installation

Install with `pip install oryxflow`. To update, run `pip install oryxflow -U`.

If you are behind an enterprise firewall, you can also clone/download the repo and run `pip install .`

**Python3 only** You might need to call `pip3 install oryxflow` if you have not set python 3 as default.

To install latest DEV `pip install git+git://github.com/oryxintel/oryxflow.git` or upgrade `pip install git+git://github.com/oryxintel/oryxflow.git -U --no-deps`

## Claude Code plugin

Build oryxflow workflows faster with AI assistance. The [oryxflow Claude Code plugin](https://github.com/oryxintel/oryxflow-claude-plugin) adds a skill that auto-activates when you edit pipeline files (`tasks.py`, `flow.py`, `run.py`) plus slash commands to scaffold and manage projects:

* `/oryxflow:init-project` – scaffold a new oryxflow project from templates
* `/oryxflow:init-gitlfs` – set up Git LFS to version data outputs (see [Sharing data](#sharing-data))
* `/oryxflow:oryxflow` – manually invoke the skill (optional; it auto-activates on pipeline files)

Install in [Claude Code](https://claude.com/claude-code):

```
/plugin marketplace add oryxintel/oryxflow-claude-plugin
/plugin install oryxflow@oryxflow
```

See the [plugin repo](https://github.com/oryxintel/oryxflow-claude-plugin) for more details.

## Example: Model Comparison

Below is an introductory example that gets training data, trains two models and compares their performance.  

**[See the full ML workflow example here](http://tiny.cc/d6tflow-start-example)**  
**[Interactive mybinder jupyter notebook](http://tiny.cc/d6tflow-start-interactive)**

```python

import oryxflow
import sklearn.datasets, sklearn.ensemble, sklearn.linear_model
import pandas as pd


# get training data and save it
class GetData(oryxflow.tasks.TaskPqPandas):
    persists = ['x','y']

    def run(self):
        ds = sklearn.datasets.load_boston()
        df_trainX = pd.DataFrame(ds.data, columns=ds.feature_names)
        df_trainY = pd.DataFrame(ds.target, columns=['target'])
        self.save({'x': df_trainX, 'y': df_trainY}) # persist/cache training data


# train different models to compare
@oryxflow.requires(GetData)  # define dependency
class ModelTrain(oryxflow.tasks.TaskPickle):
    model = oryxflow.Parameter()  # parameter for model selection

    def run(self):
        df_trainX, df_trainY = self.inputLoad()  # quickly load input data

        if self.model=='ols':  # select model based on parameter
            model = sklearn.linear_model.LinearRegression()
        elif self.model=='gbm':
            model = sklearn.ensemble.GradientBoostingRegressor()

        # fit and save model with training score
        model.fit(df_trainX, df_trainY)
        self.save(model)  # persist/cache model
        self.saveMeta({'score': model.score(df_trainX, df_trainY)})  # save model score

# goal: compare performance of two models
# define workflow manager
flow = oryxflow.WorkflowMulti(ModelTrain, {'model1':{'model':'ols'}, 'model2':{'model':'gbm'}})
flow.reset_upstream(confirm=False) # DEMO ONLY: force re-run
flow.run()  # execute model training including all dependencies

'''
Scheduled 2 tasks
* 2 ran successfully
* 0 complete
* 0 failed
'''

scores = flow.outputLoadMeta()  # load model scores
print(scores)
# {'model1': {'score': 0.7406426641094095}, 'gbm': {'model2': 0.9761405838418584}}


```


## Example Library

* [Minimal example](https://github.com/oryxintel/oryxflow/blob/main/docs/example-minimal.py)
* [Multi-parameter example](https://github.com/oryxintel/oryxflow/blob/main/docs/example-flow-multi.py)
* [Rapid Prototyping for Quantitative Investing with oryxflow](https://github.com/d6tdev/d6tflow-binder-interactive/blob/master/example-trading.ipynb) 
* oryxflow with functions only: get the power of oryxflow with little change in code. **[Jupyter notebook example](https://github.com/oryxintel/oryxflow/blob/main/docs/example-functional.ipynb)**

## Documentation

Library usage and reference https://docs.oryxflow.dev/

## Getting started resources

[Transition to oryxflow from typical scripts](https://docs.oryxflow.dev/docs/transition/)

[5 Step Guide to Scalable Deep Learning Pipelines with oryxflow](https://htmlpreview.github.io/?https://github.com/d6t/d6t-python/blob/master/blogs/blog-20190813-d6tflow-pytorch.html)

[Data science project starter templates](https://github.com/d6t/d6tflow-template)

# Sharing data

By default data gets written to `data/` which is gitignored to avoid writing large files to source control.

To source control you can use git lfs to dvc.

## Git lfs

1. Install the LFS extension (once per machine)

```
  winget install GitHub.GitLFS   # or: choco install git-lfs
  git lfs install                 # hooks LFS into your git config
```

2. adjust .gitignore to track `data/` and `reports/render`

3. Tell LFS which files to track
```shell
git lfs track "data/**"
git lfs track "reports/render/**"
git lfs track "*.ipynb"
```

4. commit `.gitattributes` and `.gitignore`


## Pro version

Additional features:  
* Team sharing of workflows and data
* Integrations for datbase and cloud storage (SQL, S3)
* Integrations for distributed compute (dask, pyspark)
* Integrations for cloud execution (athena)
* Workflow deployment and scheduling

[Schedule demo](https://calendar.app.google/FkNWJE9u7QuowfH89)

## Accelerate Data Science

Check out other d6t libraries, including  
* import data: quickly ingest messy raw CSV and XLS files to pandas, SQL and more
* join data: quickly combine multiple datasets using fuzzy joins

https://github.com/d6t/d6t-python

## How To Contribute

Thank you for considering to contribute to the project. First, fork the code repository and then pick an issue that is open. Afterwards follow these steps
* Create a branch called \[issue_no\]\_yyyymmdd\_\[feature\]
* Implement the feature
* Write unit tests for the desired behaviour
* Create a pull request to merge branch with master

A similar workflow applies to bug-fixes as well. In the case of a fix, just change the feature name with the bug-fix name. And make sure the code passes already written unit tests.
