# oryxflow test suite

How the tests are organized, what they cover, and the order to run them.

## Test files

The suite is four `test_*.py` files (pytest auto-discovers these). All other files in
`tests/` are helpers or scratch, not collected by pytest:

| File | Collected? | Purpose |
|---|---|---|
| `test_main.py` | yes | Core unit tests: targets, tasks, save/load, formats, params, execute/preview, Excel, paths |
| `test_workflow.py` | yes | `Workflow` API, `FlowExport`/`FlowImport`, input/output load, output paths, meta, subflows |
| `test_workflowMulti.py` | yes | `WorkflowMulti` (multi-experiment) preview/run/outputLoad/outputPath |
| `test_workflowMulti2.py` | yes | `WorkflowMulti` with `params_generator_*` parameter grids |
| `for_import.py` | no | Module loaded by `FlowImport` tests in `test_workflow.py` |
| `tmp-*.py`, `publish.sh` | no | Scratch / manual snippets, ignored by pytest |

There is no `conftest.py` and no `pytest.ini`/`setup.cfg` config — pytest runs with defaults.

## How the tests work

- **Shared data directory.** Every file points oryxflow at `tests/data/` via
  `oryxflow.set_dir('data/')`. Task outputs are written there (parquet/cache/excel/etc.),
  and `tests/data/` is the working area for the whole suite. Run pytest from the repo root
  so the relative `data/` path resolves to `tests/data/`.
- **Tasks are defined inline.** Most tests declare small `oryxflow.tasks.Task*` subclasses
  (often nested inside the test) and assert on `complete()`, `run()`, `save()`/`load()`,
  `outputLoad()`, `output().path`, and `outputPath()`.
- **`test_main.py` isolation.** `TestMain` uses a `cleanup` fixture that wipes and recreates
  `tests/data/` around tests that need a clean slate, so those tests don't depend on leftover
  state. Tests not using the fixture rely on running their own tasks first.
- **Path assertions are OS-agnostic.** All oryxflow targets expose `.path` (and
  `outputPath()` returns) `pathlib.Path` objects. Tests compare against `Path("a/b/c")`
  rather than raw `"a/b/c"` strings so they pass on both POSIX and Windows. `FlowExport`
  code-generation tests embed `{Path(...)}` in their expected file text for the same reason.

## Dependencies

Runtime deps come from `requirements.txt`; test/dev deps from `requirements-dev.txt`:

```
pip install -r requirements.txt -r requirements-dev.txt
```

The tests additionally import:
- `scikit-learn` — `test_workflow.py` / `test_workflowMulti.py` use `load_diabetes`,
  `load_breast_cancer`, and `LogisticRegression`/`SVC`.
- `pyarrow` — parquet targets (`TaskPqPandas`).
- `openpyxl` — Excel tests in `test_main.py` (`TaskExcelPandas`).
- `jinja2` — `FlowExport` code generation in `test_workflow.py`.
- `tables` (PyTables) — `TaskH5Pandas` in `test_main.py::test_formats`.

**Optional:** `datatable` is exercised in `test_main.py::test_formats` but is *soft* — if it
is not installed (or fails to import), the test emits a `UserWarning: datatable failed` and
still passes. The warning is expected and not a failure.

## Running the tests

Run from the repository root (`oryxflow/`), not from inside `tests/`.

### Recommended order

Run incrementally, most foundational first, so a failure in the core layer surfaces before
the higher-level workflow tests:

```bash
# 1. Core: targets, tasks, save/load, formats, params, excel, paths
python -m pytest tests/test_main.py -q

# 2. Workflow API + export/import + output paths/meta
python -m pytest tests/test_workflow.py -q

# 3. WorkflowMulti (multi-experiment)
python -m pytest tests/test_workflowMulti.py -q

# 4. WorkflowMulti parameter-grid generators
python -m pytest tests/test_workflowMulti2.py -q
```

### Everything at once

```bash
python -m pytest tests/test_main.py tests/test_workflow.py \
    tests/test_workflowMulti.py tests/test_workflowMulti2.py -q
```

### Single test or class

```bash
python -m pytest tests/test_main.py::TestMain::test_excel_sheets -q
python -m pytest tests/test_workflow.py::TestWorkflowOutput -q
```

Expected result: all tests pass (one benign `datatable failed` warning if `datatable` is
not installed). The suite is repeatable — running it twice in a row gives the same result
because `tests/data/` state is cleaned between the relevant runs.
