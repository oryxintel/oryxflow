# Reports from notebooks

Turn a Jupyter notebook into a report that oryxflow keeps up to date. You write the notebook once as a template. oryxflow runs it with your task's parameters, saves the finished notebook and a clean HTML copy without the code, and reruns it only when something that feeds it has changed.

The code on this page is illustrative: it is not run by the docs test suite, because it needs a Jupyter kernel.

## Install

```bash
pip install "oryxflow[notebook]"
```

The report runs in the `python3` Jupyter kernel, so that kernel must be the environment where oryxflow and this extra are installed.

## 1. Write the template notebook

Make a notebook at the project root, for example `viz-region-summary.ipynb` (at the root, `import tasks` and the relative `data/` folder resolve the same way they do for `run.py`). It needs one cell tagged `parameters`. In classic Notebook, use View > Cell Toolbar > Tags. In JupyterLab, select the cell and open the property inspector (the gear icon in the right sidebar). List every name your notebook uses, with a default, and add `oryxflow_inputs = None`. A name that is not listed there is still passed in, but Jupyter and papermill warn about it:

```python
# cell tagged "parameters"
region = 'north'
oryxflow_inputs = None
```

Then load your data and build the report. The first cell below lets you work on the notebook by hand in Jupyter: when `oryxflow_inputs` is `None`, it asks the task for the same inputs oryxflow would pass.

```python
import oryxflow.report as rpt

if oryxflow_inputs is None:   # running by hand in Jupyter
    import tasks
    oryxflow_inputs = rpt.inputs_of(tasks.RegionReport(region=region))

sales = rpt.inputLoad(task='a')
rpt.table(sales, formats={'growth_pct': 'pct', 'revenue': 'int'}, caption=f'Sales, {region}')
```

`rpt.inputLoad(task='a')` loads the data of the upstream task you named `'a'` in `requires()`. With a single upstream task, `rpt.inputLoad()` is enough. If that task saves several outputs, pass `keys='train'` for one of them, or leave `keys` out to get a dict of all of them.

## 2. Declare the report task

```python
import oryxflow
from oryxflow.tasks import TaskPqPandas
from oryxflow.tasks.notebook import TaskNotebook

class LoadSales(TaskPqPandas):
    region = oryxflow.ChoiceParameter(choices=['north', 'south'])

    def run(self):
        ...   # build the sales table for self.region and self.save() it

@oryxflow.requires({'a': LoadSales})
class RegionReport(TaskNotebook):
    notebook = 'viz-region-summary.ipynb'
```

`@oryxflow.requires` copies `region` from `LoadSales` onto `RegionReport`. The upstream task must save to disk, as `TaskPqPandas` does; an in-memory `TaskCache` output is not reachable from the notebook.

The `notebook` path is relative to the folder you run the flow from (usually the project root), so run the flow from there. If the template cannot be found, the report counts as out of date and running it fails with an error naming the path.

Every task parameter, here `region`, is passed to the notebook under its own name. To pass extra values, define `notebook_parameters(self)` returning a dict. Names starting with `oryxflow_` are reserved.

## 3. Run it and open the report

```python
flow = oryxflow.Workflow(RegionReport, {'region': 'north'})
flow.run()
html = flow.outputLoad()    # path of the HTML report
```

Two files are saved next to your other task outputs: the executed notebook (`.ipynb`, with code, handy for debugging) and the report (`.html`, without code). `outputLoad()` returns the path of the `.html`. To keep the code in the report, set `html_include_code = True` on the task; the cell that oryxflow fills in with your parameters is then shown too, including the local data paths of your inputs. If the notebook fails, the partly executed copy is kept as `.failed.ipynb` so you can see where it stopped.

## When does the report rerun?

The report reruns when:

- its upstream **data** changes,
- a **parameter** changes,
- the task's **Python code** changes,
- the template's **cells** change (code or text, not just outputs).

It does not rerun when you simply run and save the template in Jupyter, because that only changes the saved outputs.

## Tables that match your Excel files

`rpt.table(df, formats=...)` shows a dataframe with the same `formats=` dict you use for [Excel files](targets.md#excel-files-people-will-open), so one dict styles both. Keys are column names or patterns like `'*_pct'`, and values are names such as `'pct'`, `'int'` and `'date'`. A default index is hidden. Useful options:

- `columns=[...]` picks and orders columns, `rename={...}` relabels them (key `formats` by the new names),
- `caption='...'` adds a title,
- `index=True` or `False` forces the index on or off.

To format a single number, for example inside a sentence, use `rpt.fmt(0.053, 'pct')`, which gives `5.3%`. To get a dataframe of ready-made strings for markdown or prose tables, use `rpt.format_frame(df, formats)`; it does not need `jinja2`.
